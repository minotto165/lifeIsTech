"""Life is Tech! 受講者向け API のクライアントと、未完了レッスンの選択ロジック。

認証はブラウザの cookie `mozermovie`（値は `<login_id>_<JWT>`）を使い、
`Authorization: Bearer <値>` で API を GET する。
"""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import config
import ui

MEMBER_ORIGIN = "https://member.lifeistech-lesson.jp"
# セッション cookie の名前は config 側に一本化してある。
COOKIE_NAME = config.SESSION_COOKIE_NAME
STATUS_CLEARED = "cleared"
STATUS_LOCKED = "locked"


class MemberApiError(RuntimeError):
    """API 呼び出しに失敗した（再実行で復帰できる可能性がある）。"""


class AuthError(MemberApiError):
    """ログイン情報が無い、または期限切れ。"""


@dataclass
class LessonTarget:
    textbook_id: int
    chapter_id: int
    chapter_label: str
    chapter_title: str
    lesson_id: int
    label: str
    title: str
    status: str

    @property
    def name(self):
        head = f"{self.chapter_label} {self.label or self.lesson_id}".strip()
        title = f"「{self.title}」" if self.title else ""
        return f"{head}{title}"

    def page_url(self):
        return (
            f"{MEMBER_ORIGIN}/textbooks/{self.textbook_id}"
            f"/chapters/{self.chapter_id}/lessons/{self.lesson_id}"
        )


@dataclass
class CheckworkStatus:
    """理解度チェックワークの実施状況（API の採点履歴から作る）。"""

    attempts: int = 0
    latest_score: int | None = None
    best_score: int | None = None
    submitted_at: str | None = None

    def is_done(self, min_score=0):
        """完了とみなすか。min_score=0 なら「1 回でも実施したか」だけを見る。"""
        if self.attempts == 0:
            return False
        if min_score <= 0:
            return True
        # 一度でも満点に届いていれば完了扱い（最新が下がってもやり直さない）。
        return (self.best_score or 0) >= min_score

    def describe(self):
        if self.attempts == 0:
            return "未実施"
        parts = [f"{self.attempts} 回実施"]
        if self.latest_score is not None:
            parts.append(f"最新 {self.latest_score}%")
        if self.best_score is not None:
            parts.append(f"最高 {self.best_score}%")
        return " / ".join(parts)


@dataclass
class CheckworkEntry:
    """未実施のチェックワーク 1 件（レッスンとワークの対応）。"""

    target: LessonTarget
    checkwork_id: int
    status: CheckworkStatus

    @property
    def name(self):
        return self.target.name


class MemberApi:
    def __init__(self, context=None, token=None):
        self._token = token or read_token(context)
        self._urlopen = urllib.request.urlopen

    # --- HTTP -------------------------------------------------------
    def _request(self, path):
        url = f"{config.MEMBER_API_BASE}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        last_error = None
        for attempt in range(config.MEMBER_API_MAX_RETRIES + 1):
            try:
                request = urllib.request.Request(url, headers=headers)
                with self._urlopen(request, timeout=config.MEMBER_API_TIMEOUT_SEC) as res:
                    body = res.read()
                return json.loads(body) if body else None
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise AuthError(
                        f"ログインの有効期限が切れています（HTTP {e.code}）。"
                        "Chrome で LifeisTech! にログインし直してください。"
                    ) from e
                if e.code == 404:
                    return None
                if e.code < 500:
                    raise MemberApiError(f"API が HTTP {e.code} を返しました: {path}") from e
                last_error = e
            except Exception as e:
                last_error = e
            if attempt < config.MEMBER_API_MAX_RETRIES:
                time.sleep(0.5 * (2**attempt))
        raise MemberApiError(f"API に接続できませんでした: {path} ({last_error})")

    # --- エンドポイント ----------------------------------------------
    def textbook_courses(self):
        data = self._request("players/textbooks/courses") or {}
        return data.get("courses") or []

    def course_names(self):
        return self._request("players/course_names") or []

    def chapter(self, chapter_id):
        return self._request(f"players/textbooks/chapters/{chapter_id}")

    def lesson(self, chapter_id, lesson_id):
        return self._request(
            f"players/textbooks/chapters/{chapter_id}/lessons/{lesson_id}"
        )

    def checkwork_scores(self, chapter_id, lesson_id):
        """理解度チェックワークの採点履歴を新しい順で返す（無ければ空リスト）。

        レッスンページが表示している「実施回数 / 最新3回の結果 / 満点回数」と
        同じデータ。未実施なら空リストになる。
        """
        data = self._request(
            f"players/textbooks/chapters/{int(chapter_id)}"
            f"/lessons/{int(lesson_id)}/player_checkwork_scores"
        )
        return data or []

    def checkwork_status(self, chapter_id, lesson_id):
        """採点履歴を集計して CheckworkStatus を返す。"""
        return summarize_checkwork(self.checkwork_scores(chapter_id, lesson_id))


