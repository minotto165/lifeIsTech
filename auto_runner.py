"""未完了レッスンを自動選択して連続実行するランナー。

Chrome に接続し、受講者 API からコース・章・レッスンの進捗を取得して
「最初の未完了レッスン」から順に、レッスン画面を開いて開始ボタンを押し、
既存の高速自動進行（lifeistech_auto.run_lesson）で完了まで進める。
完了は API の status が cleared になったことで確認する。

使い方:
    uv run python auto_runner.py             # config.py の設定で実行
    uv run python auto_runner.py --dry-run   # 対象一覧だけ表示
    uv run python auto_runner.py --max 1     # 1 本だけ
"""

import argparse
import pathlib
import sys
import time

import config
import dom
import poll
import ui
from chrome_session import ChromeSession
from human_utils import human_click
from instance_guard import acquire as acquire_instance
from lifeistech_auto import log, run_lesson
from member_api import (
    STATUS_CLEARED,
    STATUS_LOCKED,
    AuthError,
    MemberApi,
    MemberApiError,
    find_course,
    iter_lessons,
    list_all_lessons,
)
from work_automation import already_graded, run_work

# 開始系ボタンの優先順。一覧・セレクタ・選択処理はこの 1 か所から導出する。
LESSON_BUTTON_TUPLE = (
    config.SEL_LESSON_START_BTN,
    config.SEL_LESSON_RESUME_BTN,
    config.SEL_LESSON_RESTART_BTN,
)
LESSON_BUTTONS = ", ".join(LESSON_BUTTON_TUPLE)

# run_one の結果。完了・スキップ・失敗を区別しないと、未完了を完了として
# 数えてしまうため、bool ではなくこの 3 値で返す。
RESULT_DONE = "done"
RESULT_SKIPPED = "skipped"
RESULT_FAILED = "failed"


def prune_screenshots(directory, keep):
    """古いスクリーンショットを消して、置き場所が膨らみ続けないようにする。"""
    try:
        shots = sorted(
            directory.glob("*.png"), key=lambda item: item.stat().st_mtime, reverse=True
        )
        for old in shots[max(keep, 0) :]:
            old.unlink()
    except Exception as e:
        log(f"古いスクリーンショットを消せませんでした: {e}")


def screenshot(page, tag):
    try:
        directory = pathlib.Path(config.SCREENSHOT_DIR)
        # 画面にはログイン中の情報が写るため、所有者だけ読める権限で作る。
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = directory / f"stop_{stamp}_{tag}.png"
        page.screenshot(path=str(path))
        prune_screenshots(directory, config.SCREENSHOT_KEEP)
        log(f"画面のスクリーンショットを保存しました: {path}")
    except Exception as e:
        log(f"スクリーンショットを保存できませんでした: {e}")


def goto_lesson_page(page, target):
    try:
        page.goto(
            target.page_url(),
            wait_until="domcontentloaded",
            timeout=int(config.LESSON_PAGE_READY_TIMEOUT * 1000),
        )
        return True
    except Exception as e:
        log(f"レッスン画面を開けませんでした: {e}")
        return False


def choose_start_button(page):
    """表示されている開始系ボタンを優先順で選ぶ。"""
    for selector in LESSON_BUTTON_TUPLE:
        try:
            locator = page.locator(selector)
            if locator.count() > 0 and locator.first.is_visible():
                return selector
        except Exception as e:
            log(f"開始ボタンの確認に失敗しました（{selector}）: {e}")
            continue
    return None


def click_button(page, selector):
    """dom.click に集約したクリック。押せなかったときだけログを出す。"""
    if dom.click(page, selector, timeout_ms=config.ACTION_CLICK_TIMEOUT_MS):
        return True
    log(f"ボタンを押せませんでした: {selector}")
    return False


