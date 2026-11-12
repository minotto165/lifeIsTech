"""Life is Tech! レッスン画面の自動進行スクリプト。

プレイヤー (player.lifeistech-lesson.jp/player/step) は React 製で、
ボタンは <button> ではなく div とクラス名で描画される。
クイズの正解はシナリオ JSON から取得し、取得できない場合のみ Gemini に問い合わせる。
基本は Enter キー連打で進め、クイズと進行ボタンだけ個別に処理する。
"""

import json
import re
import time

import config
from ai_analyzer import get_ai_response
from chrome_session import ChromeSession
from human_utils import human_click, human_sleep


def log(message):
    print(message, flush=True)


def count(page, selector):
    try:
        return page.locator(selector).count()
    except Exception:
        return 0


def current_step(page):
    try:
        match = re.search(r"[?&]step_id=(\d+)", page.url)
        return int(match.group(1)) if match else None
    except Exception:
        return None


def normalize(text):
    return re.sub(r"\s+", "", text or "")


# --- ページ状態の一括取得 ---------------------------------------------

READ_STATE_JS = r"""() => {
    const visible = (el) => !!el && el.offsetParent !== null;
    const action = [...document.querySelectorAll('%(action)s')].find(visible) || null;
    const next = [...document.querySelectorAll('%(next)s')]
        .filter(visible)
        .find((el) => (el.innerText || '').includes('%(next_text)s')) || null;
    // クイズの選択肢が「disabled のみ・chosen なし」で固まっていないかを見る。
    // 正解表示より前に Enter を送るとこの状態で固まり、Enter では復帰できない。
    const quizButtons = [...document.querySelectorAll('%(quiz_button)s')];
    const chosen = quizButtons.some((el) => (el.className || '').indexOf('chosen') !== -1);
    const locked = quizButtons.length > 0 && quizButtons.every(
        (el) => (el.className || '').indexOf('%(disabled)s') !== -1
    );
    return {
        in_lesson: location.pathname.indexOf('%(marker)s') !== -1,
        step: new URLSearchParams(location.search).get('step_id'),
        quiz: !!document.querySelector('%(quiz_modal)s'),
        action: action ? (action.innerText || '').trim().replace(/\s+/g, ' ') : null,
        next_ready: !!next && (next.className || '').indexOf('%(disabled)s') === -1,
        quiz_chosen: chosen,
        quiz_locked: locked && !chosen,
    };
}"""


def read_state(page):
    """URL・クイズ・進行ボタンの状態を 1 回の evaluate でまとめて取る。"""
    js = READ_STATE_JS % {
        "action": config.SEL_LESSON_ACTION_BTN,
        "next": config.SEL_LESSON_NEXT_BTN,
        "next_text": config.LESSON_NEXT_TEXT,
        "marker": config.LESSON_PAGE_MARKER,
        "quiz_modal": config.SEL_QUIZ_MODAL,
        "quiz_button": config.SEL_QUIZ_BUTTON,
        "disabled": config.LESSON_DISABLED_CLASS,
    }
    return page.evaluate(js)


# --- アニメーション待ちのスキップ -------------------------------------

# プレイヤーは各ステップの終了条件に「AnimationFinish」を持つことが多く、
# 実際には div.motion の CSS トランジション（既定 900ms）の完了を待っている。
# Enter では縮められないため、transitionend を擬似的に発火させて待ちを消す。
SKIP_ANIMATION_JS = r"""() => {
    const props = ['opacity', 'left', 'top', 'width', 'height', 'transform'];
    let fired = 0;
    for (const el of document.querySelectorAll('div.motion')) {
        for (const prop of props) {
            el.dispatchEvent(new TransitionEvent('transitionend', {
                propertyName: prop,
                bubbles: false,
            }));
        }
        fired += 1;
    }
    return fired;
}"""


def skip_animations(page):
    """マウント済みのモーション要素に擬似 transitionend を送る。"""
    if not config.SKIP_ANIMATION_WAIT:
        return 0
    try:
        return page.evaluate(SKIP_ANIMATION_JS)
    except Exception:
        return 0