def read_token(context):
    """ブラウザの cookie から API 用トークンを取り出す。"""
    try:
        cookies = context.cookies(MEMBER_ORIGIN) if context is not None else []
    except Exception as e:
        # 取得自体に失敗した場合と「cookie が無い」場合を区別できるようにする。
        print(f"cookie の取得に失敗しました: {e}", flush=True)
        cookies = []
    for cookie in cookies:
        if cookie.get("name") == COOKIE_NAME and cookie.get("value"):
            return cookie["value"]
    raise AuthError(
        "LifeisTech! のログイン cookie が見つかりません。"
        "Chrome でログインしてから実行してください。"
    )


def find_course(courses, name):
    """コース名から対象コースを決める。"""
    if not courses:
        raise MemberApiError("受講中のコースが見つかりませんでした。")
    name = (name or "").strip()
    if not name:
        return courses[0]
    for course in courses:
        if (course.get("name") or "").strip() == name:
            return course
    # 部分一致で複数に当たるなら誤ったコースを選ぶほうが危険なので、一意なときだけ許す。
    partial = [c for c in courses if name in (c.get("name") or "")]
    if len(partial) == 1:
        return partial[0]
    available = " / ".join(course.get("name") or "?" for course in courses)
    raise MemberApiError(
        f"コース「{name}」が見つかりません。利用できるコース: {available}"
    )


def to_target(chapter, lesson):
    status = (lesson.get("player_lesson") or {}).get("status") or "unstarted"
    return LessonTarget(
        textbook_id=chapter.get("textbook_id"),
        chapter_id=chapter.get("id"),
        chapter_label=chapter.get("label") or "",
        chapter_title=chapter.get("title") or "",
        lesson_id=lesson.get("id"),
        label=lesson.get("label") or "",
        title=lesson.get("title") or "",
        status=status,
    )


def chapter_details(api, course, chapter_label=None):
    """章の詳細を sort_no 順に返す（コース側の情報をマージ済み）。"""
    chapters = sorted(
        course.get("chapters") or [], key=lambda c: c.get("sort_no") or 0
    )
    if chapter_label:
        chapters = [
            c
            for c in chapters
            if chapter_label in (c.get("label") or "")
            or chapter_label in (c.get("title") or "")
        ]
        if not chapters:
            raise MemberApiError(f"章「{chapter_label}」が見つかりません。")

    textbook_id = (course.get("textbook") or {}).get("id")
    for chapter in chapters:
        detail = api.chapter(chapter.get("id"))
        if detail is None:
            # 404 を「未完了なし」として扱うと、黙ってコースを飛ばしてしまう。
            raise MemberApiError(
                f"章の詳細を取得できませんでした（chapter {chapter.get('id')}）。"
                "ページを再読み込みしてから再実行してください。"
            )
        detail = {**chapter, **detail}
        if not detail.get("textbook_id"):
            detail["textbook_id"] = textbook_id
        if not detail.get("textbook_id"):
            raise MemberApiError(
                f"章 {chapter.get('id')} の textbook_id が分からず、レッスンページの URL を組み立てられません。"
            )
        yield detail


def iter_lessons(api, course, scope="chapter", chapter_label=None):
    """未完了レッスンを順番に返す。

    scope="chapter": 最初の未完了レッスンを含む章だけを対象にする。
    scope="course" : コース内の全章を順に見る。
    """
    targets = []
    for detail in chapter_details(api, course, chapter_label):
        pending = []
        for lesson in detail.get("lessons") or []:
            target = to_target(detail, lesson)
            if target.status == STATUS_CLEARED:
                continue
            if target.status == STATUS_LOCKED:
                ui.log(f"ロック中のためスキップします: {target.name}")
                continue
            pending.append(target)
        if not pending:
            continue
        targets.extend(pending)
        if scope == "chapter":
            break
    return targets