def click_and_wait(context, page, selector, marker, timeout):
    """ボタンを押し、同一タブ／新規タブどちらでも遷移先ページを返す。"""
    before = set(context.pages)
    if not click_button(page, selector):
        return None

    def find_target():
        for candidate in context.pages:
            if candidate in before:
                continue
            if marker in dom.safe_page_url(candidate):
                return candidate
        if marker in dom.safe_page_url(page):
            return page
        return None

    return poll.poll_until(find_target, timeout, interval=config.NAV_POLL_SEC)


def preferred_os():
    """この環境で選ぶべき start_links の os 名。"""
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win"):
        return "win"
    return "chromebook"


def pick_player_link(detail, selector):
    """押したボタンに合わせて、開くべきプレイヤーの URL を API から選ぶ。

    start_links は Windows / Mac / Chromebook / iPad の版が並ぶことがある。
    先頭を鵜呑みにすると別 OS 向けの説明（キー操作が違う）を開くため、
    この環境に合う os のリンクを優先する。
    """
    if selector == config.SEL_LESSON_RESUME_BTN:
        link = detail.get("continue_link") or {}
        if link.get("player_url"):
            return link["player_url"]
    links = [
        link for link in (detail.get("start_links") or []) if link.get("player_url")
    ]
    if not links:
        return None
    want = preferred_os()
    for link in links:
        if link.get("os") == want:
            return link["player_url"]
    for link in links:
        if not link.get("os"):
            return link["player_url"]
    return links[0]["player_url"]


def choose_os_if_asked(page):
    """OS 選択ダイアログが出ていれば、この環境に合う OS を選ぶ。"""
    label = config.OS_CHOICE_LABELS.get(preferred_os())
    if not label:
        return False
    try:
        locator = page.get_by_role("button", name=label, exact=True)
        if locator.count() == 0 or not locator.first.is_visible():
            return False
        button = locator.first
        human_click(
            button,
            base_delay=0,
            timeout_ms=config.ACTION_CLICK_TIMEOUT_MS,
        )
        log(f"端末の環境「{label}」を選びました。")
        return True
    except Exception as e:
        # ボタンはアイコン付きで text-is では当たらないため、dom.click ではなく
        # 役割と名前で引いたロケータを human_click に渡す。
        log(f"端末の環境「{label}」を選べませんでした: {e}")
        return False


def wait_for_player(api, context, page, target, selector, before, timeout=None):
    """開始ボタン押下後、プレイヤーのページを返す。

    レッスン画面はクリック後に受講レコードを作ってから遷移するため、
    API の status が started になるのを待って遷移先 URL を取得する。
    タブが遷移したことを URL で検知できない場合（page.url が追随しない
    ことがある）は、取得した URL へ直接移動する。
    before はクリック前に開いていたページの集合。未完了レッスンの
    プレイヤーを別タブに開いたままにしていると、それを今回の遷移先と
    誤認して別レッスンを操作してしまうため、新しいタブだけを見る。
    """
    timeout = config.LESSON_PAGE_READY_TIMEOUT if timeout is None else timeout
    attempted = None

    def find_player():
        nonlocal attempted
        for candidate in context.pages:
            if candidate in before:
                continue
            if config.LESSON_PAGE_MARKER in dom.safe_page_url(candidate):
                return candidate
        if config.LESSON_PAGE_MARKER in dom.safe_page_url(page):
            return page
        # EX① などは開始ボタンの後に「端末の環境を選択」が出る。
        choose_os_if_asked(page)

        detail = None
        try:
            detail = api.lesson(target.chapter_id, target.lesson_id) or {}
        except AuthError:
            raise
        except MemberApiError:
            detail = None
        if not detail:
            return None
        status = (detail.get("player_lesson") or {}).get("status")
        if status not in ("started", STATUS_CLEARED):
            return None
        link = pick_player_link(detail, selector)
        if not link or link == attempted:
            return None
        attempted = link
        try:
            page.goto(
                link,
                wait_until="domcontentloaded",
                timeout=int(timeout * 1000),
            )
            return page
        except Exception as e:
            # SPA 自身の遷移と競合すると
            # 「interrupted by another navigation」で goto が失敗する。
            # 遷移は進んでいるので URL を確かめて続行する。
            log(f"プレイヤーの遷移を待ちます: {e}")
            if config.LESSON_PAGE_MARKER in dom.safe_page_url(page):
                return page
            return None

    return poll.poll_until(find_player, timeout, interval=config.NAV_POLL_SEC)


