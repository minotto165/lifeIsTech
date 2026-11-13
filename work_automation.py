"""Life is Tech! 理解度チェックワークの自動解答スクリプト。"""

import json
import os
import random
import re
import sys
import time

import config
import ui
from ai_analyzer import get_ai_response, get_ai_response_with_images
from chrome_session import ChromeSession
from human_utils import human_click
from instance_guard import acquire as acquire_instance


def log(message):
    ui.log(message)


def count(page, selector):
    try:
        return page.locator(selector).count()
    except Exception:
        return 0


def normalize(text):
    return re.sub(r"\s+", "", text or "")


def grade_state(page):
    """採点ボタンの有無と押せるかどうか（全問回答するまで無効）。"""
    try:
        return page.evaluate(GRADE_STATE_JS, config.GRADE_BTN_TEXT)
    except Exception:
        return {"found": False, "enabled": False}


def grade_ready(page):
    return grade_state(page)["enabled"]


def click_if_present(page, selector, base_delay=1.0, timeout_ms=config.ACTION_CLICK_TIMEOUT_MS):
    locator = page.locator(selector)
    if locator.count() == 0:
        return False
    try:
        # disabled を押しに行くと Playwright が待ち時間いっぱい粘るので先に弾く。
        if locator.first.is_disabled():
            return False
    except Exception:
        pass
    try:
        human_click(locator.first, base_delay=base_delay, timeout_ms=timeout_ms)
        return True
    except Exception as e:
        log(f"要素のクリックに失敗しました: {e}")
        return False


COLLECT_OPTIONS_JS = """() => [...document.querySelectorAll('input[type="radio"], input[type="checkbox"]')]
  .map((input, index) => {
    const box = input.closest('label') || input.parentElement;
    const text = box ? (box.innerText || '').trim() : '';
    const alts = box ? [...box.querySelectorAll('img')].map((img) => img.alt || '').filter(Boolean) : [];
    return { index: index, text: text, alts: alts };
  })"""

GRADE_STATE_JS = """(text) => {
  const btn = [...document.querySelectorAll('button')].find((b) => (b.innerText || '').includes(text));
  return { found: !!btn, enabled: !!btn && !btn.disabled };
}"""

SELECT_OPTION_JS = """(index) => {
  const inputs = [...document.querySelectorAll('input[type="radio"], input[type="checkbox"]')];
  const input = inputs[index];
  if (!input) return false;
  (input.closest('label') || input).click();
  return true;
}"""


def collect_options(page):
    """選択肢を index 付きで集める（1 往復）。

    テキストを持たず画像だけの選択肢（コードの正誤を選ぶ問題など）もあるため、
    その場合は img の alt を表示名にし、画像であることを覚えておく。
    """
    try:
        raw = page.evaluate(COLLECT_OPTIONS_JS)
    except Exception:
        return []
    options = []
    for item in raw:
        text = (item.get("text") or "").strip()
        alts = item.get("alts") or []
        image_only = not text and bool(alts)
        if image_only:
            text = alts[0]
        if not text:
            text = f"画像の選択肢{item.get('index', 0) + 1}"
        if len(text) > config.MAX_OPTION_TEXT_LENGTH:
            continue
        options.append(
            {"text": text, "image_only": image_only, "index": item["index"]}
        )
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
        correct = [o for o in options if normalize(o["text"]) == answer]
        if not correct:
            correct = [
                o
                for o in options
                if normalize(o["text"])
                and (normalize(o["text"]) in answer or answer in normalize(o["text"]))
            ]
    if not correct and any(o["image_only"] for o in options):
        # 画像だけの選択肢は AI が内容を読めないので先頭を選ぶ（採点は通る）。
        log("画像の選択肢のため先頭を選びます。")
        return options[0], False
    if correct and len(options) > 1 and random.random() < config.WRONG_ANSWER_RATE:
        chosen = {o["text"] for o in correct}
        wrong = [o for o in options if o["text"] not in chosen]
        if wrong:
            return random.choice(wrong), True
    if correct:
        return correct[0], False
    return None, False


def answer_cache_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), config.WORK_ANSWER_CACHE)


def answer_cache_key(text, options):
    parts = [normalize(text)] + sorted(normalize(o["text"]) for o in options)
    return "|".join(parts)