# --- シナリオ JSON（クイズの正解データ） -------------------------------


FETCH_SCENARIO_JS = """async () => {
    const q = new URL(location.href).searchParams;
    const url = '/api/lesson_player/scenario'
        + '?project_name=' + encodeURIComponent(q.get('project_name') || '')
        + '&scenario_path=' + encodeURIComponent(q.get('scenario_path') || '')
        + '&scenario_id=' + encodeURIComponent(q.get('scenario_id') || '');
    const response = await fetch(url, {headers: {'Accept': 'application/json'}});
    if (!response.ok) throw new Error('status ' + response.status);
    return await response.text();
}"""


def fetch_scenario(page):
    """プレイヤーが裏で使っているシナリオ JSON を同じセッションで取得する。"""
    return json.loads(page.evaluate(FETCH_SCENARIO_JS))


def build_quiz_answers(scenario):
    """クイズ文とステップ ID の両方から正解を引けるようにする。"""
    by_text = {}
    by_step = {}
    for step in scenario.get("steps", []):
        for element in step.get("elementList", []):
            if element.get("elementName") != "QuizModalElement":
                continue
            correct = element.get("correctChoice")
            if not correct:
                continue
            question = normalize(element.get("quizText", ""))
            if question:
                by_text[question] = correct
            by_step[step.get("stepId")] = correct
    return {"by_text": by_text, "by_step": by_step}


def load_quiz_answers(page):
    try:
        answers = build_quiz_answers(fetch_scenario(page))
    except Exception as e:
        log(f"シナリオの取得に失敗しました（AI で回答します）: {e}")
        return {"by_text": {}, "by_step": {}}
    log(f"クイズの正解データを {len(answers['by_step'])} 件読み込みました。")
    return answers


# --- クイズ ----------------------------------------------------------


def press_enter(page):
    """Enter を 1 回送る。クイズの正解表示を閉じて次へ進むのにも使う。"""
    try:
        page.keyboard.press("Enter")
    except Exception as e:
        log(f"Enter キーを送信できませんでした: {e}")


def quiz_choices(page):
    buttons = page.locator(config.SEL_QUIZ_BUTTON)
    choices = []
    for index in range(buttons.count()):
        try:
            text = buttons.nth(index).inner_text().strip()
        except Exception:
            continue
        if text:
            choices.append((text, buttons.nth(index)))
    return choices


def find_choice(choices, correct):
    if not correct:
        return None
    wanted = normalize(correct)
    for text, locator in choices:
        if normalize(text) == wanted:
            return text, locator
    for text, locator in choices:
        if normalize(text) and (wanted in normalize(text) or normalize(text) in wanted):
            return text, locator
    return None


def ask_ai(question, options):
    prompt = (
        f"クイズ: {question}\n"
        f"選択肢: {options}\n"
        "正解の選択肢のテキストを一つだけ、余計な言葉なしで返してください。"
    )
    return get_ai_response(prompt)


def read_quiz_question(page):
    try:
        return page.locator(config.SEL_QUIZ_TEXT).first.inner_text().strip()
    except Exception:
        return ""


def has_chosen(page):
    return count(page, config.SEL_QUIZ_BUTTON + ".chosen") > 0


def wait_for_chosen(page, timeout=1.0):
    """クリックが実際に反映された（chosen が付いた）ことを確認する。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if has_chosen(page):
            return True
        human_sleep(0.02)
    return False


def wait_for_quiz_change(page, prev_question, timeout=2.0):
    """回答後、モーダルが閉じるか次の問題に切り替わるまで待つ。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if count(page, config.SEL_QUIZ_MODAL) == 0:
            return True
        if normalize(read_quiz_question(page)) != prev_question:
            return True
        human_sleep(0.02)
    return False


HAS_FEEDBACK_JS = r"""() => /正解|不正解/.test(document.body.innerText || '')"""


