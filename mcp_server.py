"""Life is Tech! レッスン自動化の MCP サーバー（stdio）。

Claude Desktop / Codex などの MCP クライアントから、レッスンの一覧取得と
実行を呼べるようにする。

    uv run python mcp_server.py        # MCP クライアントから stdio で起動される

設計:
- stdout は JSON-RPC 専用。ログ（ui.py / print）は stderr に逃がす。
- 実行は子プロセス（jobrunner.py）に任せ、ツールは即座に job_id を返す。
- 同時に動かせるジョブは 1 つ（同じ Chrome を同時に操作すると壊れるため）。
"""

import json
import os
import secrets
import signal
import subprocess
import sys
import time

# ui.py が rich の出力先を決める前に設定する必要がある（import より先）。
os.environ.setdefault("LIFEISTECH_LOG_STREAM", "stderr")

from mcp.server import MCPServer  # noqa: E402  (環境変数を先に立てるため)

import browser  # noqa: E402
import config  # noqa: E402
from member_api import (  # noqa: E402
    STATUS_CLEARED,
    LessonTarget,
    MemberApi,
    chapter_details,
    find_course,
    find_pending_checkworks,
)
from playwright.sync_api import sync_playwright  # noqa: E402

mcp = MCPServer("lifeistech")

JOB_DIR = config.MCP_JOB_DIR
LOG_FILE = os.path.join(config.PROJECT_DIR, "mcp_server.log")


def _log_to_file(message):
    """デバッグ用の記録（MCP の stdout を汚さないためファイルへ）。"""
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


# --- Chrome / API への薄いアクセス ------------------------------------


def _member_token():
    """起動中の Chrome からセッション cookie を読む（ページ遷移はしない）。"""
    if not browser.is_debug_port_open():
        raise RuntimeError(
            "Chrome が起動していないか、CDP に接続できません"
            f"（ポート {config.CHROME_DEBUG_PORT}）。"
            "先に main.py を実行するか、ツール chrome_status で状態を確認してください。"
        )
    token = None
    playwright = sync_playwright().start()
    try:
        chrome = playwright.chromium.connect_over_cdp(config.CDP_ENDPOINT)
        if chrome.contexts:
            for cookie in chrome.contexts[0].cookies():
                if cookie.get("name") == config.SESSION_COOKIE_NAME:
                    token = cookie.get("value")
                    break
    finally:
        # Chrome 自体は閉じない（ユーザーのブラウザを巻き込まない）。
        playwright.stop()
    if not token:
        raise RuntimeError(
            "ログイン情報が見つかりませんでした。ブラウザで lifeistech-lesson.jp にログインしてください。"
        )
    return token


def _api():
    return MemberApi(token=_member_token())


def _course(name=None):
    api = _api()
    course = find_course(api.textbook_courses(), name or config.AUTO_COURSE_NAME)
    return api, course


def _target_dict(target):
    return {
        "lesson_id": target.lesson_id,
        "chapter_id": target.chapter_id,
        "chapter_label": target.chapter_label,
        "label": target.label,
        "title": target.title,
        "status": target.status,
        "name": target.name,
        "url": target.page_url(),
    }


# --- ジョブ管理 --------------------------------------------------------


def _job_file(job_id, suffix):
    return os.path.join(JOB_DIR, f"{job_id}.{suffix}")