def read_answer_cache():
    try:
        with open(answer_cache_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_answer_cache(cache):
    try:
        path = answer_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log(f"解答キャッシュを保存できませんでした: {e}")


def cached_answer(text, options, prompt):
    """同じ問題なら AI を呼ばずキャッシュから返す。"""
    key = answer_cache_key(text, options)
    cache = read_answer_cache()
    hit = cache.get(key)
    if hit:
        return hit
    answer = get_ai_response(prompt)
    if answer:
        cache[key] = answer
        write_answer_cache(cache)
    return answer


CARD_JS = """() => {
  const labs = [...document.querySelectorAll('label')].filter(
    (l) => l.querySelector('input[type="radio"], input[type="checkbox"]')
  );
  if (!labs.length) return null;
  let root = labs[0];
  for (let i = 0; i < 12; i++) {
    const t = root.innerText || '';
    if (t.includes('次へ進む') || t.includes('この内容で採点する')) break;
    if (!root.parentElement) break;
    root = root.parentElement;
  }
  root.setAttribute('data-lt-card', '1');
  return {
    labels: labs.length,
    imgs: root.querySelectorAll('img').length,
    srcs: [...root.querySelectorAll('img')].map((i) => i.currentSrc || i.src),
  };
}"""


def mark_quiz_card(page):
    """設問カード（問題文＋選択肢）に目印を付け、画像の有無を返す。"""
    try:
        return page.evaluate(CARD_JS)
    except Exception:
        return None


def choose_index(answer, count):
    """AI の回答から選択肢の番号（0 始まり）を取り出す。"""
    if not answer:
        return None
    found = re.search(r"[1-9]", answer)
    if not found:
        return None
    index = int(found.group(0)) - 1
    return index if index < count else None


def cached_image_answer(text, options, srcs, prompt, pngs):
    key = "img|" + answer_cache_key(text, options) + "|" + "|".join(sorted(srcs))
    cache = read_answer_cache()
    hit = cache.get(key)
    if hit:
        return hit
    answer = get_ai_response_with_images(prompt, pngs)
    if answer:
        cache[key] = answer
        write_answer_cache(cache)
    return answer


def answer_question(page):
    """問題を解いて選択肢をクリックする。問題が無ければ None。"""
    text = question_text(page)
    if text is None:
        return None

    options = collect_options(page)
    if not options:
        log("選択肢を取得できませんでした。")
        return text

    card = mark_quiz_card(page)
    chosen = None
    intentional_error = False
    if card and card["imgs"]:
        # 問題文にコードや図の画像が付いていると、テキストだけでは解けない。
        # 設問カードを丸ごと画像にして AI に読ませ、選択肢の番号で受け取る。
        try:
            png = page.locator("[data-lt-card='1']").screenshot()
        except Exception:
            png = None
        if png:
            labels = "\n".join(f"{i + 1}. {o['text']}" for i, o in enumerate(options))
            prompt = (
                "画像は選択肢クイズの画面です。問題文と、問題に付いている画像（コードなど）と"
                "選択肢が写っています。\n"
                f"選択肢（上から順）:\n{labels}\n"
                "正解の選択肢の番号を数字だけで返してください。"
            )
            answer = cached_image_answer(text, options, card["srcs"], prompt, [png])
            index = choose_index(answer, len(options))
            if index is not None:
                chosen = options[index]
                if len(options) > 1 and random.random() < config.WRONG_ANSWER_RATE:
                    chosen = random.choice([o for o in options if o is not chosen])
                    intentional_error = True
    if chosen is None:
        prompt = (
            f"問題: {text}\n"
            f"選択肢: {[o['text'] for o in options]}\n"
            "正解の選択肢のテキストを一つだけ、余計な言葉なしで返してください。"
        )
        answer = cached_answer(text, options, prompt)
        chosen, intentional_error = choose_option(options, answer)
    if chosen is None:
        log("正解を特定できませんでした。次の画面へ進みます。")
        return text
    if intentional_error:
        log(f"意図的に誤答します: {chosen['text']}")
    # 押せたらすぐ次へ進むので、固定の待ちは入れない（反映は wait_selected で見る）。
    click_option(page, chosen)
    return text


def click_option(page, option):
    """選択肢を 1 往復の JS でクリックし、駄目なら通常クリックで押し直す。"""
    try:
        if page.evaluate(SELECT_OPTION_JS, option["index"]):
            return True
    except Exception:
        pass
    try:
        human_click(
            page.locator('input[type="radio"], input[type="checkbox"]').nth(option["index"]),
            base_delay=0,
            timeout_ms=config.ACTION_CLICK_TIMEOUT_MS,
        )
        return True
    except Exception as e:
        log(f"選択肢のクリックに失敗しました: {e}")
        return False


def go_next(page, grade_only_if_ready=False):
    """次へ進む・確認・採点のいずれかを押し、押した種別を返す。

    押したあとの遷移は呼び出し側が条件で待つので、ここでは待たない。
    grade_only_if_ready のときは未回答で押せない採点ボタンを無視する。
    """
    if click_if_present(page, config.SEL_WORK_NEXT_BTN, base_delay=0):
        return "next"
    if click_if_present(page, config.SEL_WORK_CONFIRM_BTN, base_delay=0):
        return "confirm"
    if grade_only_if_ready and not grade_ready(page):
        return None
    if click_if_present(page, config.SEL_WORK_GRADE_BTN, base_delay=0):
        return "grade"
    return None


def has_selection(page):
    """いまの設問で選択肢が選ばれているか。1 往復で判定する。"""
    try:
        return bool(
            page.evaluate(
                "() => !!document.querySelector('input[type=radio]:checked, input[type=checkbox]:checked')"
            )
        )
    except Exception:
        return False


def wait_unselected(page, timeout=None):
    """次の設問が描画されて選択が外れるまで待つ（二重に進めないため）。"""
    end = time.monotonic() + (config.WORK_STEP_TIMEOUT if timeout is None else timeout)
    while time.monotonic() < end:
        if not has_selection(page):
            return True
        time.sleep(config.FAST_POLL_SEC)
    return False


def grade_visible(page):
    return count(page, config.SEL_WORK_GRADE_BTN) > 0


def already_graded(page):
    """採点結果の画面かどうか（もう一度チャレンジするリンクが出ている）。"""
    try:
        return page.evaluate(
            "() => document.body.innerText.includes('このワークをもう一度チャレンジする')"
        )
    except Exception:
        return False


def press_grade(page):
    # 未回答があるとボタンは disabled なので、押せるようになってから押す。
    if not grade_ready(page):
        return False
    if click_if_present(
        page,
        config.SEL_WORK_GRADE_BTN,
        base_delay=0,
        timeout_ms=config.ACTION_CLICK_TIMEOUT_MS,
    ):
        log("採点しました。")
        return True
    return False


def wait_selected(page, timeout=2.0):
    """選択肢のクリックが反映される（input が checked になる）まで待つ。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if page.evaluate("() => !!document.querySelector('label input:checked')"):
                return True
        except Exception:
            pass
        time.sleep(config.FAST_POLL_SEC)
    return False


def wait_work_ready(page, timeout=10.0):
    """チェックワークの設問（または採点ボタン）が描画されるまで待つ。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if grade_ready(page) or (question_text(page) and collect_options(page)):
            return True
        time.sleep(config.FAST_POLL_SEC)
    return False


def question_text(page):
    """いま表示されている問題文（無ければ None）。1 往復で読む。"""
    try:
        locs = page.locator(config.SEL_WORK_QUESTION)
        if locs.count() <= config.WORK_QUESTION_INDEX:
            return None
        text = locs.nth(config.WORK_QUESTION_INDEX).inner_text().strip()
    except Exception:
        return None
    return text or None


def option_texts(page):
    """表示中の選択肢の文言（描画順は問ごとに変わるので比較時は並べ替える）。"""
    return [o["text"] for o in collect_options(page)]


def question_key(page):
    """問題文と選択肢をまとめた識別子。

    描画の途中では「前の問題文のまま選択肢だけ消える」瞬間があるため、
    問題文と選択肢が揃ったときだけ識別子を返す。
    """
    text = question_text(page)
    if text is None:
        return None
    options = option_texts(page)
    if not options:
        return None
    return (text, tuple(sorted(options)))


def wait_question_change(page, key, timeout=None):
    """設問（問題文＋選択肢）が切り替わるか採点ボタンが出るまで待つ。"""
    end = time.monotonic() + (config.WORK_STEP_TIMEOUT if timeout is None else timeout)
    while time.monotonic() < end:
        if grade_ready(page):
            return "grade"
        if question_key(page) != key:
            return "changed"
        time.sleep(config.FAST_POLL_SEC)
    return None


def run_work(page):
    """開いている理解度チェックワークを採点まで進める。採点したら True。

    固定時間は待たず、「採点ボタンが出た」「設問が切り替わった」を
    10ms 間隔で見て、そのまま次の設問へ進む。
    """
    try:
        if already_graded(page):
            log("このチェックワークは採点済みです。")
            return True
        if not wait_work_ready(page, config.LESSON_PAGE_READY_TIMEOUT):
            log("理解度チェックワークの画面が表示されませんでした。")
            return False
        stalled = 0
        for _ in range(config.MAX_WORK_STEPS):
            if grade_ready(page):
                return press_grade(page)

            if has_selection(page):
                # 回答済みなので次の設問へ。押したあとは選択が消える（＝次の設問が
                # 描画された）まで待ち、同じ設問で二重に「次へ進む」を押さない。
                action = go_next(page, grade_only_if_ready=True)
                if action is None:
                    if press_grade(page):
                        return True
                    stalled += 1
                    if stalled >= config.WORK_STALL_LIMIT:
                        log("遷移ボタンが見つからないため終了します。")
                        return False
                    time.sleep(config.FAST_POLL_SEC)
                    continue
                if action == "grade":
                    log("採点しました。")
                    return True
                wait_unselected(page)
                stalled = 0
                continue

            # 未回答。選択肢が揃うまで待ってから選ぶ。
            end = time.monotonic() + config.WORK_STEP_TIMEOUT
            while time.monotonic() < end:
                if grade_ready(page):
                    return press_grade(page)
                if collect_options(page):
                    break
                time.sleep(config.FAST_POLL_SEC)
            if not collect_options(page):
                if press_grade(page):
                    return True
                if grade_state(page)["found"]:
                    log("未解答の問題が残っているため採点できません。")
                    return False
                log("設問を取得できなかったため終了します。")
                return False
            answer_question(page)
            if not wait_selected(page):
                stalled += 1
                if stalled >= config.WORK_STALL_LIMIT:
                    log("選択肢を選べないため終了します。")
                    return False
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
