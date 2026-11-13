"""Life is Tech! 理解度チェックワークの自動解答スクリプト。"""

import json
import os
import random
import re
import sys
import time
from contextlib import suppress

import config
from ai_analyzer import get_ai_response, get_ai_response_with_images
from chrome_session import ChromeSession
from dom import log, normalize
from human_utils import human_click
from instance_guard import acquire as acquire_instance
from poll import poll_true, poll_until


def grade_state(page):
    """採点ボタンの有無と押せるかどうか（全問回答するまで無効）。"""
    state = {"found": False, "enabled": False}
    # 画面遷移の直後は evaluate が失敗する（想定内）。呼び出し側のポーリングで再試行する。
    with suppress(Exception):
        state = page.evaluate(GRADE_STATE_JS, config.GRADE_BTN_TEXT)
    if not isinstance(state, dict):
        state = {"found": False, "enabled": False}
    return state


def grade_ready(page):
    return grade_state(page)["enabled"]


def click_if_present(page, selector, base_delay=1.0, timeout_ms=config.ACTION_CLICK_TIMEOUT_MS):
    locator = page.locator(selector)
    if locator.count() == 0:
        return False
    # disabled を押しに行くと Playwright が待ち時間いっぱい粘るので先に弾く。
    disabled = False
    # 判定できないときは押せる前提で進む（押せなければ human_click 側が失敗する）。
    with suppress(Exception):
        disabled = locator.first.is_disabled()
    if disabled:
        return False
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

# クリックしたあとの状態を必ず読む。無条件に true を返すと click_option が
# 成功と誤認し、human_click へのフォールバックが一度も働かなくなる。
SELECT_OPTION_JS = r"""(index) => {
  const inputs = [...document.querySelectorAll('input[type="radio"], input[type="checkbox"]')];
  const input = inputs[index];
  if (!input) return false;
  const label = input.closest('label');
  (label || input).click();
  if (input.checked) return true;
  if (input.getAttribute('aria-checked') === 'true') return true;
  const marked = (el) => {
    if (!el) return false;
    const cls = el.className || '';
    return /(^|[\s_-])(selected|checked)([\s_-]|$)/i.test(cls);
  };
  return marked(label) || marked(input.parentElement);
}"""

# 「選択肢が選ばれている」の唯一の判定。has_selection / wait_selected で共用する。
SELECTION_JS = r"""() => {
  const inputs = [...document.querySelectorAll('input[type="radio"], input[type="checkbox"]')];
  const marked = (el) => {
    if (!el) return false;
    const cls = el.className || '';
    return /(^|[\s_-])(selected|checked)([\s_-]|$)/i.test(cls);
  };
  return inputs.some(
    (input) =>
      input.checked ||
      input.getAttribute('aria-checked') === 'true' ||
      marked(input.closest('label')) ||
      marked(input.parentElement)
  );
}"""