def wait_for_cleared(api, target, timeout):
    """API の status が cleared になるまで待つ。"""
    last_status = None

    def is_cleared():
        nonlocal last_status
        try:
            detail = api.lesson(target.chapter_id, target.lesson_id) or {}
            last_status = (detail.get("player_lesson") or {}).get("status")
            return last_status == STATUS_CLEARED
        except AuthError:
            raise
        except MemberApiError as e:
            last_status = str(e)
            return False

    if poll.poll_true(is_cleared, timeout, interval=config.API_POLL_SEC):
        return True
    log(f"クリアを確認できませんでした（status={last_status}）。")
    return False


def resume_url(api, target):
    try:
        detail = api.lesson(target.chapter_id, target.lesson_id) or {}
    except MemberApiError:
        return None
    return (detail.get("continue_link") or {}).get("player_url")


def checkwork_of(api, target):
    """このレッスンに対応する理解度チェックワークの id（無ければ None）。"""
    try:
        detail = api.lesson(target.chapter_id, target.lesson_id) or {}
    except AuthError:
        raise
    except MemberApiError as e:
        log(f"チェックワークの取得に失敗しました: {e}")
        return None
    for key in ("variant_checkwork", "checkwork"):
        checkwork = detail.get(key) or {}
        if checkwork.get("id"):
            return checkwork
    return None


def wait_for_graded(page, timeout=None):
    """採点結果の画面になるまで待つ。"""
    if timeout is None:
        timeout = config.CHECKWORK_GRADED_WAIT_SEC
    return poll.poll_true(
        lambda: already_graded(page), timeout, interval=config.FAST_POLL_SEC
    )


def run_checkwork(api, context, page, target):
    """レッスン完了後に、対応する理解度チェックワークを解く。"""
    if not config.AUTO_RUN_CHECKWORK:
        return True
    checkwork = checkwork_of(api, target)
    if not checkwork:
        log("このレッスンには理解度チェックワークがありません。")
        return True
    label = checkwork.get("id")
    log(f"理解度チェックワークを開始します（{label}）。")
    started = time.monotonic()
    if not goto_lesson_page(page, target):
        return False
    try:
        page.wait_for_selector(
            config.SEL_LESSON_CHECKWORK_BTN,
            timeout=int(config.LESSON_PAGE_READY_TIMEOUT * 1000),
        )
    except Exception as e:
        log(f"チェックワークのボタンが見つかりません: {e}")
        return False

    work_page = click_and_wait(
        context,
        page,
        config.SEL_LESSON_CHECKWORK_BTN,
        config.WORK_PAGE_MARKER,
        config.LESSON_PAGE_READY_TIMEOUT,
    )
    if work_page is None:
        log("チェックワーク画面へ遷移できませんでした。")
        screenshot(page, f"checkwork_{target.lesson_id}")
        return False

    ok = False
    try:
        ok = run_work(work_page)
        # 採点ボタンを押せても結果画面が出ないことがあるため、必ず確認する。
        if ok and not wait_for_graded(work_page):
            log("採点結果の画面を確認できませんでした。")
            ok = False
    finally:
        if work_page is not page and not work_page.is_closed():
            work_page.close()
    if ok:
        log(f"チェックワークを採点しました（{time.monotonic() - started:.1f} 秒）。")
    else:
        log("チェックワークを完了できませんでした。")
    return ok


def lesson_unavailable(page):
    """API には未完了で載っているのに、レッスンページが 404 のことがある。"""
    try:
        text = page.inner_text("body")
    except Exception:
        return False
    return "対応レッスンはありません" in text or "404 Not Found" in text


