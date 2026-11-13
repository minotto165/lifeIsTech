"""MCP サーバーから起動されるジョブの実行本体。

MCP のツール呼び出しは即座に返したいので、実際のレッスン実行はこのスクリプトを
子プロセスとして起動する。経過は --log（標準出力をリダイレクトしたファイル）に、
最終結果は --result の JSON に書き出す。

    python jobrunner.py --kind lesson --lesson 14020410 --result /path/result.json

kind:
    lesson    ... 1 本のレッスンを最初から（または続きから）実行する
    checkwork ... 1 本の理解度チェックワークを実行する
    auto      ... 未完了レッスンを連続実行する
"""

import argparse
import json
import os
import sys
import time
import traceback

import config
from chrome_session import ChromeSession
from instance_guard import acquire as acquire_instance
from member_api import (
    AuthError,
    MemberApi,
    MemberApiError,
    find_course,
    iter_lessons,
    resolve_lesson,
)


def write_result(path, payload):
    """結果 JSON を一時ファイル経由で書き出す（途中で読まれても壊れないように）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def build_parser(argv=None):
    parser = argparse.ArgumentParser(description="MCP 用のジョブ実行")
    parser.add_argument("--kind", required=True, choices=("lesson", "checkwork", "auto"))
    parser.add_argument("--result", required=True, help="結果 JSON の出力先")
    parser.add_argument("--lesson", type=int, help="レッスン ID")
    parser.add_argument("--chapter-id", type=int, help="章 ID（分かっていれば探索を省ける）")
    parser.add_argument("--from-scratch", action="store_true", help="完了済みでも最初から")
    parser.add_argument("--checkwork", dest="checkwork", action="store_true", default=None)
    parser.add_argument("--no-checkwork", dest="checkwork", action="store_false")
    parser.add_argument("--scope", default=config.AUTO_SCOPE, choices=("chapter", "course"))
    parser.add_argument("--chapter", default=config.AUTO_CHAPTER_LABEL)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args(argv)


def run(args):
    # 遅延 import（Playwright の起動を必要なときだけに寄せる）。
    import auto_runner

    if not acquire_instance():
        return {
            "ok": False,
            "error": "別の自動化プロセスが既に実行中です（同じ Chrome を同時に操作すると壊れます）。",
        }

    if args.checkwork is not None:
        config.AUTO_RUN_CHECKWORK = args.checkwork
    if args.from_scratch:
        # 明示的に選ばれたレッスンなので、完了済みでも最初からやり直せるようにする。
        config.AUTO_RESTART_PARTIAL = True

    session = ChromeSession()
    try:
        page = session.connect()
        context = session.browser.contexts[0]
        api = MemberApi(context)
        course = find_course(api.textbook_courses(), config.AUTO_COURSE_NAME)

        if args.kind in ("lesson", "checkwork"):
            target = resolve_lesson(api, course, args.lesson, args.chapter_id)
            if target is None:
                return {"ok": False, "error": f"レッスン {args.lesson} が見つかりません。"}
            if args.kind == "checkwork":
                config.AUTO_RUN_CHECKWORK = True
                started = time.monotonic()
                ok = auto_runner.run_checkwork(api, context, page, target)
                return {
                    "ok": ok,
                    "seconds": round(time.monotonic() - started, 1),
                    "results": [
                        {
                            "lesson_id": target.lesson_id,
                            "name": target.name,
                            "result": auto_runner.RESULT_DONE if ok else auto_runner.RESULT_FAILED,
                        }
                    ],
                }
            started = time.monotonic()
            result = auto_runner.run_one(api, context, page, target, 1, 1)
            return {
                "ok": result == auto_runner.RESULT_DONE,
                "seconds": round(time.monotonic() - started, 1),
                "results": [
                    {"lesson_id": target.lesson_id, "name": target.name, "result": result}
                ],
            }

        targets = iter_lessons(
            api, course, scope=args.scope, chapter_label=args.chapter
        )
        if args.limit:
            targets = targets[: args.limit]
        if not targets:
            return {"ok": True, "results": [], "note": "未完了のレッスンはありません。"}
        results = auto_runner.run_targets(api, context, page, targets)
        return {
            "ok": not results["failed"],
            "results": [
                {"lesson_id": t.lesson_id, "name": t.name, "result": "done"}
                for t in results["done"]
            ]
            + [
                {"lesson_id": t.lesson_id, "name": t.name, "result": "skipped"}
                for t in results["skipped"]
            ]
            + [
                {"lesson_id": t.lesson_id, "name": t.name, "result": "failed"}
                for t in results["failed"]
            ],
        }
    finally:
        session.close()


def main(argv=None):
    args = build_parser(argv)
    started = time.time()
    payload = {"kind": args.kind, "pid": os.getpid()}
    exit_code = 0
    try:
        payload.update(run(args))
    except AuthError as e:
        payload.update({"ok": False, "error": f"ログインが必要です: {e}"})
        exit_code = 2
    except MemberApiError as e:
        payload.update({"ok": False, "error": f"API エラー: {e}"})
        exit_code = 1
    except KeyboardInterrupt:
        payload.update({"ok": False, "cancelled": True, "error": "中止しました。"})
        exit_code = 130
    except Exception as e:
        # 想定外の失敗はスタックトレースも残す（MCP 側はログ末尾で確認できる）。
        traceback.print_exc()
        payload.update({"ok": False, "error": f"{type(e).__name__}: {e}"})
        exit_code = 1
    payload["seconds"] = round(time.time() - started, 1)
    payload["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_result(args.result, payload)
    print(f"[jobrunner] 終了: ok={payload.get('ok')} ({payload['seconds']} 秒)", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
