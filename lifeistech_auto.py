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
    """今のステップ番号。

    プレイヤーはステップ移動を history.pushState で行うため page.url は
    古いままになる。必ずブラウザ側の location から取り、失敗時だけ URL を見る。
    """
    try:
        value = page.evaluate("() => new URLSearchParams(location.search).get('step_id')")
        if value:
            return int(value)
    except Exception:
        pass
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
        image_quiz: !!document.querySelector('%(image_quiz_modal)s'),
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
        "image_quiz_modal": config.SEL_IMAGE_QUIZ_MODAL,
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


def build_image_quiz_answers(scenario):
    """画像選択クイズの正解を、ステップ ID と問題文から引けるようにする。

    correctChoice は choiceLabel（"1" 始まりの番号）なので、選択肢の並び順
    （quizChoices の順）から何番目をクリックするかを決める。
    """
    by_text = {}
    by_step = {}
    for step in scenario.get("steps", []):
        for element in step.get("elementList", []):
            if element.get("elementName") != "ImageSelectQuizElement":
                continue
            correct = element.get("correctChoice")
            if not correct:
                continue
            labels = [str(c.get("choiceLabel")) for c in element.get("quizChoices") or []]
            if not labels:
                continue
            info = {"labels": labels, "correct": str(correct)}
            question = normalize(element.get("quizText", ""))
            if question:
                by_text[question] = info
            by_step[step.get("stepId")] = info
    return {"by_text": by_text, "by_step": by_step}


def build_code_tasks(scenario):
    """コード課題（エディタに入力する問題）の解答をステップ ID から引けるようにする。

    EditorModalElement の content.correct に「どのファイルの何行目に何を書くか」が
    入っているので、それをそのまま利用する。
    """
    tasks = {}
    for step in scenario.get("steps", []):
        for element in step.get("elementList", []):
            if element.get("elementName") != "EditorModalElement":
                continue
            correct = (element.get("content") or {}).get("correct") or {}
            codes = correct.get("codes") or []
            file_name = correct.get("file") or ""
            if codes and file_name:
                tasks[step.get("stepId")] = {"file": file_name, "codes": codes}
    return tasks


def load_scenario_data(page):
    """クイズの正解とコード課題の解答をまとめて読み込む。"""
    try:
        scenario = fetch_scenario(page)
    except Exception as e:
        log(f"シナリオの取得に失敗しました（AI で回答します）: {e}")
        empty = {"by_text": {}, "by_step": {}}
        return {"answers": empty, "image_answers": empty, "codes": {}}
    answers = build_quiz_answers(scenario)
    image_answers = build_image_quiz_answers(scenario)
    codes = build_code_tasks(scenario)
    log(
        f"クイズの正解データを {len(answers['by_step'])} 件、"
        f"画像選択クイズを {len(image_answers['by_step'])} 件、"
        f"コード課題を {len(codes)} 件読み込みました。"
    )
    return {"answers": answers, "image_answers": image_answers, "codes": codes}


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


def quiz_open(page):
    """通常クイズ・画像選択クイズのどちらかが開いているか。"""
    return count(page, config.SEL_QUIZ_MODAL) + count(page, config.SEL_IMAGE_QUIZ_MODAL) > 0