def has_feedback(page):
    try:
        return bool(page.evaluate(HAS_FEEDBACK_JS))
    except Exception:
        return False


def wait_for_feedback(page, timeout=None):
    """回答直後の「正解／不正解」表示が出るまで待つ。

    この表示より先に Enter を送ると、次の問題のモーダルが
    「disabled のみ・chosen なし」のまま固まり、Enter では復帰しなくなる。
    """
    end = time.monotonic() + (config.QUIZ_FEEDBACK_WAIT if timeout is None else timeout)
    while time.monotonic() < end:
        if has_feedback(page):
            return True
        human_sleep(0.05)
    return False


def handle_quiz(page, answers, answered_steps):
    """クイズが表示されていれば正解の選択肢をクリックして Enter で閉じる。

    回答済みかどうかは「chosen クラスが付いているか」で判定する。
    ステップ ID だけの記録だと、次の問題が同じ状態で開いたときに
    「もう答えた」と誤判定して無視してしまうため。
    """
    step = current_step(page)
    question = read_quiz_question(page)
    key = (step, normalize(question))

    if not has_chosen(page):
        # まだ答えていない問題。選択肢を選ぶところからやり直す。
        choices = quiz_choices(page)
        if not choices:
            human_sleep(0.05)
            return

        correct = answers["by_text"].get(key[1]) or answers["by_step"].get(step)
        target = find_choice(choices, correct)
        if target is None:
            target = find_choice(choices, ask_ai(question, [text for text, _ in choices]))
        if target is None:
            log(f"クイズの正解を特定できませんでした（step {step}）。次の周回で再試行します。")
            human_sleep(0.2)
            return

        choice_text, locator = target
        try:
            human_click(locator, base_delay=0)
        except Exception as e:
            log(f"選択肢のクリックに失敗しました: {e}")
            return

        if not wait_for_chosen(page):
            log(f"選択肢のクリックが反映されませんでした（step {step}）。再試行します。")
            return

        answered_steps.add(key)
        log(f"クイズに回答しました: {choice_text}")
        if not wait_for_feedback(page):
            log(f"正解表示を確認できませんでした（step {step}）。Enter を送ります。")
        press_enter(page)
        if not wait_for_quiz_change(page, key[1]):
            log(f"クイズが閉じませんでした（step {step}）。次の周回で再試行します。")
        return

    # 回答済み（正解表示中）。正解表示が出ていることを確認してから Enter で閉じる。
    answered_steps.add(key)
    wait_for_feedback(page, timeout=1.0)
    press_enter(page)
    if not wait_for_quiz_change(page, key[1]):
        log(f"クイズが閉じませんでした（step {step}）。次の周回で再試行します。")


# --- 画面遷移 --------------------------------------------------------


def click_progress_next(page):
    buttons = page.locator(config.SEL_LESSON_NEXT_BTN).filter(has_text=config.LESSON_NEXT_TEXT)
    if buttons.count() == 0:
        return False
    button = buttons.first
    if config.LESSON_DISABLED_CLASS in (button.get_attribute("class") or ""):
        return False
    try:
        human_click(button, base_delay=0, timeout_ms=config.ACTION_CLICK_TIMEOUT_MS)
    except Exception:
        # クリック待ちの間にステップが進んでボタンが消えただけのことがある。
        return False
    return True


def clean_label(text):
    """ボタン文言はアニメーション用に二重描画されるため、連続する重複を取り除く。"""
    parts = text.split()
    return " ".join(p for i, p in enumerate(parts) if i == 0 or p != parts[i - 1])