def collect_options(page):
    """選択肢を index 付きで集める（1 往復）。

    テキストを持たず画像だけの選択肢（コードの正誤を選ぶ問題など）もあるため、
    その場合は img の alt を表示名にし、画像であることを覚えておく。
    """
    raw = []
    # 画面遷移中は evaluate が失敗する（想定内）。呼び出し側のポーリングで再試行する。
    with suppress(Exception):
        raw = page.evaluate(COLLECT_OPTIONS_JS)
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
    except FileNotFoundError:
        # 初回はまだキャッシュが無い（正常）。
        return {}
    except Exception as e:
        log(f"解答キャッシュを読み込めませんでした: {e}")
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
  // 前の問題で付けた目印が残っていると screenshot の対象が二重になる。
  document.querySelectorAll('[data-lt-card]').forEach((el) => el.removeAttribute('data-lt-card'));
  const labs = [...document.querySelectorAll('label')].filter(
    (l) => l.querySelector('input[type="radio"], input[type="checkbox"]')
  );
  if (!labs.length) return null;
  const first = labs[0];
  let root = first;
  let found = false;
  for (let i = 0; i < 12; i++) {
    const t = root.innerText || '';
    if (t.includes('次へ進む') || t.includes('この内容で採点する')) { found = true; break; }
    if (!root.parentElement || root === document.body) break;
    root = root.parentElement;
  }
  // ボタンを含む祖先が見つからない（描画途中など）ときは html/body まで登らず、
  // 選択肢を包む祖先で止める。body を撮ると画面全体になり AI も解けない。
  if (!found || root === document.body || root === document.documentElement) {
    root = first.parentElement || first;
    for (let i = 0; i < 6 && root.parentElement; i++) {
      if (root.querySelectorAll('img').length) break;
      root = root.parentElement;
    }
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
    except Exception as e:
        log(f"設問カードの目印付けに失敗しました: {e}")
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
        except Exception as e:
            png = None
            log(f"設問カードの画像化に失敗しました: {e}")
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
    """選択肢を 1 往復の JS でクリックし、駄目なら通常クリックで押し直す。

    JS は押したあとの選択状態まで確認して返す。反映されていなければ
    （または JS 自体が失敗したら）human_click で押し直す。
    """
    try:
        if page.evaluate(SELECT_OPTION_JS, option["index"]):
            return True
    except Exception as err:
        log(f"JS での選択に失敗したため通常クリックで押し直します: {err}")
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
    selected = False
    # 画面遷移中は evaluate が失敗する（想定内）。呼び出し側のポーリングで再試行する。
    with suppress(Exception):
        selected = bool(page.evaluate(SELECTION_JS))
    return selected


def wait_unselected(page, timeout=None):
    """次の設問が描画されて選択が外れるまで待つ（二重に進めないため）。"""
    return poll_true(
        lambda: not has_selection(page),
        config.WORK_STEP_TIMEOUT if timeout is None else timeout,
    )


def already_graded(page):
    """採点結果の画面かどうか（もう一度チャレンジするリンクが出ている）。"""
    graded = False
    # 読めないときは未採点として進む（呼び出し側が採点ボタンで再判定する）。
    with suppress(Exception):
        graded = bool(
            page.evaluate(
                "() => document.body.innerText.includes('このワークをもう一度チャレンジする')"
            )
        )
    return graded


def press_grade(page, state=None):
    """採点ボタンを押す。state を渡すと grade_state の再取得を省く。"""
    # 未回答があるとボタンは disabled なので、押せるようになってから押す。
    state = grade_state(page) if state is None else state
    if not state["enabled"]:
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


def wait_selected(page, timeout=config.WORK_SELECT_TIMEOUT):
    """選択肢のクリックが反映される（選択済みになる）まで待つ。"""
    return poll_true(lambda: has_selection(page), timeout)


def wait_work_ready(page, timeout=config.WORK_READY_TIMEOUT):
    """チェックワークの設問（または採点ボタン）が描画されるまで待つ。"""
    return poll_true(
        lambda: grade_ready(page) or bool(question_text(page) and collect_options(page)),
        timeout,
    )


def question_text(page):
    """いま表示されている問題文（無ければ None）。1 往復で読む。"""
    text = ""
    # 画面遷移中は要素が無い／読めない（想定内）。呼び出し側のポーリングで再試行する。
    with suppress(Exception):
        locs = page.locator(config.SEL_WORK_QUESTION)
        if locs.count() > config.WORK_QUESTION_INDEX:
            text = locs.nth(config.WORK_QUESTION_INDEX).inner_text().strip()
    return text or None


def run_work(page):
    """開いている理解度チェックワークを採点まで進める。採点したら True。

    固定時間は待たず、「採点ボタンが出た」「設問が切り替わった」を
    10ms 間隔で見て、そのまま次の設問へ進む。
    """
    try:
        if already_graded(page):
            log("このチェックワークは採点済みです。")
            return True
        if not wait_work_ready(page, config.WORK_PAGE_READY_TIMEOUT):
            log("理解度チェックワークの画面が表示されませんでした。")
            return False
        stalled = 0
        for _ in range(config.MAX_WORK_STEPS):
            # grade_state を 1 回だけ引き、判定と採点で同じ結果を使い回す。
            state = grade_state(page)
            if state["enabled"]:
                return press_grade(page, state)

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

            # 未回答。選択肢が揃うまで待ち、揃った選択肢の取得結果をそのまま使う。
            options = poll_until(
                lambda: "grade" if grade_ready(page) else (collect_options(page) or None),
                config.WORK_STEP_TIMEOUT,
            )
            if options == "grade":
                return press_grade(page)
            if not options:
                state = grade_state(page)
                if press_grade(page, state):
                    return True
                if state["found"]:
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
