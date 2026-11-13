"""Life is Tech! 理解度チェックワークの自動解答スクリプト。"""

import random
import re
import sys

import config
from ai_analyzer import get_ai_response
from chrome_session import ChromeSession
from human_utils import human_click, human_sleep
from instance_guard import acquire as acquire_instance


def log(message):
    print(message, flush=True)


def count(page, selector):
    try:
        return page.locator(selector).count()
    except Exception:
        return 0


def normalize(text):
    return re.sub(r"\s+", "", text or "")


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
    """ラジオボタンを持つ label だけを選択肢として収集する。

    同じ文言の選択肢が並ぶことがあるため、dict ではなくリストで保持する。
    """
    labels = page.locator(config.SEL_WORK_OPTION_LABEL)
    options = []
    for i in range(labels.count()):
        label = labels.nth(i)
        try:
            text = label.inner_text().strip()
        except Exception:
            continue
        if not text or len(text) > config.MAX_OPTION_TEXT_LENGTH:
            continue
        inputs = label.locator("input")
        if inputs.count() == 0:
            continue
        options.append((text, inputs.first))
    return options


def choose_option(options, ai_result):
    """AI の回答と誤答確率から、実際にクリックする選択肢を決める。

    部分一致は「A が B に含まれる」だけで誤爆するため、完全一致を優先する。
    """
    if not options:
        return None, False
    answer = normalize(ai_result)
    correct = []
    if answer:
        correct = [(text, loc) for text, loc in options if normalize(text) == answer]
        if not correct:
            correct = [
                (text, loc)
                for text, loc in options
                if normalize(text) and (normalize(text) in answer or answer in normalize(text))
            ]
    if correct and len(options) > 1 and random.random() < config.WRONG_ANSWER_RATE:
        chosen = {text for text, _ in correct}
        wrong = [(text, loc) for text, loc in options if text not in chosen]
        if wrong:
            return random.choice(wrong)[0], True
    if correct:
        return correct[0][0], False
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
        f"選択肢: {[text for text, _ in options]}\n"
        "正解の選択肢のテキストを一つだけ、余計な言葉なしで返してください。"
    )
    text, intentional_error = choose_option(options, get_ai_response(prompt))
    if text is None:
        log("正解を特定できませんでした。次の画面へ進みます。")
        return question_text

    target = next((loc for label, loc in options if label == text), None)
    if target is None:
        log("選択肢の対応付けに失敗しました。次の画面へ進みます。")
        return question_text
    if intentional_error:
        log(f"意図的に誤答します: {text}")
    try:
        human_click(target, base_delay=0.2)
        human_sleep(0.2)
    except Exception as e:
        log(f"選択肢のクリックに失敗しました: {e}")
    return question_text


def go_next(page):
    """次へ進む・確認・採点のいずれかを押し、押した種別を返す。"""
    if click_if_present(page, config.SEL_WORK_NEXT_BTN, base_delay=1.5):
        return "next"
    if click_if_present(page, config.SEL_WORK_CONFIRM_BTN, base_delay=0.5):
        return "confirm"
    if click_if_present(page, config.SEL_WORK_GRADE_BTN, base_delay=0.5):
        return "grade"
    return None


def run_work(page):
    """開いている理解度チェックワークを採点まで進める。採点したら True。"""
    try:
        last_question = None
        stalled = 0
        for _ in range(config.MAX_WORK_STEPS):
            if click_if_present(page, config.SEL_WORK_GRADE_BTN, base_delay=2.0):
                log("採点しました。")
                return True

            question_text = answer_question(page)
            if question_text is None:
                if click_if_present(page, config.SEL_WORK_GRADE_BTN, base_delay=2.0):
                    log("採点しました。")
                    return True
                log("問題文を取得できなかったため終了します。")
                return False

            action = go_next(page)
            if action is None:
                log("遷移ボタンが見つからないため終了します。")
                return False
            if action == "grade":
                log("採点しました。")
                return True

            if question_text == last_question:
                stalled += 1
                if stalled >= config.WORK_STALL_LIMIT:
                    log(f"同じ問題のまま進まないため終了します（{stalled} 回連続）。")
                    return False
            else:
                stalled = 0
                last_question = question_text
        log(f"設問数が上限（{config.MAX_WORK_STEPS}）に達したため終了します。")
        return False
    except KeyboardInterrupt:
        raise
    except Exception as e:
        log(f"エラーが発生したため終了します: {e}")
        return False


def solve_work():
    log("--- 理解度チェックワーク自動化を開始します ---")
    if not acquire_instance():
        return False
    try:
        # 無関係なタブ（レッスンなど）に回答を送らないよう、URL で絞り込む。
        with ChromeSession(config.WORK_PAGE_MARKER) as page:
            return run_work(page)
    except KeyboardInterrupt:
        log("処理を中断しました。")
        return False
    except RuntimeError as e:
        log(str(e))
        return False


if __name__ == "__main__":
    sys.exit(0 if solve_work() else 1)