def _read_job(job_id):
    path = _job_file(job_id, "json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _job_result(job_id):
    path = _job_file(job_id, "result.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _job_status(job):
    """実行中・完了・失敗・中止を判定する。"""
    result = _job_result(job["job_id"])
    if result is not None:
        if result.get("ok"):
            return "done"
        if result.get("cancelled"):
            return "cancelled"
        return "failed"
    if _job_alive(job):
        return "running"
    return "cancelled" if job.get("stop_requested") else "failed"


# このサーバープロセスが起動したジョブの Popen。poll() で回収しないと
# 子プロセスがゾンビになり、is_process_alive() が永久に True を返す。
_PROCESSES = {}


def _reap(job_id):
    """終了した子プロセスを回収する（ゾンビ対策）。"""
    process = _PROCESSES.get(job_id)
    if process is None:
        return None
    if process.poll() is not None:
        _PROCESSES.pop(job_id, None)
        return process
    return None


def _job_alive(job):
    job_id = job["job_id"]
    _reap(job_id)
    process = _PROCESSES.get(job_id)
    if process is not None:
        return process.poll() is None
    # サーバー再起動前のジョブなど、Popen を持っていない場合のフォールバック。
    if not browser.is_process_alive(job["pid"]):
        return False
    # ゾンビ（defunct）は生きていない扱いにする。
    try:
        with open(f"/proc/{job['pid']}/stat", encoding="utf-8") as f:
            return f.read().split()[2] != "Z"
    except OSError:
        # macOS には /proc が無いので ps で状態を見る。
        try:
            output = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(job["pid"])],
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return False
        return bool(output) and not output.startswith("Z")


def _running_job():
    for job in _all_jobs():
        if _job_status(job) == "running":
            return job
    return None


def _all_jobs():
    if not os.path.isdir(JOB_DIR):
        return []
    jobs = []
    for name in os.listdir(JOB_DIR):
        if not name.endswith(".json") or name.endswith(".result.json"):
            continue
        job = _read_job(name[: -len(".json")])
        if job:
            jobs.append(job)
    jobs.sort(key=lambda j: j.get("started_at", ""), reverse=True)
    return jobs


def _tail(path, lines):
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            chunks = f.readlines()
    except OSError:
        return ""
    text = "".join(chunks[-lines:])
    return text[-config.MCP_LOG_TAIL_MAX_CHARS :]


def _start_job(kind, params, args):
    running = _running_job()
    if running is not None:
        raise RuntimeError(
            f"既に実行中のジョブがあります（{running['job_id']} / {running['kind']}）。"
            "job_status で確認するか、job_stop で停止してください。"
        )
    if not browser.is_debug_port_open():
        raise RuntimeError(
            f"Chrome の CDP（ポート {config.CHROME_DEBUG_PORT}）に接続できません。"
            "先に main.py を実行してください。"
        )

    os.makedirs(JOB_DIR, exist_ok=True)
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    log_path = _job_file(job_id, "log")
    result_path = _job_file(job_id, "result.json")
    cmd = [
        sys.executable,
        os.path.join(config.PROJECT_DIR, "jobrunner.py"),
        "--kind",
        kind,
        "--result",
        result_path,
        *args,
    ]
    with open(log_path, "w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            cmd,
            cwd=config.PROJECT_DIR,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # job_stop でプロセスグループごと止められるように
        )
    job = {
        "job_id": job_id,
        "kind": kind,
        "params": params,
        "pid": process.pid,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "log": log_path,
        "result": result_path,
    }
    _PROCESSES[job_id] = process
    with open(_job_file(job_id, "json"), "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)
    _log_to_file(f"start {job_id} kind={kind} pid={process.pid} args={args}")
    return job


def _job_summary(job):
    status = _job_status(job)
    summary = {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "status": status,
        "pid": job["pid"],
        "started_at": job.get("started_at"),
        "params": job.get("params"),
    }
    result = _job_result(job["job_id"])
    if result is not None:
        summary["ok"] = result.get("ok")
        summary["seconds"] = result.get("seconds")
        summary["results"] = result.get("results", [])
        summary["error"] = result.get("error")
        summary["note"] = result.get("note")
        summary["finished_at"] = result.get("finished_at")
    return summary


# --- ツール ------------------------------------------------------------


@mcp.tool()
def chrome_status():
    """自動化用 Chrome の接続状態とログイン状態、実行中ジョブを返す。"""
    port_open = browser.is_debug_port_open()
    status = {
        "cdp_port": config.CHROME_DEBUG_PORT,
        "cdp_open": port_open,
        "profile_dir": config.CHROME_PROFILE_DIR,
        "headless": config.HEADLESS,
        "logged_in": False,
    }
    if port_open:
        try:
            token = _member_token()
            status["logged_in"] = True
            # 値は JWT を含むため、ログイン ID の部分だけ返す。
            status["login_id"] = token.split("_", 1)[0]
        except Exception as e:  # ログイン切れは状態として返す（エラーにしない）
            status["login_error"] = str(e)
    running = _running_job()
    status["running_job"] = running["job_id"] if running else None
    return status


@mcp.tool()
def list_courses():
    """受講中コースの一覧（コース名・章の数）を返す。"""
    api = _api()
    courses = api.textbook_courses()
    return {
        "courses": [
            {
                "name": course.get("name"),
                "course_id": course.get("id"),
                "textbook_id": (course.get("textbook") or {}).get("id"),
                "chapters": len(course.get("chapters") or []),
            }
            for course in courses
        ]
    }


@mcp.tool()
def list_lessons(course: str = "", chapter: str = "", unfinished_only: bool = True):
    """レッスンの一覧を返す。

    Args:
        course: コース名（空なら config.AUTO_COURSE_NAME）。
        chapter: 章の絞り込み（例 "第4章"、空なら全章）。
        unfinished_only: True なら完了済みを除く。
    """
    api, course_info = _course(course or None)
    rows = []
    for detail in chapter_details(api, course_info, chapter or None):
        for lesson in detail.get("lessons") or []:
            target = LessonTarget(
                textbook_id=detail.get("textbook_id"),
                chapter_id=detail.get("id"),
                chapter_label=detail.get("label") or "",
                chapter_title=detail.get("title") or "",
                lesson_id=lesson.get("id"),
                label=lesson.get("label") or "",
                title=lesson.get("title") or "",
                status=(lesson.get("player_lesson") or {}).get("status") or "unstarted",
            )
            if unfinished_only and target.status == STATUS_CLEARED:
                continue
            rows.append(_target_dict(target))
    return {"count": len(rows), "lessons": rows}


@mcp.tool()
def list_checkworks(course: str = "", chapter: str = "", min_score: int = 0):
    """未実施（または基準スコア未満）の理解度チェックワークを一覧する。

    Args:
        course: コース名（空なら config.AUTO_COURSE_NAME）。
        chapter: 章の絞り込み（例 "第4章"、空なら全章）。
        min_score: 完了基準。0 なら「1 回でも実施済み」は除外（未実施だけ）。100 なら満点まで対象。
    """
    api, course_info = _course(course or None)
    entries = find_pending_checkworks(
        api,
        course_info,
        chapter_label=chapter or None,
        min_score=max(0, int(min_score or 0)),
    )
    return {
        "count": len(entries),
        "checkworks": [
            {
                "lesson_id": entry.target.lesson_id,
                "chapter_id": entry.target.chapter_id,
                "chapter_label": entry.target.chapter_label,
                "label": entry.target.label,
                "title": entry.target.title,
                "lesson_status": entry.target.status,
                "checkwork_id": entry.checkwork_id,
                "attempts": entry.status.attempts,
                "latest_score": entry.status.latest_score,
                "best_score": entry.status.best_score,
                "name": entry.name,
                "url": entry.target.page_url(),
            }
            for entry in entries
        ],
    }


@mcp.tool()
def start_checkworks(
    course: str = "", chapter: str = "", limit: int = 0, min_score: int = 0
):
    """未実施の理解度チェックワークをまとめて実行する（即座に job_id を返す）。

    Args:
        course: コース名（空なら config.AUTO_COURSE_NAME）。
        chapter: 章の絞り込み（例 "第4章"、空なら全章）。
        limit: 実行する最大本数（0 なら無制限）。
        min_score: 完了基準。0 なら未実施だけ、100 なら満点までやり直す。
    """
    args = ["--limit", str(max(0, int(limit or 0)))]
    if chapter:
        args += ["--chapter", chapter]
    if course:
        args += ["--course", course]
    args += ["--checkwork-min-score", str(max(0, int(min_score or 0)))]
    job = _start_job(
        "checkworks",
        {
            "course": course or None,
            "chapter": chapter or None,
            "limit": limit,
            "min_score": min_score,
        },
        args,
    )
    return {"job_id": job["job_id"], "status": "running"}


@mcp.tool()
def start_lesson(
    lesson_id: int,
    chapter_id: int = 0,
    from_scratch: bool = False,
    with_checkwork: bool = False,
):
    """1 本のレッスンを自動で最後まで進める（即座に job_id を返す）。

    Args:
        lesson_id: レッスン ID（list_lessons の lesson_id）。
        chapter_id: 章 ID。分かっていれば渡すと探索が速い。
        from_scratch: True なら完了済みでも最初からやり直す。
        with_checkwork: True なら完了後に理解度チェックワークも解く。
    """
    args = ["--lesson", str(int(lesson_id))]
    if chapter_id:
        args += ["--chapter-id", str(int(chapter_id))]
    if from_scratch:
        args += ["--from-scratch"]
    args += ["--checkwork" if with_checkwork else "--no-checkwork"]
    job = _start_job(
        "lesson",
        {
            "lesson_id": lesson_id,
            "chapter_id": chapter_id or None,
            "from_scratch": from_scratch,
            "with_checkwork": with_checkwork,
        },
        args,
    )
    return {
        "job_id": job["job_id"],
        "status": "running",
        "hint": "job_status で進捗を確認してください。",
    }


@mcp.tool()
def start_checkwork(lesson_id: int, chapter_id: int = 0):
    """1 本の理解度チェックワークを自動で解く（即座に job_id を返す）。

    Args:
        lesson_id: 対応するレッスンの ID。
        chapter_id: 章 ID（省略可）。
    """
    args = ["--lesson", str(int(lesson_id))]
    if chapter_id:
        args += ["--chapter-id", str(int(chapter_id))]
    job = _start_job(
        "checkwork",
        {"lesson_id": lesson_id, "chapter_id": chapter_id or None},
        args,
    )
    return {"job_id": job["job_id"], "status": "running"}


@mcp.tool()
def start_auto(
    scope: str = "chapter",
    chapter: str = "",
    limit: int = 0,
    with_checkwork: bool = True,
):
    """未完了レッスンを自動選択して連続実行する（即座に job_id を返す）。

    Args:
        scope: "chapter" は最初の未完了章だけ、"course" は全章。
        chapter: 章の絞り込み（例 "第4章"、空なら設定どおり）。
        limit: 実行する最大本数（0 なら無制限）。
        with_checkwork: True なら各レッスンの後に理解度チェックワークも解く。
    """
    if scope not in ("chapter", "course"):
        raise RuntimeError('scope は "chapter" か "course" を指定してください。')
    args = ["--scope", scope]
    if chapter:
        args += ["--chapter", chapter]
    if limit:
        args += ["--limit", str(int(limit))]
    args += ["--checkwork" if with_checkwork else "--no-checkwork"]
    job = _start_job(
        "auto",
        {
            "scope": scope,
            "chapter": chapter or None,
            "limit": limit,
            "with_checkwork": with_checkwork,
        },
        args,
    )
    return {"job_id": job["job_id"], "status": "running"}


@mcp.tool()
def job_status(job_id: str, tail_lines: int = 40):
    """ジョブの状態・結果と、ログの末尾を返す。"""
    job = _read_job(job_id)
    if job is None:
        raise RuntimeError(
            f"ジョブ {job_id} が見つかりません。list_jobs で確認してください。"
        )
    summary = _job_summary(job)
    lines = max(1, min(int(tail_lines or config.MCP_LOG_TAIL_LINES), 200))
    summary["log_tail"] = _tail(job["log"], lines)
    return summary


@mcp.tool()
def job_stop(job_id: str):
    """実行中のジョブを停止する（プロセスグループごと止める）。"""
    job = _read_job(job_id)
    if job is None:
        raise RuntimeError(f"ジョブ {job_id} が見つかりません。")
    status = _job_status(job)
    if status != "running":
        return {"job_id": job_id, "status": status, "stopped": False}
    job["stop_requested"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(_job_file(job_id, "json"), "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)
    try:
        os.killpg(os.getpgid(job["pid"]), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(job["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not _job_alive(job):
            break
        time.sleep(0.1)
    else:
        try:
            os.killpg(os.getpgid(job["pid"]), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    _log_to_file(f"stop {job_id}")
    return {"job_id": job_id, "status": _job_status(job), "stopped": True}


@mcp.tool()
def list_jobs(limit: int = 10):
    """最近のジョブを新しい順に返す。"""
    limit = max(1, min(int(limit or 10), 50))
    return {"jobs": [_job_summary(job) for job in _all_jobs()[:limit]]}


def main():
    # MCP クライアントは stdin/stdout を JSON-RPC に使う。ui.py のログは stderr へ。
    _log_to_file(f"mcp_server start (python {sys.version.split()[0]})")
    mcp.run()


if __name__ == "__main__":
    main()
