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