def wait_for_quiz_change(page, prev_question, timeout=2.0):
    """回答後、モーダルが閉じるか次の問題に切り替わるまで待つ。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not quiz_open(page):
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


def close_quiz(page, prev_question):
    """Enter を送ってクイズを閉じる。

    正解表示のウィンドウとモーダル本体の 2 段階で閉じることがあり、
    Enter 1 回では閉じきらない。短い間隔で数回送って確実に閉じる。
    """
    for _ in range(3):
        press_enter(page)
        if wait_for_quiz_change(page, prev_question, timeout=0.8):
            return True
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
        if not close_quiz(page, key[1]):
            log(f"クイズが閉じませんでした（step {step}）。次の周回で再試行します。")
        return

    # 回答済み（正解表示中）。正解表示が出ていることを確認してから Enter で閉じる。
    answered_steps.add(key)
    wait_for_feedback(page, timeout=1.0)
    if not close_quiz(page, key[1]):
        log(f"クイズが閉じませんでした（step {step}）。次の周回で再試行します。")


def handle_image_quiz(page, image_answers, answered_steps):
    """画像から選ぶクイズに答える。

    選択肢は画像だけでテキストを持たないため、シナリオの quizChoices の並びと
    correctChoice（choiceLabel）からクリックする位置を決める。
    """
    step = current_step(page)
    question = read_quiz_question(page)
    key = (step, normalize(question))

    if not has_chosen(page):
        info = image_answers["by_text"].get(key[1]) or image_answers["by_step"].get(step)
        if not info:
            log(f"画像選択クイズの正解を特定できませんでした（step {step}）。")
            human_sleep(0.2)
            return
        try:
            index = info["labels"].index(info["correct"])
        except ValueError:
            log(f"画像選択クイズの選択肢番号が不正です（step {step}）。")
            human_sleep(0.2)
            return

        buttons = page.locator(config.SEL_IMAGE_QUIZ_CHOICE)
        if buttons.count() <= index:
            log(f"画像選択クイズの選択肢が見つかりません（step {step}）。")
            human_sleep(0.2)
            return
        try:
            human_click(buttons.nth(index), base_delay=0)
        except Exception as e:
            log(f"画像選択クイズのクリックに失敗しました: {e}")
            return
        if not wait_for_chosen(page):
            log(f"画像選択クイズのクリックが反映されませんでした（step {step}）。")
            return

        answered_steps.add(key)
        log(f"画像選択クイズに回答しました（{info['correct']} 番目）。")
        if not wait_for_feedback(page):
            log(f"正解表示を確認できませんでした（step {step}）。Enter を送ります。")
        if not close_quiz(page, key[1]):
            log(f"画像選択クイズが閉じませんでした（step {step}）。次の周回で再試行します。")
        return

    answered_steps.add(key)
    wait_for_feedback(page, timeout=1.0)
    if not close_quiz(page, key[1]):
        log(f"画像選択クイズが閉じませんでした（step {step}）。次の周回で再試行します。")


# --- コード課題 ------------------------------------------------------

# エディタは Ace。解答はシナリオの content.correct.codes（0 始まりの行番号）を使う。
# 検証はサーバー側で、エディタを変更すると自動的に内容が送信されて
# validationResult が返る（success ならステップが進む）。
# ただし editor.setValue() は Ace の入力経路を通らず、React 側のスナップショットが
# 更新されないためサーバー検証が古い内容で走ってしまう。行を選択して
# 実キーボード入力することで、Ace の onChange を正しく発火させる。
FOCUS_CODE_EDITOR_JS = r"""({file, first, last}) => {
    const visible = (el) => !!el && el.offsetParent !== null;
    const isCss = /\.css$/.test(file);
    const find = () => [...document.querySelectorAll('%(editor)s')].find(
        (el) => visible(el) && (isCss ? el.className.includes('css') : el.className.includes('html'))
    );

    let target = find();
    if (!target) {
        const tab = document.querySelector(isCss ? '%(tab_css)s' : '%(tab_html)s');
        if (tab) {
            tab.click();
            target = find();
        }
    }
    if (!target) return {ok: false, reason: 'editor-not-visible'};

    // Ace は要素の env に実インスタンスを持っている（ace.edit() を呼ぶと別の
    // エディタが増殖して内容が壊れるので、必ず env.editor を使う）。
    const editor = (target.env && target.env.editor) || null;
    if (!editor) return {ok: false, reason: 'ace-not-ready'};
    if (editor.getReadOnly()) return {ok: false, reason: 'readonly'};

    editor.focus();
    editor.selection.setRange(
        {start: {row: first, column: 0}, end: {row: last, column: 100000}},
        false,
    );
    const rect = target.getBoundingClientRect();
    return {
        ok: true,
        x: rect.x,
        y: rect.y,
        lineHeight: editor.renderer.lineHeight,
        gutter: editor.renderer.$gutterLayer.gutterWidth,
    };
}"""


def has_code_check(page):
    return count(page, config.SEL_CODE_CHECK_BTN) > 0


SELECT_CODE_LINE_JS = r"""({file, line}) => {
    const isCss = /\.css$/.test(file);
    const el = [...document.querySelectorAll('%(editor)s')].find(
        (e) => e.offsetParent !== null && (isCss ? e.className.includes('css') : e.className.includes('html'))
    );
    if (!el || !el.env || !el.env.editor) return false;
    const editor = el.env.editor;
    // 「{」や「<」を打ったときの自動補完（behaviours）は入力内容を崩すので切る。
    editor.setOptions({behavioursEnabled: false});
    editor.focus();
    editor.selection.setRange(
        {start: {row: line, column: 0}, end: {row: line, column: 100000}},
        false,
    );
    return true;
}"""


def apply_code_task(page, task):
    """コード課題の解答をエディタに入力し、サーバー検証が通るまで待つ。"""
    codes = task.get("codes") or []
    if not codes:
        return False

    file_name = task.get("file", "")
    js = FOCUS_CODE_EDITOR_JS % {
        "editor": config.SEL_CODE_EDITOR,
        "tab_css": config.SEL_CODE_TAB_CSS,
        "tab_html": config.SEL_CODE_TAB_HTML,
    }
    try:
        info = page.evaluate(js, {"file": file_name, "first": 0, "last": 0})
    except Exception as e:
        log(f"エディタの準備に失敗しました: {e}")
        return False
    if not info.get("ok"):
        return False

    before = current_step(page)
    # まずエディタ内をクリックしてフォーカスを移す（focus() だけでは
    # キー入力が届かないことがある）。
    try:
        page.mouse.click(
            info["x"] + info["gutter"] + 30,
            info["y"] + info["lineHeight"] * 1.5,
        )
    except Exception:
        pass

    line_js = SELECT_CODE_LINE_JS % {"editor": config.SEL_CODE_EDITOR}
    try:
        for code in sorted(codes, key=lambda c: c["line"]):
            if not page.evaluate(line_js, {"file": file_name, "line": code["line"]}):
                return False
            sentence = code.get("sentence", "")
            if sentence:
                page.keyboard.type(sentence, delay=15)
            else:
                # 空行は選択を削除して空にする。
                page.keyboard.press("Delete")
    except Exception as e:
        log(f"エディタへの入力に失敗しました: {e}")
        return False
    finally:
        try:
            page.evaluate(
                """() => {
                    document.querySelectorAll('.ace_editor').forEach((el) => {
                        if (el.env && el.env.editor) {
                            el.env.editor.setOptions({behavioursEnabled: true});
                        }
                    });
                }"""
            )
        except Exception:
            pass

    # 入力すると自動で検証が走り、成功すればステップが進む。
    # 進まない場合だけ「コードチェック」を押して再検証させる。
    deadline = time.monotonic() + config.CODE_APPLY_WAIT
    checked_at = time.monotonic() + config.CODE_APPLY_WAIT / 2
    checked = False
    while time.monotonic() < deadline:
        if current_step(page) != before:
            return True
        if not checked and time.monotonic() >= checked_at:
            checked = True
            click_code_check(page)
        human_sleep(0.2)

    # 判定結果のウィンドウが出ていたら閉じる（閉じないとステップが進まない）。
    close_code_result(page)
    return current_step(page) != before


def click_code_check(page):
    """「コードチェック」を押す。押せなくても例外は出さない。"""
    button = page.locator(config.SEL_CODE_CHECK_BTN).first
    try:
        if button.count() == 0:
            return False
        button.evaluate("(el) => el.click()")
        return True
    except Exception:
        return False


def close_code_result(page, timeout=3.0):
    """コードチェック後に出る結果ウィンドウを閉じる。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        link = page.locator(config.SEL_CODE_RESULT_CLOSE).first
        try:
            if link.count() > 0 and link.is_visible():
                link.evaluate("(el) => el.click()")
                return True
        except Exception:
            pass
        human_sleep(0.2)
    return False


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