def run_one(api, context, page, target, index, total):
    log("")
    log(f"[{index}/{total}] {target.name} ({target.status})")
    started = time.monotonic()

    if not goto_lesson_page(page, target):
        screenshot(page, target.lesson_id)
        return RESULT_FAILED

    try:
        page.wait_for_selector(
            LESSON_BUTTONS, timeout=int(config.LESSON_PAGE_READY_TIMEOUT * 1000)
        )
    except Exception as e:
        if lesson_unavailable(page):
            # 404 は「完了」ではなく「スキップ」。完了数に数えない。
            log("このレッスンは公開されていません（404）。スキップします。")
            return RESULT_SKIPPED
        log(f"レッスン開始ボタンが見つかりません（{e}）。既に完了している可能性があります。")
        screenshot(page, f"no_button_{target.lesson_id}")
        return RESULT_FAILED

    selector = choose_start_button(page)
    if selector is None:
        log("押せる開始ボタンがありません。")
        screenshot(page, f"no_clickable_{target.lesson_id}")
        return RESULT_FAILED
    if selector == config.SEL_LESSON_RESTART_BTN and not config.AUTO_RESTART_PARTIAL:
        log("途中まで進んだレッスンですが、最初からやり直す設定になっていません。")
        return RESULT_FAILED

    before = set(context.pages)
    if not click_button(page, selector):
        screenshot(page, f"click_failed_{target.lesson_id}")
        return RESULT_FAILED

    player = wait_for_player(api, context, page, target, selector, before)
    if player is None:
        log("レッスンプレイヤーを開けませんでした。")
        screenshot(page, f"no_player_{target.lesson_id}")
        return RESULT_FAILED
    log(f"プレイヤーを開きました: {dom.safe_page_url(player)}")

    try:
        if not run_lesson(player):
            log("レッスンの自動進行が正常終了しませんでした。クリア状況を確認します。")

        if wait_for_cleared(api, target, config.AUTO_CLEAR_WAIT_SEC):
            log(f"レッスンをクリアしました（{time.monotonic() - started:.1f} 秒）。")
            # 設定フラグと対応ワークの有無は run_checkwork 内で判定する。
            run_checkwork(api, context, page, target)
            return RESULT_DONE

        url = resume_url(api, target)
        if not url:
            log("未クリアですが再開リンクがありません。停止します。")
            return RESULT_FAILED
        log("未クリアのため、続きからもう一度だけ実行します。")
        try:
            player.goto(
                url,
                wait_until="domcontentloaded",
                timeout=config.RESUME_PAGE_GOTO_TIMEOUT_MS,
            )
        except Exception as e:
            log(f"再開リンクを開けませんでした: {e}")
            return RESULT_FAILED
        if not run_lesson(player):
            log("再開後の自動進行も正常終了しませんでした。")

        if wait_for_cleared(api, target, config.AUTO_CLEAR_WAIT_SEC):
            log(f"レッスンをクリアしました（{time.monotonic() - started:.1f} 秒）。")
            run_checkwork(api, context, page, target)
            return RESULT_DONE
        screenshot(player, f"not_cleared_{target.lesson_id}")
        return RESULT_FAILED
    finally:
        if player is not page and not player.is_closed():
            player.close()


# 一覧表示用の状態ラベル（API は unstarted / unlocked / started / cleared / locked を返す）。
STATUS_LABELS = {
    STATUS_CLEARED: "完了",
    "started": "途中",
    "unstarted": "未着手",
    "unlocked": "未着手",
    STATUS_LOCKED: "ロック",
}