def resolve_lesson(api, course, lesson_id, chapter_id=None):
    """レッスン ID から LessonTarget を引く（完了済み・ロック中も返す）。

    chapter_id を渡すとその章だけを見る。見つからなければ None。
    MCP サーバーの start_lesson / start_checkwork から使う。
    """
    lesson_id = int(lesson_id)
    if chapter_id:
        detail = api.chapter(int(chapter_id))
        if detail is None:
            return None
        course_chapters = {
            c.get("id"): c for c in (course.get("chapters") or [])
        }
        detail = {**course_chapters.get(int(chapter_id), {}), **detail}
        if not detail.get("textbook_id"):
            detail["textbook_id"] = (course.get("textbook") or {}).get("id")
        for lesson in detail.get("lessons") or []:
            if int(lesson.get("id") or 0) == lesson_id:
                return to_target(detail, lesson)
        return None
    for target in list_all_lessons(api, course):
        if target.lesson_id == lesson_id:
            return target
    return None


def list_all_lessons(api, course, chapter_label=None):
    """コース内の全レッスンを章順・レッスン順で返す（完了済みも含む）。

    CLI でレッスンを選ぶときに使う。ロック中も一覧には出すが、実行はできない。
    """
    targets = []
    for detail in chapter_details(api, course, chapter_label):
        for lesson in detail.get("lessons") or []:
            targets.append(to_target(detail, lesson))
    return targets


# --- 理解度チェックワークの進捗 ---------------------------------------


def _score_of(entry):
    try:
        return int(entry.get("score"))
    except (AttributeError, TypeError, ValueError):
        return None


def summarize_checkwork(scores):
    """採点履歴（新しい順とは限らない）から実施回数とスコアを集計する。"""
    ordered = sorted(
        scores or [], key=lambda s: (s.get("submitted_at") or ""), reverse=True
    )
    values = [value for value in (_score_of(s) for s in ordered) if value is not None]
    return CheckworkStatus(
        attempts=len(ordered),
        latest_score=values[0] if values else None,
        best_score=max(values) if values else None,
        submitted_at=ordered[0].get("submitted_at") if ordered else None,
    )


def checkwork_of(api, target, detail=None):
    """このレッスンに対応する理解度チェックワーク（無ければ None）。"""
    if detail is None:
        detail = api.lesson(target.chapter_id, target.lesson_id) or {}
    for key in ("variant_checkwork", "checkwork"):
        checkwork = detail.get(key) or {}
        if checkwork.get("id"):
            return checkwork
    return None


def find_pending_checkworks(api, course, chapter_label=None, min_score=None, verbose=True):
    """未実施（または min_score 未満）のチェックワークを章順に返す。

    完了済みレッスンも対象に含める。スライドだけ終わらせてチェックワークを
    やっていないレッスンを拾うのが目的なので、iter_lessons とは別に全レッスンを見る。
    """
    if min_score is None:
        min_score = config.CHECKWORK_MIN_SCORE
    entries = []
    seen = 0
    for detail in chapter_details(api, course, chapter_label):
        for lesson in detail.get("lessons") or []:
            target = to_target(detail, lesson)
            if target.status == STATUS_LOCKED:
                continue
            seen += 1
            if verbose and seen % 10 == 0:
                ui.log(f"チェックワークの状況を確認しています…（{seen} レッスン目）")
            # 採点履歴が取れればワークの有無も分かるので、先にこちらを引く。
            status = summarize_checkwork(
                api.checkwork_scores(target.chapter_id, target.lesson_id)
            )
            if status.is_done(min_score):
                continue
            checkwork = checkwork_of(api, target)
            if not checkwork:
                # チェックワークが無いレッスンは対象外（失敗にしない）。
                continue
            entries.append(
                CheckworkEntry(
                    target=target,
                    checkwork_id=checkwork.get("id"),
                    status=status,
                )
            )
    return entries
