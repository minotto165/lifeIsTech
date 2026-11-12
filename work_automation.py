"""Life is Tech! 理解度チェックワークの自動解答スクリプト。"""

import random

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


def click_if_present(page, selector, base_delay=1.0):
    locator = page.locator(selector)
    if locator.count() == 0:
        return False
    try:
        human_click(locator.first, base_delay=base_delay)
        return True
    except Exception as e:
        log(f"要素のクリックに失敗しました: {e}")
        return False


def read_question(page):
    """問題文が描画されるまで待つ。取得できなければ None を返す。"""
    question_locs = page.locator(config.SEL_WORK_QUESTION)
    for _ in range(config.QUESTION_RETRY_COUNT):
        if count(page, config.SEL_WORK_GRADE_BTN) > 0:
            return None
        if question_locs.count() > config.WORK_QUESTION_INDEX:
            try:
                text = question_locs.nth(config.WORK_QUESTION_INDEX).inner_text().strip()
            except Exception:
                text = ""
            if text:
                return text
        human_sleep(config.QUESTION_RETRY_INTERVAL)
    return None


def collect_options(page):
    """ラジオボタンを持つ label だけを選択肢として収集する。"""
    labels = page.locator(config.SEL_WORK_OPTION_LABEL)
    options = {}
    for i in range(labels.count()):
        label = labels.nth(i)
        try:
            text = label.inner_text().strip()
        except Exception:
            continue
        if not text or len(text) > config.MAX_OPTION_TEXT_LENGTH:
            continue
        if label.locator("input").count() == 0:
            continue
        if text not in options:
            options[text] = label.locator("input").first
    return options


def choose_option(options, ai_result):
    """AI の回答と誤答確率から、実際にクリックする選択肢を決める。"""
    if not options:
        return None, False
    correct = [
        text
        for text in options
        if ai_result and (text in ai_result or ai_result in text)
    ]
    if correct and len(options) > 1 and random.random() < config.WRONG_ANSWER_RATE:
        wrong = [text for text in options if text not in correct]
        if wrong:
            return random.choice(wrong), True
    if correct:
        return correct[0], False
    return None, False


def answer_question(page):
    """問題を解いて選択肢をクリックする。問題が無ければ None。"""
    question_text = read_question(page)
    if question_text is None:
        return None

    options = collect_options(page)
    if not options:
        log("選択肢を取得できませんでした。")
        return question_text

    prompt = (
        f"問題: {question_text}\n"
        f"選択肢: {list(options.keys())}\n"
        "正解の選択肢のテキストを一つだけ、余計な言葉なしで返してください。"
    )
    text, intentional_error = choose_option(options, get_ai_response(prompt))
    if text is None:
        log("正解を特定できませんでした。次の画面へ進みます。")
        return question_text

    if intentional_error:
        log(f"意図的に誤答します: {text}")
    try:
        human_click(options[text], base_delay=0.2)
        human_sleep(0.2)
    except Exception as e:
        log(f"選択肢のクリックに失敗しました: {e}")
    return question_text


def go_next(page):
    """次へ進む・確認・採点のいずれかを押す。押せなければ False。"""
    if click_if_present(page, config.SEL_WORK_NEXT_BTN, base_delay=1.5):
        return True
    if click_if_present(page, config.SEL_WORK_CONFIRM_BTN, base_delay=0.5):
        return True
    if click_if_present(page, config.SEL_WORK_GRADE_BTN, base_delay=0.5):
        log("採点しました。")
        return True
    return False


def solve_work():
    log("--- 理解度チェックワーク自動化を開始します ---")
    try:
        with ChromeSession() as page:
            for _ in range(config.MAX_LOOP_ITERATIONS):
                if click_if_present(page, config.SEL_WORK_GRADE_BTN, base_delay=2.0):
                    log("採点しました。")
                    return

                question_text = answer_question(page)
                if question_text is None:
                    if click_if_present(page, config.SEL_WORK_GRADE_BTN, base_delay=2.0):
                        log("採点しました。")
                        return
                    log("問題文を取得できなかったため終了します。")
                    return

                if not go_next(page):
                    log("遷移ボタンが見つからないため終了します。")
                    return
            log("最大ループ回数に達したため終了します。")
    except KeyboardInterrupt:
        log("処理を中断しました。")
    except Exception as e:
        log(f"エラーが発生したため終了します: {e}")


if __name__ == "__main__":
    solve_work()