def run_targets(api, context, page, targets, stop_on_failure=False, on_progress=None):
    """レッスンの一覧を順に実行し、結果を分類して返す。

    MCP サーバーのジョブからも呼ぶため、集計と表示を分けてある。
    on_progress(index, total, target, result) は 1 本終わるたびに呼ばれる。
    """
    done = []
    skipped = []
    failed = []
    total = len(targets)
    for index, target in enumerate(targets, 1):
        result = run_one(api, context, page, target, index, total)
        if result == RESULT_DONE:
            done.append(target)
        elif result == RESULT_SKIPPED:
            # 完了ではないので done に数えず、スキップとして記録する。
            skipped.append(target)
            log(f"「{target.name}」は公開されていないため、スキップします。")
        else:
            failed.append(target)
            if on_progress is not None:
                on_progress(index, total, target, result)
            if stop_on_failure:
                log("")
                log(f"「{target.name}」で停止しました。原因を確認して再実行してください。")
                break
            log(f"「{target.name}」は自動化できませんでした（未完了のまま次へ進みます）。")
            continue
        if on_progress is not None:
            on_progress(index, total, target, result)
    return {"done": done, "skipped": skipped, "failed": failed}


def print_summary(results):
    """run_targets の結果を表示する。"""
    done = results["done"]
    skipped = results["skipped"]
    failed = results["failed"]
    total = len(done) + len(skipped) + len(failed)
    log("")
    log(
        f"完了 {len(done)} 件 / スキップ {len(skipped)} 件 / 失敗 {len(failed)} 件"
        f"（対象 {total} 件）。"
    )
    if skipped:
        log(f"公開されておらずスキップしたレッスン（{len(skipped)} 件）:")
        for target in skipped:
            log(f"  - {target.name} ({target.lesson_id})")
    if failed:
        log(f"自動化できなかったレッスン（{len(failed)} 件）:")
        for target in failed:
            log(f"  - {target.name} ({target.lesson_id})")
        log("これらは手動で進めるか、原因を確認してから再実行してください。")


def status_label(status):
    return STATUS_LABELS.get(status, status or "?")


def select_lesson(api, course, chapter_label=None):
    """レッスンの一覧を表示し、番号で選ばれた 1 本を返す。

    完了済みも含めた全レッスンを出す。ロック中は選べない。
    """
    targets = list_all_lessons(api, course, chapter_label)
    if not targets:
        log("レッスンが見つかりませんでした。")
        return None

    ui.blank()
    ui.header(f"コース「{course.get('name')}」のレッスン一覧")
    current_chapter = None
    rows = []
    for index, target in enumerate(targets, 1):
        if target.chapter_label != current_chapter:
            if rows:
                ui.lesson_table(rows)
                rows = []
            current_chapter = target.chapter_label
            heading = f"{current_chapter} {target.chapter_title}".rstrip()
            ui.chapter(heading)
        label = target.label if target.label not in ("", "-", None) else str(target.lesson_id)
        title = f"「{target.title}」" if target.title else ""
        rows.append((index, label, title, status_label(target.status)))
    if rows:
        ui.lesson_table(rows)
    ui.blank()

    while True:
        try:
            raw = ui.ask("実行するレッスンの番号を入力してください（q で終了）: ")
        except EOFError:
            return None
        raw = raw.strip()
        if raw.lower() in ("q", "quit", "exit"):
            return None
        if not raw.isdigit():
            log("番号を入力してください。")
            continue
        index = int(raw)
        if not 1 <= index <= len(targets):
            log(f"1〜{len(targets)} の番号を入力してください。")
            continue
        target = targets[index - 1]
        if target.status == STATUS_LOCKED:
            log("ロック中のレッスンは実行できません。")
            continue
        return target