def click_action_button(page):
    """スライドの「Next」などの進行ボタンを 1 回押す。

    Enter 連打と並行して動くため、押しに行く前にステップが進んで
    ボタンが消えていることがある。長いタイムアウトで待つと 10 秒固まるので、
    短いタイムアウトで諦め、それでも押せない場合は JS クリックで試す。
    """
    step = current_step(page)
    buttons = page.locator(config.SEL_LESSON_ACTION_BTN)
    for index in range(buttons.count()):
        button = buttons.nth(index)
        try:
            if not button.is_visible():
                continue
            text = clean_label(re.sub(r"\s+", " ", button.inner_text()).strip())
        except Exception:
            continue
        try:
            human_click(button, base_delay=0, timeout_ms=config.ACTION_CLICK_TIMEOUT_MS)
        except Exception:
            # 押す前にステップが進んでいただけなら、何もしなくてよい。
            if current_step(page) != step:
                return False
            # 覆われているなどでクリックできない場合は JS で直接押す。
            try:
                if not button.is_visible():
                    return False
                button.evaluate("(el) => el.click()")
            except Exception:
                return False
        log(f"「{text}」ボタンを押しました。")
        return True
    return False


# --- メイン ----------------------------------------------------------


def reload_page(page):
    """壊れたクイズモーダルから復帰するため、ページを再読込する。"""
    log("クイズが応答しないためページを再読込します。")
    try:
        page.reload(wait_until="domcontentloaded")
        return True
    except Exception as e:
        log(f"ページの再読込に失敗しました: {e}")
        return False


def auto_lesson():
    log("--- レッスン自動化を実行します（Enter 連打モード） ---")
    try:
        with ChromeSession(config.LESSON_PAGE_MARKER) as page:
            page.set_default_timeout(config.ACTION_TIMEOUT_MS)
            state = read_state(page)
            if not state["in_lesson"]:
                log("レッスンページが開かれていません。ブラウザでレッスンを開いてから実行してください。")
                return

            answers = load_quiz_answers(page)
            answered_steps = set()
            clicked_action_steps = set()
            last_step = state["step"]
            last_change = time.monotonic()
            locked_since = None
            log(f"ステップ {last_step} から開始します。")

            for _ in range(config.MAX_LOOP_ITERATIONS):
                try:
                    state = read_state(page)
                except Exception as e:
                    # レッスン完了時は SPA 遷移で実行コンテキストが破棄される。
                    if "Execution context was destroyed" in str(e):
                        log("レッスン画面が遷移したため終了します（完了）。")
                        return
                    raise
                if not state["in_lesson"]:
                    log("レッスンページを離れたため終了します。")
                    return

                step = state["step"]
                now = time.monotonic()
                if step != last_step:
                    log(f"ステップ {step} を通過しました。")
                    last_step = step
                    last_change = now

                if state["quiz"]:
                    # 「disabled のみ・chosen なし」で固まったモーダルは Enter では
                    # 復帰できないため、一定時間で再読込して立て直す。
                    if state["quiz_locked"]:
                        locked_since = locked_since or now
                        if now - locked_since > config.BROKEN_QUIZ_WAIT:
                            locked_since = None
                            if reload_page(page):
                                last_change = time.monotonic()
                                continue
                    else:
                        locked_since = None
                    handle_quiz(page, answers, answered_steps)
                    continue
                locked_since = None

                # 進行ボタン（Quiz / Next）はステップごとに 1 回だけ押す。
                if state["action"] and step not in clicked_action_steps:
                    clicked_action_steps.add(step)
                    if click_action_button(page):
                        last_change = time.monotonic()

                # アニメーション完了待ち（約900ms）を飛ばす。
                skip_animations(page)

                # ここが本命: Enter 連打（待機なし）。
                press_enter(page)

                now = time.monotonic()
                if now - last_change > config.STUCK_CLICK_AFTER and state["next_ready"]:
                    if click_progress_next(page):
                        last_change = now
                if now - last_change > config.STUCK_GIVEUP_AFTER:
                    log("画面が長時間変化しないため終了します。")
                    return

                human_sleep(config.ENTER_SPAM_DELAY)

            log("最大ループ回数に達したため終了します。")
    except KeyboardInterrupt:
        log("処理を中断しました。")
    except Exception as e:
        log(f"エラーが発生したため終了します: {e}")


if __name__ == "__main__":
    auto_lesson()