def run_lesson(page):
    """開いているレッスンページを完了まで進める。完了したら True を返す。

    auto_runner.py からは既にプレイヤーを開いたページを渡す。
    """
    try:
        return _run_lesson_loop(page)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        log(f"エラーが発生したため終了します: {e}")
        return False


def _run_lesson_loop(page):
    page.set_default_timeout(config.ACTION_TIMEOUT_MS)
    state = read_state(page)
    if not state["in_lesson"]:
        log("レッスンページが開かれていません。ブラウザでレッスンを開いてから実行してください。")
        return False

    data = load_scenario_data(page)
    answers = data["answers"]
    image_answers = data["image_answers"]
    code_tasks = data["codes"]
    answered_steps = set()
    clicked_action_steps = set()
    code_attempts = {}
    last_step = state["step"]
    last_change = time.monotonic()
    locked_since = None
    log(f"ステップ {last_step} から開始します。")

    while True:
        try:
            state = read_state(page)
        except Exception as e:
            # レッスン完了時は SPA 遷移で実行コンテキストが破棄される。
            if "Execution context was destroyed" in str(e):
                log("レッスン画面が遷移したため終了します（完了）。")
                return True
            raise
        if not state["in_lesson"]:
            log("レッスンページを離れたため終了します。")
            return True

        # ステップ番号はシナリオ JSON（int）と突き合わせるので int にそろえる。
        step = int(state["step"]) if state["step"] else None
        now = time.monotonic()
        if step != last_step:
            log(f"ステップ {step} を通過しました。")
            last_step = step
            last_change = now

        if state["quiz"] or state["image_quiz"]:
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
            if state["image_quiz"]:
                handle_image_quiz(page, image_answers, answered_steps)
            else:
                handle_quiz(page, answers, answered_steps)
            continue
        locked_since = None

        # コード課題: エディタに解答を入れて「コードチェック」を押す。
        if step and step in code_tasks and has_code_check(page):
            tries = code_attempts.get(step, 0)
            if tries < config.CODE_MAX_ATTEMPTS:
                code_attempts[step] = tries + 1
                log(f"コード課題に解答します（step {step}・{tries + 1} 回目）。")
                if apply_code_task(page, code_tasks[step]):
                    last_change = time.monotonic()
                human_sleep(config.CODE_CHECK_INTERVAL)
                continue
            if tries == config.CODE_MAX_ATTEMPTS:
                code_attempts[step] = tries + 1
                log(f"コード課題が通りません（step {step}）。手動確認してください。")

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
            return False

        human_sleep(config.ENTER_SPAM_DELAY)


def auto_lesson():
    log("--- レッスン自動化を実行します（Enter 連打モード） ---")
    try:
        with ChromeSession(config.LESSON_PAGE_MARKER) as page:
            run_lesson(page)
    except KeyboardInterrupt:
        log("処理を中断しました。")


if __name__ == "__main__":
    auto_lesson()