def print_targets(course, targets, scope):
    ui.blank()
    ui.header(
        f"コース「{course.get('name')}」/ 範囲: {scope} / "
        f"未完了レッスン: {len(targets)} 件"
    )
    rows = []
    for index, target in enumerate(targets, 1):
        label = target.label if target.label not in ("", "-", None) else str(target.lesson_id)
        title = f"「{target.title}」" if target.title else ""
        rows.append((index, label, title, status_label(target.status)))
    if rows:
        ui.lesson_table(rows)
    ui.blank()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="未完了レッスンを自動選択して連続実行します。"
    )
    parser.add_argument(
        "--select",
        "--choose",
        dest="select",
        action="store_true",
        help="レッスン一覧から番号で選んで 1 本だけ実行する",
    )
    parser.add_argument(
        "--dry-run",
        "--list",
        dest="dry_run",
        action="store_true",
        help="対象レッスンの一覧だけ表示して終了する",
    )
    parser.add_argument("--course", help="コース名（既定: config.AUTO_COURSE_NAME）")
    parser.add_argument(
        "--scope",
        choices=("chapter", "course"),
        help="chapter: 最初の未完了を含む章だけ / course: コース全体",
    )
    parser.add_argument("--chapter", help='章の絞り込み（例: "第5章"）')
    parser.add_argument("--lesson", type=int, help="レッスン ID を指定してその 1 本だけ実行")
    parser.add_argument("--max", type=int, help="実行するレッスン数の上限（0 で無制限）")
    parser.add_argument(
        "--stop-on-failure",
        action="store_true",
        help="自動化できなかったレッスンで停止する（既定は記録して次へ進む）",
    )
    parser.add_argument(
        "--checkwork",
        dest="checkwork",
        action="store_true",
        default=None,
        help="レッスン完了後に理解度チェックワークも解く（既定）",
    )
    parser.add_argument(
        "--no-checkwork",
        dest="checkwork",
        action="store_false",
        help="理解度チェックワークを解かない",
    )
    return parser.parse_args(argv)


def apply_args(args):
    if args.course:
        config.AUTO_COURSE_NAME = args.course
    if args.scope:
        config.AUTO_SCOPE = args.scope
    if args.chapter:
        config.AUTO_CHAPTER_LABEL = args.chapter
    if args.checkwork is not None:
        config.AUTO_RUN_CHECKWORK = args.checkwork
    limit = config.AUTO_MAX_LESSONS
    if args.max is not None:
        limit = args.max
    return limit


def main(argv=None):
    args = parse_args(argv)
    limit = apply_args(args)

    log("--- 未完了レッスンの自動実行を開始します ---")
    if not acquire_instance():
        return 1
    session = ChromeSession()
    exit_code = 1
    try:
        page = session.connect()
        context = session.browser.contexts[0]
        api = MemberApi(context)

        course = find_course(api.textbook_courses(), config.AUTO_COURSE_NAME)

        if args.select:
            target = select_lesson(api, course, config.AUTO_CHAPTER_LABEL)
            if target is None:
                log("レッスンを選ばなかったため終了します。")
                exit_code = 0
                return exit_code
            # 明示的に選んだレッスンなので、完了済みでも「最初からやり直す」を許す。
            config.AUTO_RESTART_PARTIAL = True
            result = run_one(api, context, page, target, 1, 1)
            if result == RESULT_SKIPPED:
                log("選択したレッスンは公開されていないため、実行していません。")
            exit_code = 0 if result != RESULT_FAILED else 1
            return exit_code

        targets = iter_lessons(
            api,
            course,
            scope=config.AUTO_SCOPE,
            chapter_label=config.AUTO_CHAPTER_LABEL,
        )
        if args.lesson:
            targets = [target for target in targets if target.lesson_id == args.lesson]
            if not targets:
                log(f"レッスン {args.lesson} は未完了の対象に含まれていません。")
                return 1
        if limit:
            targets = targets[:limit]

        print_targets(course, targets, config.AUTO_SCOPE)
        if args.dry_run:
            exit_code = 0
            return exit_code
        if not targets:
            log("未完了のレッスンはありません。すべて完了しています。")
            exit_code = 0
            return exit_code

        results = run_targets(
            api, context, page, targets, stop_on_failure=args.stop_on_failure
        )
        print_summary(results)
        exit_code = 1 if args.stop_on_failure and results["failed"] else 0
    except AuthError as e:
        log(str(e))
    except MemberApiError as e:
        log(f"API エラー: {e}")
    except KeyboardInterrupt:
        log("処理を中断しました。")
        exit_code = 130
    finally:
        session.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
