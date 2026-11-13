"""Life is Tech! レッスン画面の自動進行スクリプト。

プレイヤー (player.lifeistech-lesson.jp/player/step) は React 製で、
ボタンは <button> ではなく div とクラス名で描画される。
クイズの正解はシナリオ JSON から取得し、取得できない場合のみ Gemini に問い合わせる。
基本は Enter キー連打で進め、クイズと進行ボタンだけ個別に処理する。
"""

import json
import os
import re
import sys
import time

import config
import ui
from ai_analyzer import get_ai_response
from chrome_session import ChromeSession
from human_utils import human_click, human_sleep
from instance_guard import acquire as acquire_instance


def log(message):
    # 色分けは ui 側で行う（rich が無い環境では素の print にフォールバック）。
    ui.log(message)


def count(page, selector):
    try:
        return page.locator(selector).count()
    except Exception as e:
        # Chrome 側が落ちただけなのに「要素なし」と取り違えない。
        text = str(e)
        if "Target closed" in text or "has been closed" in text:
            raise
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


def wait_for_step_change(page, before, timeout):
    """ステップが before から変わるまで最大 timeout 秒待つ。変われば True。

    固定 sleep ではなく、変わった瞬間に戻る条件待ち。timeout は上限としてだけ使う。
    """
    deadline = time.monotonic() + timeout
    while True:
        if current_step(page) != before:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(config.FAST_POLL_SEC)


def step_key(step):
    """シナリオ JSON と DOM のステップ番号を int にそろえる。"""
    try:
        return int(step)
    except (TypeError, ValueError):
        return None


# --- ページ状態の一括取得 ---------------------------------------------

READ_STATE_JS = r"""() => {
    // offsetParent は position:fixed の要素で null になるため、矩形と算出スタイルで見る。
    const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        const style = getComputedStyle(el);
        // opacity は演出中に 0 付近を通過するので判定に使わない。
        return style.visibility !== 'hidden' && style.display !== 'none';
    };
    // innerText は要素ごとにレイアウトを走らせるので textContent を使う。
    const textOf = (el) => (el.textContent || '').trim().replace(/\s+/g, ' ');
    const action = [...document.querySelectorAll('%(action)s')].find(visible) || null;
    // クイズの選択肢が「disabled のみ・chosen なし」で固まっていないかを見る。
    // 正解表示より前に Enter を送るとこの状態で固まり、Enter では復帰できない。
    const quizButtons = [...document.querySelectorAll('%(quiz_button)s')];
    const chosen = quizButtons.some((el) => (el.className || '').indexOf('chosen') !== -1);
    const locked = quizButtons.length > 0 && quizButtons.every(
        (el) => (el.className || '').indexOf('%(quiz_disabled)s') !== -1
    );
    const quizTextEl = document.querySelector('%(quiz_text)s');
    return {
        in_lesson: location.pathname.indexOf('%(marker)s') !== -1,
        step: new URLSearchParams(location.search).get('step_id'),
        quiz: !!document.querySelector('%(quiz_modal)s'),
        image_quiz: !!document.querySelector('%(image_quiz_modal)s'),
        // クイズの判定に必要な情報も同じ 1 回で取る（往復を減らす）。
        quiz_question: quizTextEl ? textOf(quizTextEl) : '',
        quiz_choices: quizButtons.map(textOf),
        image_quiz_count: document.querySelectorAll('%(image_quiz_choice)s').length,
        action: action ? textOf(action) : null,
        quiz_chosen: chosen,
        quiz_locked: locked && !chosen,
        // ステップ所要時間の内訳を測るための目印（出現した時刻を記録する）。
        talk: !!document.querySelector('.fukidashi'),
        editor: !!document.querySelector('%(code_editor)s'),
        element_count: document.querySelectorAll('.ozm-element').length,
    };
}"""


def read_state(page):
    """URL・クイズ・進行ボタンの状態を 1 回の evaluate でまとめて取る。"""
    js = READ_STATE_JS % {
        "action": config.SEL_LESSON_ACTION_BTN,
        "marker": config.LESSON_PAGE_MARKER,
        "quiz_modal": config.SEL_QUIZ_MODAL,
        "image_quiz_modal": config.SEL_IMAGE_QUIZ_MODAL,
        "quiz_button": config.SEL_QUIZ_BUTTON,
        "quiz_text": config.SEL_QUIZ_TEXT,
        "image_quiz_choice": config.SEL_IMAGE_QUIZ_CHOICE,
        "quiz_disabled": config.QUIZ_DISABLED_CLASS,
        "code_editor": config.SEL_CODE_EDITOR,
    }
    return page.evaluate(js)


# --- アニメーション待ちのスキップ -------------------------------------

# プレイヤーは各ステップの終了条件に「AnimationFinish」を持つことが多く、
# 実際には div.motion の CSS トランジション（既定 900ms）の完了を待っている。
# Enter では縮められないため、transitionend を擬似的に発火させて待ちを消す。
SKIP_ANIMATION_JS = r"""() => {
    // sizeAndPosition のキーは left/top/width/height/transform だけではなく
    // color なども含まれるため、広めに撒いて取りこぼしを無くす。
    const props = [
        'opacity', 'left', 'top', 'width', 'height', 'transform', 'color',
        'background', 'background-color', 'border-color', 'box-shadow', 'filter',
        'font-size', 'line-height', 'margin', 'padding', 'visibility', 'z-index',
    ];
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
    """マウント済みのモーション要素に擬似 transitionend を送る。

    要素はステップごとに描画が遅れて出ることもあるため、毎回送る。
    ただし発火しすぎると React の再描画が無駄に走るので、
    呼び出し側で SKIP_ANIMATION_INTERVAL 秒以上の間隔を空けて使う。
    """
    if not config.SKIP_ANIMATION_WAIT:
        return 0
    try:
        return page.evaluate(SKIP_ANIMATION_JS)
    except Exception:
        return 0


# --- シナリオ JSON（クイズの正解データ） -------------------------------


FETCH_SCENARIO_JS = """async () => {
    const q = new URL(location.href).searchParams;
    const url = '%(path)s'
        + '?project_name=' + encodeURIComponent(q.get('project_name') || '')
        + '&scenario_path=' + encodeURIComponent(q.get('scenario_path') || '')
        + '&scenario_id=' + encodeURIComponent(q.get('scenario_id') || '');
    const response = await fetch(url, {headers: {'Accept': 'application/json'}});
    if (!response.ok) throw new Error('status ' + response.status);
    return await response.text();
}"""


def fetch_scenario(page):
    """プレイヤーが裏で使っているシナリオ JSON を同じセッションで取得する。"""
    js = FETCH_SCENARIO_JS % {"path": config.SCENARIO_API_PATH}
    return json.loads(page.evaluate(js))


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
            by_step[step_key(step.get("stepId"))] = correct
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
            by_step[step_key(step.get("stepId"))] = info
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
            # 入力する課題はサーバー検証（ValidationFinish）が付いている。
            # 付いていないものは「お手本のコードを見せるだけ」のステップなので
            # 入力しない（読み取り専用のエディタを掴んで待つだけになる）。
            if "ValidationFinish" not in json.dumps(
                step.get("exitCondition") or {}, ensure_ascii=False
            ):
                continue
            correct = (element.get("content") or {}).get("correct") or {}
            codes = correct.get("codes") or []
            file_name = correct.get("file") or ""
            if codes and file_name:
                tasks[step_key(step.get("stepId"))] = {"file": file_name, "codes": codes}
    return tasks


# --- プレビュー型の課題（iframe 内のアプリを操作する） -----------------

# onafterchanges の中は
#   x === '0' && y === '0' && newValue === '=0.3-0.2'
# や
#   String(x) === '1' && String(y) === '2' &&
#   newValue.toString().toUpperCase().replace(/\s+/g, '') === '=C2*E2/100'
# のような比較が並んでいるだけなので、セル座標と入力値をそのまま読む。
PREVIEW_X_RE = re.compile(r"x\s*\)?\s*===?\s*['\"]?(\d+)")
PREVIEW_Y_RE = re.compile(r"y\s*\)?\s*===?\s*['\"]?(\d+)")
PREVIEW_VALUE_RE = re.compile(
    r"newValue[\s\S]{0,160}?(?:===|==)\s*(?:'([^']*)'|\"([^\"]*)\"|(-?\d+(?:\.\d+)?))"
)

PREVIEW_HANDLER_JS = r"""() => {
    const opts = window.options;
    if (!opts) return null;
    for (const key of ['onafterchanges', 'onchange', 'onupdate']) {
        if (typeof opts[key] === 'function') return opts[key].toString();
    }
    return null;
}"""


def build_preview_tasks(scenario):
    """プレビュー（iframe 内アプリ）の完了で次へ進むステップを集める。

    どのセルに何を入れるかはシナリオ側に無く、プレビューアプリの
    成功条件（onafterchanges）に書かれているため、実行時に読む。
    """
    steps = set()
    for step in scenario.get("steps", []):
        condition = step.get("exitCondition") or {}
        details = condition.get("detailList") or []
        if isinstance(details, str):
            details = [details]
        if any("PreviewStatus" in str(detail) for detail in details):
            steps.add(step_key(step.get("stepId")))
    return steps


CLICK_CONDITION_RE = re.compile(r"^Click:(.+)$")


def build_free_input_tasks(scenario):
    """ヒントを見て自分でコードを完成させるステップを集める。

    「コードチェック」形式（EditorModalElement に正解が入っている）ではなく、
    シナリオに正解が無い ValidationFinish のステップ。ヒントの HTML を読んで
    組み立てる必要があるため、現状は自動解答できない。
    """
    steps = set()
    for step in scenario.get("steps", []):
        condition = step.get("exitCondition") or {}
        details = condition.get("detailList") or []
        if isinstance(details, str):
            details = [details]
        if not any(str(detail).startswith("ValidationFinish") for detail in details):
            continue
        answer = None
        for element in step.get("elementList", []):
            if element.get("elementName") == "EditorModalElement":
                answer = (element.get("content") or {}).get("correct")
        if not answer:
            steps.add(step_key(step.get("stepId")))
    return steps


def build_step_structure(scenario):
    """ステップごとの「構造」（終了条件の種類・要素の種類・セリフ文字数）を集める。

    所要時間と突き合わせて「どの構造のステップが遅いか」を測るためのデータで、
    実行時の制御には使わない。
    """
    structure = {}
    for step in scenario.get("steps", []):
        condition = step.get("exitCondition") or {}
        details = condition.get("detailList") or []
        if isinstance(details, str):
            details = [details]
        exit_types = []
        for detail in details:
            text = str(detail)
            kind = text.split(":", 1)[0]
            if kind not in exit_types:
                exit_types.append(kind)
        elements = []
        talk_chars = 0
        for element in step.get("elementList", []):
            name = str(element.get("elementName") or "")
            if name and name not in elements:
                elements.append(name)
            talk = element.get("talkDetail") or {}
            for line in talk.get("textList") or []:
                talk_chars += len(str(line))
        structure[step_key(step.get("stepId"))] = {
            "exit": exit_types,
            "elements": elements,
            "talk_chars": talk_chars,
        }
    return structure


def build_click_tasks(scenario):
    """「この要素をクリックすると次へ進む」ステップを集める。

    exitCondition の Click:<domId> は、シナリオの elementList にある
    ButtonElement の domId を指す。DOM 上に id / class がそのまま出るわけでは
    ないため、要素定義から実際の見た目（buttonCssClass と文言）を引いておく。
    例: {"elementName": "ButtonElement", "domId": "confirm-button",
         "text": "Next", "buttonCssClass": "btn-next"}
    """
    steps = {}
    for step in scenario.get("steps", []):
        condition = step.get("exitCondition") or {}
        details = condition.get("detailList") or []
        if isinstance(details, str):
            details = [details]
        targets = []
        for detail in details:
            match = CLICK_CONDITION_RE.match(str(detail))
            if match:
                targets.append(match.group(1).strip())
        if not targets:
            continue
        buttons = {}
        for element in step.get("elementList") or []:
            if element.get("elementName") != "ButtonElement":
                continue
            buttons[str(element.get("domId") or "")] = {
                "text": str(element.get("text") or "").strip(),
                "css": str(element.get("buttonCssClass") or "btn-next").strip(),
            }
        resolved = []
        for target in targets:
            info = buttons.get(target)
            if info:
                cls = info["css"].split()[0] if info["css"] else "btn-next"
                resolved.append({
                    "domId": target,
                    "css": "button." + cls,
                    "text": info["text"],
                })
            resolved.append({"domId": target, "css": "#" + target, "text": ""})
            resolved.append({"domId": target, "css": "." + target, "text": ""})
            resolved.append({"domId": target, "css": '[data-dom-id="' + target + '"]', "text": ""})
        if "confirm-button" in targets:
            resolved.append({"domId": "confirm-button", "css": config.SEL_CONFIRM_BTN, "text": ""})
        steps[step_key(step.get("stepId"))] = resolved
    return steps


# 1 往復で「押せる要素がもう出ているか」を調べる（locator を何度も照会しない）。
CLICK_FIND_JS = r"""({targets}) => {
    const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        const style = getComputedStyle(el);
        return style.visibility !== 'hidden' && style.display !== 'none';
    };
    for (let i = 0; i < targets.length; i += 1) {
        const t = targets[i];
        let nodes;
        try {
            nodes = document.querySelectorAll(t.css);
        } catch (e) {
            continue;
        }
        for (let j = 0; j < nodes.length; j += 1) {
            const el = nodes[j];
            if (!visible(el)) continue;
            if (t.text && (el.textContent || '').indexOf(t.text) === -1) continue;
            return { index: i, nth: j };
        }
    }
    return null;
}"""


def click_step(page, targets, wait_sec=None):
    """Click:<domId> の要素を押す。

    ステップに入った直後はまだ描画されていないことがある（OK ボタンは約 2 秒後）
    ため、confirm-button だけは config.CLICK_WAIT_SEC まで待つ。それ以外は
    取りこぼしても通常の進行（Enter / 進行ボタン）で進めるので短く打ち切る。
    """
    if not targets:
        return False
    deadline = time.monotonic() + (
        (config.CLICK_WAIT_SEC if _needs_render_wait(targets) else config.CLICK_SHORT_WAIT_SEC)
        if wait_sec is None
        else wait_sec
    )
    found = None
    while True:
        try:
            found = page.evaluate(CLICK_FIND_JS, {"targets": targets})
        except Exception:
            found = None
        if found or time.monotonic() >= deadline:
            break
        time.sleep(config.FAST_POLL_SEC)
    if not found:
        names = ", ".join(t.get("domId", "?") for t in targets)
        log(f"クリックできる要素が見つかりませんでした: {names}")
        return False
    target = targets[found["index"]]
    try:
        locator = page.locator(target["css"]).nth(found["nth"])
        human_click(locator, base_delay=0, timeout_ms=config.ACTION_CLICK_TIMEOUT_MS)
    except Exception as e:
        # モーダルに覆われている等で実クリックできない場合は JS クリックで代替する。
        try:
            handle = page.locator(target["css"]).nth(found["nth"])
            handle.evaluate("(el) => el.click()")
        except Exception as inner:
            log(f"「{target['css']}」をクリックできませんでした: {e} / {inner}")
            return False
    label = clean_label(target.get("text") or target.get("domId") or target["css"])
    log(f"「{label}」をクリックしました。")
    return True


def _needs_render_wait(targets):
    """描画が遅れて出る要素（OK ボタン）を含むか。"""
    return any(t.get("domId") == "confirm-button" for t in targets)


def parse_preview_rules(source):
    """プレビューの成功条件から「セル座標と入力値」を取り出す。

    x / y / newValue の比較が 1 組ずつ並ぶ想定で、x の直後の y、
    その直後の newValue 比較を 1 組として読む（複数セルの条件にも対応）。
    """
    if not source:
        return []
    rules = []
    seen = set()
    for x_match in PREVIEW_X_RE.finditer(source):
        window = source[x_match.end() : x_match.end() + 400]
        y_match = PREVIEW_Y_RE.search(window)
        if not y_match:
            continue
        rest = window[y_match.end() :]
        value_match = PREVIEW_VALUE_RE.search(rest)
        if not value_match:
            continue
        value = value_match.group(1) or value_match.group(2) or value_match.group(3)
        x = int(x_match.group(1))
        y = int(y_match.group(1))
        if (x, y, value) in seen:
            continue
        seen.add((x, y, value))
        rules.append({"x": x, "y": y, "value": value})
    return rules


def preview_frame(page, timeout=None):
    """プレビュー（iframe）のフレームを返す。無ければ None。"""
    end = time.monotonic() + (config.PREVIEW_READY_TIMEOUT if timeout is None else timeout)
    while time.monotonic() < end:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            if "html_playground" in (frame.url or ""):
                return frame
        try:
            element = page.locator(config.SEL_PREVIEW_IFRAME).first
            if element.count():
                handle = element.element_handle()
                if handle is not None:
                    frame = handle.content_frame()
                    if frame is not None:
                        return frame
        except Exception:
            pass
        time.sleep(config.FAST_POLL_SEC)
    return None


PREVIEW_SCRIPT_JS = ("() => Array.from(document.scripts)"
                     ".map((s) => s.src || s.textContent).join('\\n')")

# プレビュー内のスプライト（p5.play）の位置と画像の対応を返す。
PREVIEW_SPRITES_JS = r"""() => {
    const scripts = Array.from(document.scripts).map((s) => s.textContent || '').join('\n');
    const images = {};
    const loadRe = /(\w+)\s*=\s*loadImage\(\s*['"]([^'"]+)['"]\s*\)/g;
    let m;
    while ((m = loadRe.exec(scripts)) !== null) images[m[1]] = m[2];
    const names = [];
    const spriteRe = /(\w+)\s*=\s*createSprite\(/g;
    while ((m = spriteRe.exec(scripts)) !== null) names.push(m[1]);
    const stem = (path) => {
        if (!path) return null;
        const file = path.split('/').pop().replace(/\.\w+$/, '');
        return file.split('_')[0];
    };
    const sprites = [];
    for (const name of names) {
        const sprite = window[name];
        if (!sprite || !sprite.position) continue;
        sprites.push({
            name,
            x: sprite.position.x,
            y: sprite.position.y,
            stem: stem(images[name + 'Image']),
        });
    }
    const canvas = document.querySelector('canvas');
    if (!canvas) return {sprites: [], canvas: null};
    const rect = canvas.getBoundingClientRect();
    return {
        sprites,
        canvas: {width: canvas.width, height: canvas.height,
                 left: rect.left, top: rect.top,
                 scaleX: rect.width / canvas.width,
                 scaleY: rect.height / canvas.height},
    };
}"""


def preview_script_text(frame):
    try:
        return frame.evaluate(PREVIEW_SCRIPT_JS) or ""
    except Exception:
        return ""


def _preview_click(frame, selector):
    frame.locator(selector).first.click(timeout=2_000)
    time.sleep(config.FAST_POLL_SEC)


def solve_preview_interaction(frame, page, script):
    """表計算以外のプレビュー（スクロール・クリック・ドラッグ）を解く。"""
    if not script or "sendSuccess" not in script:
        return False

    # 1) スクロールさせる課題: 最下部まで送れば送信される。
    scrolls = (
        "$(window).scroll" in script
        or "addEventListener('scroll'" in script
        or 'addEventListener("scroll"' in script
    )
    if scrolls:
        try:
            frame.evaluate("() => window.scrollTo(0, 0)")
            for _ in range(60):
                frame.evaluate("() => window.scrollBy(0, 160)")
                time.sleep(config.FAST_POLL_SEC)
            return True
        except Exception as e:
            log(f"プレビューのスクロールに失敗しました: {e}")
            return False

    # 2) 決まった順番にクリックする課題: ans = [0, 2, 1] が正解の並び。
    answer = re.search(r"ans\s*=\s*\[([0-9,\s]+)\]", script)
    if answer and "some-item" in script:
        try:
            order = [int(x) for x in answer.group(1).split(",") if x.strip()]
            for index in order:
                frame.locator("li.some-item").nth(index).click(timeout=2_000)
                time.sleep(config.FAST_POLL_SEC)
            return True
        except Exception as e:
            log(f"プレビューの順番クリックに失敗しました: {e}")
            return False

    # 3) 正解の要素をクリックする課題: sendSuccess を呼ぶハンドラのセレクタ。
    for match in re.finditer(
        r"\$\(\s*['\"]([^'\"]+)['\"]\s*\)\.on\(\s*['\"]click['\"][^{]*\{([^}]*)\}",
        script,
        re.S,
    ):
        selector, body = match.group(1), match.group(2)
        if "sendSuccess" in body:
            try:
                _preview_click(frame, selector)
                return True
            except Exception as e:
                log(f"プレビューのクリックに失敗しました（{selector}）: {e}")
                return False

    # 4) ドラッグ＆ドロップの課題: 同じ画像名のスプライト同士を重ねる。
    if "createSprite" in script:
        return drag_preview_sprites(frame, page)
    return False


# iframe 内でマウスイベントを発生させる（ページ側のマウス操作は iframe に届かない）。
# p5.play のスプライトをシルエットの位置へ重ねる。
# 実際のマウス操作では iframe 内の p5 にイベントが届かないため、
# スプライトの座標を直接合わせる（重なり判定は draw ループが行う）。
PREVIEW_SNAP_JS = r"""async (pairs) => {
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    for (const [from, to] of pairs) {
        const sprite = window[from];
        const target = window[to];
        if (!sprite || !target || !sprite.position || !target.position) return false;
        sprite.position.x = target.position.x;
        sprite.position.y = target.position.y;
        await sleep(500);
    }
    return true;
}"""


def drag_preview_sprites(frame, page):
    """p5.play の図形を、対応するシルエットの位置へ重ねる。"""
    try:
        info = frame.evaluate(PREVIEW_SPRITES_JS)
    except Exception as e:
        log(f"プレビューのスプライト情報を取得できませんでした: {e}")
        return False
    sprites = info.get("sprites") or []
    if not sprites:
        return False

    targets = {}
    for sprite in sprites:
        if not sprite.get("stem"):
            continue
        if sprite["name"].startswith("silhouette"):
            targets.setdefault(sprite["stem"], sprite)
    pairs = []
    for sprite in sprites:
        if sprite["name"].startswith("silhouette"):
            continue
        target = targets.get(sprite.get("stem"))
        if target:
            pairs.append([sprite["name"], target["name"]])
    if not pairs:
        return False

    try:
        frame.evaluate(PREVIEW_SNAP_JS, pairs)
    except Exception as e:
        log(f"プレビューの図形を動かせませんでした: {e}")
        return False
    log(f"プレビューで {len(pairs)} 個の図形をシルエットに合わせました。")
    return True
def apply_preview_task(page):
    """プレビューの成功条件を読み、対象セルへ入力する。

    (入力できたか, この形式を扱えるか) を返す。扱えない形式なら呼び出し側は
    通常の Enter 連打に任せる（誤って止めない）。
    """
    # iframe は作られた直後まだスクリプトが動いていないことがあるので、
    # 成功条件が読めるまで少し待つ。
    end = time.monotonic() + config.PREVIEW_READY_TIMEOUT
    frame = None
    rules = []
    while time.monotonic() < end:
        frame = preview_frame(page, timeout=1.0)
        if frame is None:
            continue
        try:
            rules = parse_preview_rules(frame.evaluate(PREVIEW_HANDLER_JS))
        except Exception:
            rules = []
        if rules:
            break
        time.sleep(config.FAST_POLL_SEC)
    if frame is None:
        log("プレビューのフレームが見つかりませんでした。")
        return False, True
    if not rules:
        script = preview_script_text(frame)
        if solve_preview_interaction(frame, page, script):
            return True, True
        log("プレビューの成功条件を特定できませんでした（未対応の課題形式）。")
        return False, False
    applied = 0
    for rule in rules:
        selector = 'td[data-x="%d"][data-y="%d"]' % (rule["x"], rule["y"])
        try:
            cell = frame.locator(selector).first
            cell.dblclick(timeout=2_000)
            time.sleep(config.FAST_POLL_SEC)
            editor = frame.locator(selector + " input").first
            editor.fill(rule["value"], timeout=2_000)
            editor.press("Enter")
            applied += 1
            time.sleep(config.FAST_POLL_SEC)
        except Exception as e:
            log(f"セルへの入力に失敗しました（{selector}）: {e}")
    return applied > 0, True


def load_scenario_data(page):
    """クイズ・画像選択クイズ・コード課題・プレビュー課題をまとめて読み込む。"""
    try:
        scenario = fetch_scenario(page)
    except Exception as e:
        log(f"シナリオの取得に失敗しました（AI で回答します）: {e}")
        empty = {"by_text": {}, "by_step": {}}
        return {
            "answers": empty,
            "image_answers": empty,
            "codes": {},
            "previews": set(),
            "clicks": {},
            "free_inputs": set(),
            "structure": {},
        }
    # 実行時のシナリオを保存しておくと、ステップログの解析に使える
    # （[…STEP_LOG] のときだけ書く。失敗しても無視する）。
    step_log_env = os.environ.get("LIFEISTECH_STEP_LOG")
    if step_log_env:
        try:
            target = "/tmp/lifeistech_scenario_last.json"
            if step_log_env != "1":
                target = step_log_env + ".scenario.json"
            with open(target, "w", encoding="utf-8") as f:
                json.dump(scenario, f, ensure_ascii=False)
        except OSError:
            pass
    answers = build_quiz_answers(scenario)
    image_answers = build_image_quiz_answers(scenario)
    codes = build_code_tasks(scenario)
    previews = build_preview_tasks(scenario)
    clicks = build_click_tasks(scenario)
    free_inputs = build_free_input_tasks(scenario)
    structure = build_step_structure(scenario)
    log(
        f"クイズの正解データを {len(answers['by_step'])} 件、"
        f"画像選択クイズを {len(image_answers['by_step'])} 件、"
        f"コード課題を {len(codes)} 件、"
        f"プレビュー課題を {len(previews)} 件、"
        f"クリック課題を {len(clicks)} 件、"
        f"自由入力のコード課題を {len(free_inputs)} 件読み込みました。"
    )
    return {
        "answers": answers,
        "image_answers": image_answers,
        "codes": codes,
        "previews": previews,
        "clicks": clicks,
        "free_inputs": free_inputs,
        "structure": structure,
    }


# --- クイズ ----------------------------------------------------------


def press_enter(page):
    """Enter を 1 回送る。クイズの正解表示を閉じて次へ進むのにも使う。"""
    try:
        page.keyboard.press("Enter")
    except Exception as e:
        log(f"Enter キーを送信できませんでした: {e}")


def find_choice_index(labels, correct):
    """選択肢テキストの並びから、正解の位置（0 始まり）を返す。"""
    if not correct:
        return None
    wanted = normalize(correct)
    for index, label in enumerate(labels):
        if normalize(label) == wanted:
            return index
    for index, label in enumerate(labels):
        normalized = normalize(label)
        if normalized and (wanted in normalized or normalized in wanted):
            return index
    return None


# locator を経由せず 1 往復でクリックする（毎回 3〜4 往復していたのを削減）。
CLICK_NTH_JS = """([selector, index]) => {
    const el = [...document.querySelectorAll(selector)][index];
    if (!el) return false;
    el.click();
    return true;
}"""


CLICK_VISIBLE_JS = r"""([selector, text, disabledClass]) => {
    const visible = (el) => {
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        const style = getComputedStyle(el);
        return style.visibility !== 'hidden' && style.display !== 'none';
    };
    const els = [...document.querySelectorAll(selector)].filter(visible);
    const el = text ? els.find((e) => (e.textContent || '').includes(text)) : els[0];
    if (!el) return null;
    if (disabledClass && (el.className || '').includes(disabledClass)) return null;
    const label = (el.textContent || '').trim().replace(/\s+/g, ' ');
    el.click();
    return label;
}"""


def click_visible(page, selector, text=None, disabled_class=None):
    """見えているボタンを 1 往復でクリックし、押したボタンの文言を返す。"""
    try:
        return page.evaluate(CLICK_VISIBLE_JS, [selector, text, disabled_class])
    except Exception:
        return None


def click_nth(page, selector, index):
    if index is None or index < 0:
        return False
    try:
        return bool(page.evaluate(CLICK_NTH_JS, [selector, index]))
    except Exception:
        return False


def ask_ai(question, options):
    prompt = (
        f"クイズ: {question}\n"
        f"選択肢: {options}\n"
        "正解の選択肢のテキストを一つだけ、余計な言葉なしで返してください。"
    )
    return get_ai_response(prompt)


def has_chosen(page):
    return count(page, config.SEL_QUIZ_BUTTON + ".chosen") > 0


def wait_for_chosen(page, timeout=1.0):
    """クリックが実際に反映された（chosen が付いた）ことを確認する。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if has_chosen(page):
            return True
        time.sleep(config.FAST_POLL_SEC)
    return False


def quiz_open(page):
    """通常クイズ・画像選択クイズのどちらかが開いているか。"""
    return count(page, config.SEL_QUIZ_MODAL) + count(page, config.SEL_IMAGE_QUIZ_MODAL) > 0


# body 全体の正規表現だと問題文に「正解」を含むだけで誤検知する。
# 「短い要素のテキストがフィードバック文言で始まる」ものだけを見る。
HAS_FEEDBACK_JS = r"""() => {
    // innerText は要素ごとにレイアウトを走らせるので重い。textContent で十分。
    const pattern = /^(正解|不正解|惜しい|おしい|正答|誤答|回答済み)/;
    for (const el of document.querySelectorAll('body *')) {
        if (el.closest('%(quiz_text)s')) continue;
        if (el.children.length > 3) continue;
        if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') continue;
        const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
        if (!text || text.length > %(limit)d) continue;
        if (pattern.test(text)) return true;
    }
    return false;
}"""


def has_feedback(page):
    try:
        js = HAS_FEEDBACK_JS % {
            "quiz_text": config.SEL_QUIZ_TEXT,
            "limit": config.FEEDBACK_MAX_LENGTH,
        }
        return bool(page.evaluate(js))
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
        time.sleep(config.FAST_POLL_SEC)
    return False


# クイズが閉じたか（モーダルが消えたか）を 1 往復で見る。
QUIZ_CLOSE_JS = r"""() => {
    const open = !!(document.querySelector('%(quiz)s') || document.querySelector('%(image_quiz)s'));
    const text = document.querySelector('%(quiz_text)s');
    return {open: open, question: text ? (text.textContent || '').trim() : ''};
}"""


def close_quiz(page, prev_question, timeout=None):
    """クイズが閉じるまで Enter を送り続ける。

    正解表示のウィンドウとモーダル本体の 2 段階で閉じるため Enter 1 回では
    閉じきらない。固定間隔で待たず、閉じたかを 1 往復の JS で見ながら
    閉じた瞬間に戻る（押しても回答済みのモーダルは disabled なので、
    誤って別の選択肢を選ぶことはない）。
    """
    deadline = time.monotonic() + (config.QUIZ_CLOSE_TIMEOUT if timeout is None else timeout)
    js = QUIZ_CLOSE_JS % {
        "quiz": config.SEL_QUIZ_MODAL,
        "image_quiz": config.SEL_IMAGE_QUIZ_MODAL,
        "quiz_text": config.SEL_QUIZ_TEXT,
    }
    while True:
        press_enter(page)
        # 1 回の Enter が処理されるまで見て、閉じたら即戻る。
        # 閉じなければ次の Enter を送る（押し過ぎても回答済みなので安全）。
        probe_until = min(deadline, time.monotonic() + config.QUIZ_ENTER_INTERVAL)
        while True:
            try:
                state = page.evaluate(js) or {}
            except Exception:
                state = {}
            if not state.get("open"):
                return True
            if normalize(state.get("question") or "") != normalize(prev_question or ""):
                return True
            if time.monotonic() >= probe_until:
                break
            time.sleep(config.FAST_POLL_SEC)
        if time.monotonic() >= deadline:
            return False


PROFILE = bool(os.environ.get("LIFEISTECH_PROFILE"))


def profile_log(message):
    if PROFILE:
        log(f"[profile] {message}")


def handle_quiz(page, answers, state=None):
    """クイズが表示されていれば正解の選択肢をクリックして Enter で閉じる。

    回答済みかどうかは「chosen クラスが付いているか」で判定する。
    ステップ ID だけの記録だと、次の問題が同じ状態で開いたときに
    「もう答えた」と誤判定して無視してしまうため。
    """
    _started = time.perf_counter()
    state = state or read_state(page)
    step = int(state["step"]) if state.get("step") else current_step(page)
    question = state.get("quiz_question") or ""
    key = (step, normalize(question))

    if not state.get("quiz_chosen"):
        # まだ答えていない問題。選択肢を選ぶところからやり直す。
        labels = state.get("quiz_choices") or []
        if not labels:
            # まだ描画されていない。次の周回（数 ms 後）で読み直す。
            return

        correct = answers["by_text"].get(key[1]) or answers["by_step"].get(step)
        index = find_choice_index(labels, correct)
        if index is None:
            index = find_choice_index(labels, ask_ai(question, labels))
        if index is None:
            log(f"クイズの正解を特定できませんでした（step {step}）。次の周回で再試行します。")
            return

        # まず JS クリック（1 往復）。反映されなければ実クリックで押し直す。
        click_nth(page, config.SEL_QUIZ_BUTTON, index)
        if not wait_for_chosen(page, timeout=0.25):
            try:
                human_click(
                    page.locator(config.SEL_QUIZ_BUTTON).nth(index),
                    base_delay=0,
                    timeout_ms=config.ACTION_CLICK_TIMEOUT_MS,
                )
            except Exception as e:
                log(f"選択肢のクリックに失敗しました: {e}")
                return
            if not wait_for_chosen(page):
                log(f"選択肢のクリックが反映されませんでした（step {step}）。再試行します。")
                return

        log(f"クイズに回答しました: {labels[index]}")
        _chosen = time.perf_counter()
        if not wait_for_feedback(page):
            log(f"正解表示を確認できませんでした（step {step}）。Enter を送ります。")
        _feedback = time.perf_counter()
        if not close_quiz(page, key[1]):
            log(f"クイズが閉じませんでした（step {step}）。次の周回で再試行します。")
        profile_log(
            f"クイズ処理 {time.perf_counter() - _started:.2f} 秒（step {step}・選択"
            f" {_chosen - _started:.2f} / 正解待ち {_feedback - _chosen:.2f} /"
            f" 閉じ {time.perf_counter() - _feedback:.2f}）"
        )
        return

    # 回答済み（正解表示中）。正解表示が出ていることを確認してから Enter で閉じる。
    wait_for_feedback(page, timeout=1.0)
    if not close_quiz(page, key[1]):
        log(f"クイズが閉じませんでした（step {step}）。次の周回で再試行します。")


def handle_image_quiz(page, image_answers, state=None):
    """画像から選ぶクイズに答える。

    選択肢は画像だけでテキストを持たないため、シナリオの quizChoices の並びと
    correctChoice（choiceLabel）からクリックする位置を決める。
    """
    _started = time.perf_counter()
    state = state or read_state(page)
    step = int(state["step"]) if state.get("step") else current_step(page)
    question = state.get("quiz_question") or ""
    key = (step, normalize(question))

    if not state.get("quiz_chosen"):
        info = image_answers["by_text"].get(key[1]) or image_answers["by_step"].get(step)
        if not info:
            log(f"画像選択クイズの正解を特定できませんでした（step {step}）。")
            return
        try:
            index = info["labels"].index(info["correct"])
        except ValueError:
            log(f"画像選択クイズの選択肢番号が不正です（step {step}）。")
            return

        if (state.get("image_quiz_count") or 0) <= index:
            log(f"画像選択クイズの選択肢が見つかりません（step {step}）。")
            return
        click_nth(page, config.SEL_IMAGE_QUIZ_CHOICE, index)
        if not wait_for_chosen(page, timeout=0.25):
            try:
                human_click(
                    page.locator(config.SEL_IMAGE_QUIZ_CHOICE).nth(index),
                    base_delay=0,
                    timeout_ms=config.ACTION_CLICK_TIMEOUT_MS,
                )
            except Exception as e:
                log(f"画像選択クイズのクリックに失敗しました: {e}")
                return
            if not wait_for_chosen(page):
                log(f"画像選択クイズのクリックが反映されませんでした（step {step}）。")
                return

        log(f"画像選択クイズに回答しました（{index + 1} 番目）。")
        _chosen = time.perf_counter()
        if not wait_for_feedback(page):
            log(f"正解表示を確認できませんでした（step {step}）。Enter を送ります。")
        _feedback = time.perf_counter()
        if not close_quiz(page, key[1]):
            log(f"画像選択クイズが閉じませんでした（step {step}）。次の周回で再試行します。")
        profile_log(
            f"画像クイズ処理 {time.perf_counter() - _started:.2f} 秒（step {step}・選択"
            f" {_chosen - _started:.2f} / 正解待ち {_feedback - _chosen:.2f} /"
            f" 閉じ {time.perf_counter() - _feedback:.2f}）"
        )
        return

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
    // offsetParent は position:fixed の要素で null になるため、矩形と算出スタイルで見る。
    const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        const style = getComputedStyle(el);
        // opacity は演出中に 0 付近を通過するので判定に使わない。
        return style.visibility !== 'hidden' && style.display !== 'none';
    };
    // 拡張子から Ace のモード名（エディタのクラス名）を決める。
    const ext = (file.split('.').pop() || '').toLowerCase();
    const modeOf = {css: 'css', html: 'html', py: 'python', js: 'javascript', json: 'json'}[ext] || ext;
    const findAll = () => [...document.querySelectorAll('%(editor)s')].filter((el) => visible(el));
    const matches = () => findAll().filter((el) => el.className.includes(modeOf));
    const usable = () => findAll().filter((el) => {
        const ed = el.env && el.env.editor;
        return ed && !ed.getReadOnly();
    });

    let target = matches()[0] || null;
    if (!target) {
        // タブが切り替わっていないだけかもしれないので、タブを押してから探し直す。
        const tab = document.querySelector('li.label-text.' + modeOf);
        if (tab) {
            tab.click();
            target = matches()[0] || null;
        }
    }
    // それでも見つからなければ、読み取り専用でない可視エディタを使う。
    if (!target) target = usable()[0] || null;
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
    const visible = (e) => {
        // offsetParent は position:fixed の要素で null になるので矩形で見る。
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0
            && getComputedStyle(e).visibility !== 'hidden';
    };
    // 拡張子からエディタのモード名（クラス名）を決める（html / css / python ...）。
    const ext = (file.split('.').pop() || '').toLowerCase();
    const modeOf = {css: 'css', html: 'html', py: 'python', js: 'javascript', json: 'json'}[ext] || ext;
    const all = [...document.querySelectorAll('%(editor)s')].filter(visible);
    const el = all.find((e) => e.className.includes(modeOf))
        || all.find((e) => e.env && e.env.editor && !e.env.editor.getReadOnly());
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


REPLACE_CODE_JS = r"""({file, text}) => {
    const visible = (e) => {
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0
            && getComputedStyle(e).visibility !== 'hidden';
    };
    const ext = (file.split('.').pop() || '').toLowerCase();
    const modeOf = {css: 'css', html: 'html', py: 'python', js: 'javascript', json: 'json'}[ext] || ext;
    const all = [...document.querySelectorAll('%(editor)s')].filter(visible);
    const el = all.find((e) => e.className.includes(modeOf))
        || all.find((e) => e.env && e.env.editor && !e.env.editor.getReadOnly());
    if (!el || !el.env || !el.env.editor) return {ok: false, reason: 'editor-not-found'};
    const editor = el.env.editor;
    if (editor.getReadOnly()) return {ok: false, reason: 'readonly'};
    // 「:」や「{」の自動インデントで行がずれるため、置換でも無効にしておく。
    editor.setOptions({behavioursEnabled: false});
    editor.focus();

    // Range クラスの入手経路は環境によって変わる（window.ace が無いビルドもある）。
    let Range = null;
    try {
        Range = (window.ace && window.ace.require) ? window.ace.require('ace/range').Range : null;
    } catch (e) { Range = null; }
    if (Range) {
        editor.session.replace(new Range(0, 0, editor.session.getLength(), 0), text);
        return {ok: true, via: 'range'};
    }
    // Range が取れないときは setValue（Ace の change イベントは発火する）。
    editor.setValue(text, -1);
    editor.clearSelection();
    return {ok: true, via: 'setValue'};
}"""


def replace_code_whole(page, file_name, codes):
    """空行やインデントを含むコードを、エディタ全体の置換で一気に入れる。

    1 行ずつ選択して打つ方式は、空行の削除や自動インデントが絡むと行が
    ずれて崩れる（重複行ができる）。行番号が 0 から連続している解答なら
    全体を置換したほうが確実。Ace の変更イベントは置換でも発火する。
    """
    lines = sorted(codes, key=lambda c: c["line"])
    if [c["line"] for c in lines] != list(range(len(lines))):
        return False
    text = "\n".join(c.get("sentence") or "" for c in lines)
    try:
        result = page.evaluate(
            REPLACE_CODE_JS % {"editor": config.SEL_CODE_EDITOR},
            {"file": file_name, "text": text},
        )
    except Exception as e:
        log(f"エディタ全体の置換に失敗しました: {e}")
        return False
    if isinstance(result, dict) and result.get("ok"):
        return True
    reason = result.get("reason") if isinstance(result, dict) else result
    log(f"エディタ全体の置換ができませんでした（{reason}）。1 行ずつ入力します。")
    return False


VERIFY_CODE_JS = r"""({file, expected}) => {
    const visible = (e) => {
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0
            && getComputedStyle(e).visibility !== 'hidden';
    };
    const ext = (file.split('.').pop() || '').toLowerCase();
    const modeOf = {css: 'css', html: 'html', py: 'python', js: 'javascript', json: 'json'}[ext] || ext;
    const all = [...document.querySelectorAll('%(editor)s')].filter(visible);
    const el = all.find((e) => e.className.includes(modeOf))
        || all.find((e) => e.env && e.env.editor && !e.env.editor.getReadOnly());
    if (!el || !el.env || !el.env.editor) return null;
    const session = el.env.editor.session;
    return expected.map(({line, text}) => ({
        line,
        ok: (session.getLine(line) || '').trim() === (text || '').trim(),
    }));
}"""


def verify_code_applied(page, file_name, codes):
    """エディタの行内容が解答どおりか確かめる。食い違った行番号を返す。"""
    expected = [
        {"line": code["line"], "text": (code.get("sentence") or "").strip()}
        for code in codes
    ]
    try:
        result = page.evaluate(
            VERIFY_CODE_JS % {"editor": config.SEL_CODE_EDITOR},
            {"file": file_name, "expected": expected},
        )
    except Exception as e:
        log(f"エディタ内容の確認に失敗しました: {e}")
        return [code["line"] for code in codes]
    if not result:
        return [code["line"] for code in codes]
    return [item["line"] for item in result if not item["ok"]]


def apply_code_task(page, task):
    """コード課題の解答をエディタに入力し、サーバー検証が通るまで待つ。"""
    started = time.perf_counter()
    codes = task.get("codes") or []
    if not codes:
        return False

    file_name = task.get("file", "")
    js = FOCUS_CODE_EDITOR_JS % {"editor": config.SEL_CODE_EDITOR}
    # ステップ遷移直後はエディタがまだマウント中で操作できないことがある。
    deadline = time.monotonic() + config.CODE_EDITOR_READY_TIMEOUT
    # 読み取り専用のエディタしか無いステップは入力する課題ではないので、
    # 短めに見切りをつける（それでも遷移直後に書き込み可能なエディタへ
    # 差し替わることがあるため、すぐには諦めない）。
    readonly_deadline = None
    reason = None
    while True:
        try:
            info = page.evaluate(js, {"file": file_name, "first": 0, "last": 0})
        except Exception as e:
            log(f"エディタの準備に失敗しました: {e}")
            return False
        if info.get("ok"):
            break
        reason = info.get("reason")
        now = time.monotonic()
        if reason == "readonly" and readonly_deadline is None:
            readonly_deadline = now + 2.0
        limit = deadline
        if readonly_deadline is not None:
            limit = min(limit, readonly_deadline)
        if now >= limit:
            if reason != "readonly":
                log(f"エディタを操作できる状態になりませんでした（{reason}）。")
            return False
        time.sleep(config.FAST_POLL_SEC)

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

    # Python のように空行・インデントを含む解答は、全体置換で入れる。
    # 1 行ずつ打つ方式だと空行の削除や自動インデントで行がずれる。
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    replaced = False
    if ext in ("py", "js"):
        replaced = replace_code_whole(page, file_name, codes)

    if not replaced:
        line_js = SELECT_CODE_LINE_JS % {"editor": config.SEL_CODE_EDITOR}
        try:
            for code in sorted(codes, key=lambda c: c["line"]):
                if not page.evaluate(line_js, {"file": file_name, "line": code["line"]}):
                    return False
                sentence = code.get("sentence", "")
                if sentence:
                    try:
                        # insert_text は 1 文字ずつのキーイベントを挟まないので速い。
                        # Ace は textarea の input イベントで受け取るためそのまま入る。
                        page.keyboard.insert_text(sentence)
                    except Exception:
                        page.keyboard.type(sentence, delay=0)
                else:
                    # 空行は選択を削除して空にする。
                    page.keyboard.press("Delete")
        except Exception as e:
            log(f"エディタへの入力に失敗しました: {e}")
    # 自動補完は他の課題でも使うので戻しておく。
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

    if config.CODE_VERIFY_APPLIED:
        mismatch = verify_code_applied(page, file_name, codes)
        if mismatch:
            log(f"エディタの内容が解答と一致しません（行: {', '.join(map(str, mismatch))}）。")
            return False

    staged = time.perf_counter()

    # 入力すると自動で検証が走り、成功すればステップが進む。
    # 進まない場合だけ「コードチェック」を押して再検証させる。
    # 待ち時間は最短にして、押して駄目ならすぐ次の手（実行）に移る。
    if wait_for_step_change(page, before, config.CODE_AUTO_VALIDATE_WAIT):
        profile_log(
            f"コード課題 step {before}: 自動検証で通過 入力後 {time.perf_counter() - staged:.2f} 秒"
            f" / 合計 {time.perf_counter() - started:.2f} 秒"
        )
        return True

    checked_at = time.perf_counter()
    checked = click_code_check(page)
    # コードチェックを押すと判定結果のウィンドウが出る。開いている間はステップが
    # 進まないので、開いた時点で閉じ、閉じたら「実行」を押す。閉じたあとも進まない
    # レッスンがあるため、ステップ番号・ウィンドウ・実行ボタンを 1 往復で見ながら
    # 必要な操作だけを最短で行う。
    wait_js = CODE_WAIT_JS % {
        "close": config.SEL_CODE_RESULT_CLOSE,
        "run": config.SEL_CODE_RUN_BTN,
    }
    closed = False
    ran = False
    deadline = checked_at + config.CODE_CHECK_WAIT
    while True:
        try:
            state = page.evaluate(wait_js) or {}
        except Exception:
            state = {}
        step = step_key(state.get("step"))
        if step is not None and step != before:
            profile_log(
                f"コード課題 step {before}: コードチェックで通過 入力後 {time.perf_counter() - staged:.2f} 秒"
                f" / 合計 {time.perf_counter() - started:.2f} 秒"
            )
            return True
        if state.get("modal") and not closed:
            close_code_result(page, timeout=0.5)
            closed = True
            profile_log(
                f"コード課題 step {before}: 結果ウィンドウ "
                f"{time.perf_counter() - checked_at:.2f} 秒で閉じました"
            )
            continue
        if closed and not ran and state.get("run"):
            ran = click_run_button(page, timeout=0.1)
            continue
        if time.perf_counter() >= deadline:
            break
        time.sleep(config.FAST_POLL_SEC)

    profile_log(
        f"コード課題 step {before}: 進みませんでした（コードチェック={checked} / 結果を閉じた={closed} / 実行={ran}）"
    )
    return current_step(page) != before


def click_run_button(page, timeout=1.5):
    """エディタ横の「実行」を押す。無いレッスンでは何もしない。

    結果ウィンドウを閉じた直後は、まだボタンが隠れていることがあるので
    表示されるまで少し待つ。
    """
    deadline = time.monotonic() + timeout
    while True:
        button = page.locator(config.SEL_CODE_RUN_BTN).first
        try:
            if button.count() > 0 and button.is_visible():
                button.evaluate("(el) => el.click()")
                log("「実行」ボタンを押しました。")
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(config.FAST_POLL_SEC)


# ステップ番号と「結果ウィンドウが開いているか」を 1 往復で取る。
CODE_WAIT_JS = r"""() => {
  const step = new URLSearchParams(location.search).get('step_id');
  const visible = (selector) => {
    const el = document.querySelector(selector);
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  return {
    step: step,
    modal: visible('%(close)s'),
    run: visible('%(run)s'),
  };
}"""


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
        time.sleep(config.FAST_POLL_SEC)
    return False


# --- 画面遷移 --------------------------------------------------------


def clean_label(text):
    """ボタン文言はアニメーション用に二重描画されることがある。

    「次へ 次へ」のような全体の二重化だけを畳み、「はい はい」のような
    正当な繰り返しは残す。
    """
    parts = text.split()
    half = len(parts) // 2
    if half and len(parts) % 2 == 0 and parts[:half] == parts[half:]:
        return " ".join(parts[:half])
    return " ".join(parts)


def press_confirm_if_visible(page):
    """黄色い「OK」（confirm-button）が画面に出ていれば押す。

    シナリオの exitCondition に Click が書かれていないステップでも
    OK 待ちになることがあるため、詰まったときの保険として使う。
    """
    try:
        button = page.locator(config.SEL_CONFIRM_BTN).first
        if not button.count() or not button.is_visible():
            return False
        try:
            button.evaluate("(el) => el.click()")
        except Exception:
            return False
        log("「OK」ボタンを押しました。")
        return True
    except Exception:
        return False


def click_action_button(page):
    """スライドの「Next」などの進行ボタンを 1 回押す。

    Enter 連打と並行して動くため、押しに行く前にステップが進んで
    ボタンが消えていることがある。長いタイムアウトで待つと 10 秒固まるので、
    短いタイムアウトで諦め、それでも押せない場合は JS クリックで試す。
    """
    label = click_visible(page, config.SEL_LESSON_ACTION_BTN)
    if label:
        log(f"「{clean_label(label)}」ボタンを押しました。")
        return True
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


ANIMATION_KILL_JS = r"""() => {
    if (document.getElementById('lifeistech-no-anim')) return false;
    const style = document.createElement('style');
    style.id = 'lifeistech-no-anim';
    style.textContent = [
        '*, *::before, *::after {',
        '  transition-duration: 0.001s !important;',
        '  transition-delay: 0s !important;',
        '  animation-duration: 0.001s !important;',
        '  animation-delay: 0s !important;',
        '}',
    ].join('\n');
    document.head.appendChild(style);
    return true;
}"""


def install_animation_killer(page):
    """演出（CSS アニメーション）を潰す style を差し込む。

    プレイヤーは要素の入れ替えに TransitionGroup の fade を使っていて、
    演出が終わるまで次のステップに進めない。0 秒にすると transitionend が
    発火しなくなり、かえって止まるので 1ms 残している。
    """
    try:
        if page.evaluate(ANIMATION_KILL_JS):
            log("演出（CSS アニメーション）を無効化しました。")
    except Exception:
        pass


TIMER_CLAMP_JS = r"""() => {
    if (window.__lifeistechTimerClamped) return false;
    window.__lifeistechTimerClamped = true;
    const cap = %(cap)d;
    const raw = window.setTimeout;
    window.setTimeout = function (fn, delay, ...args) {
        if (typeof delay === 'number' && delay >= %(trigger)d) {
            delay = cap;
        }
        return raw.call(this, fn, delay, ...args);
    };
    return true;
}""" % {"trigger": config.LONG_TIMER_TRIGGER_MS, "cap": config.LONG_TIMER_CAP_MS}


def install_timer_clamp(page):
    """長い setTimeout 待ち（シナリオの waitMilliseconds 等）を短縮する。

    ステップの exitBehaviourList に waitMilliseconds: 8000 が指定されている
    ことがあり（例: 04_02_01 の最終ステップ）、その間は無操作で待つしかない。
    1 秒以上の待ちだけを 200ms に短縮する。
    """
    try:
        if page.evaluate(TIMER_CLAMP_JS):
            log("長い待ち時間（setTimeout）を短縮しました。")
    except Exception:
        pass


def reload_page(page, reason="クイズが応答しない"):
    """壊れたクイズモーダルから復帰するため、ページを再読込する。"""
    log(f"{reason}ためページを再読込します。")
    try:
        page.reload(wait_until="domcontentloaded")
        return True
    except Exception as e:
        log(f"ページの再読込に失敗しました: {e}")
        return False


def lesson_finished(page):
    """レッスンの完了画面に到達したか。単なる離脱やエラー遷移と区別する。"""
    try:
        url = page.evaluate("() => location.href") or ""
    except Exception:
        try:
            url = page.url or ""
        except Exception:
            url = ""
    lowered = url.lower()
    return any(marker in lowered for marker in config.FINISH_URL_MARKERS)


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


def write_step_log(path, step, duration, loops, marks, structure, handled):
    """1 ステップ分の計測結果を JSON Lines で追記する（速度チューニング用）。

    marks は「そのステップが始まってから吹き出し・進行ボタン・クイズ・
    エディタが初めて現れるまでの秒数」。handled は自動処理の種類。
    実行時の挙動には影響しない。
    """
    record = {
        "step": step,
        "sec": round(duration, 3),
        "loops": loops,
        "marks": marks,
        "handled": sorted(handled),
        "exit": (structure or {}).get("exit"),
        "elements": (structure or {}).get("elements"),
        "talk_chars": (structure or {}).get("talk_chars"),
    }
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def log_finish_speed(started_at, passed_steps):
    """終了時に「何ステップを何秒で進んだか」を出す（速度チューニング用）。"""
    elapsed = time.monotonic() - started_at
    log("レッスン終了画面に到達しました。")
    if passed_steps:
        log(
            f"ステップ {passed_steps} 件を {elapsed:.1f} 秒で通過しました"
            f"（{elapsed / passed_steps * 1000:.0f} ms/ステップ）。"
        )


def _run_lesson_loop(page):
    page.set_default_timeout(config.ACTION_TIMEOUT_MS)
    state = read_state(page)
    if not state["in_lesson"]:
        log("レッスンページが開かれていません。ブラウザでレッスンを開いてから実行してください。")
        return False

    if config.DISABLE_CSS_ANIMATION:
        install_animation_killer(page)
    if config.SHORTEN_LONG_TIMERS:
        install_timer_clamp(page)

    data = load_scenario_data(page)
    answers = data["answers"]
    image_answers = data["image_answers"]
    code_tasks = data["codes"]
    preview_tasks = data["previews"]
    click_tasks = data["clicks"]
    free_inputs = data["free_inputs"]
    structure = data["structure"]
    # step -> 最後に進行ボタンを押した時刻（同じステップで押し直せるようにする）。
    clicked_action_steps = {}
    confirm_pressed = set()
    clicked_steps = set()
    click_attempts = {}
    code_attempts = {}
    preview_attempts = {}
    preview_unsupported = set()
    quiz_reloads = {}
    last_step = state["step"]
    last_change = time.monotonic()
    locked_since = None
    quiz_key = None
    quiz_since = None
    log(f"ステップ {last_step} から開始します。")
    started_at = time.monotonic()
    passed_steps = 0
    # LIFEISTECH_PROFILE=1 で 100 周ごとに内訳を出す（速度チューニング用）。
    profile = bool(os.environ.get("LIFEISTECH_PROFILE"))
    prof = {"read": 0.0, "skip": 0.0, "enter": 0.0, "action": 0.0, "count": 0}
    skip_state = {"t": 0.0}
    # LIFEISTECH_CALIB=1 で Enter 間隔を切り替えながら ms/ステップを測る（調整用）。
    calib_delays = [0.0, 0.005, 0.01, 0.02, 0.04] if os.environ.get("LIFEISTECH_CALIB") == "1" else None
    calib_index = 0
    calib_block_started = time.monotonic()
    calib_block_steps = 0

    def reload_and_reset(reason):
        """再読込して、ステップ依存の記録とシナリオの対応表を作り直す。"""
        nonlocal data, answers, image_answers, code_tasks, preview_tasks, click_tasks
        nonlocal free_inputs, structure
        nonlocal last_change, locked_since, quiz_key, quiz_since, last_step
        if not reload_page(page, reason):
            return False
        clicked_action_steps.clear()
        confirm_pressed.clear()
        clicked_steps.clear()
        click_attempts.clear()
        code_attempts.clear()
        preview_attempts.clear()
        locked_since = None
        quiz_key = None
        quiz_since = None
        # 再読込直後はプレイヤーの描画を待つ。固定 sleep ではなく、
        # レッスンページとして読めるようになった瞬間に先へ進む。
        ready_deadline = time.monotonic() + config.LESSON_PAGE_READY_TIMEOUT
        while time.monotonic() < ready_deadline:
            try:
                if read_state(page)["in_lesson"]:
                    break
            except Exception:
                pass
            time.sleep(config.NAV_POLL_SEC)
        if config.DISABLE_CSS_ANIMATION:
            install_animation_killer(page)
        if config.SHORTEN_LONG_TIMERS:
            install_timer_clamp(page)
        # 再読込で別シナリオに移っていることがあるので取り直す。
        data = load_scenario_data(page)
        answers = data["answers"]
        image_answers = data["image_answers"]
        code_tasks = data["codes"]
        preview_tasks = data["previews"]
        click_tasks = data["clicks"]
        free_inputs = data["free_inputs"]
        structure = data["structure"]
        try:
            last_step = read_state(page)["step"]
        except Exception:
            last_step = None
        last_change = time.monotonic()
        return True

    # LIFEISTECH_STEP_LOG=1 で 1 ステップごとの所要時間と構造を JSON Lines に残す。
    step_log_path = os.environ.get("LIFEISTECH_STEP_LOG")
    if step_log_path == "1":
        step_log_path = "/tmp/lifeistech_steps.jsonl"

    step_started = time.monotonic()
    step_loops = 0
    step_number = last_step
    step_marks = {}
    step_handled = set()
    while True:
        _t0 = time.perf_counter()
        step_loops += 1
        try:
            state = read_state(page)
        except Exception as e:
            # レッスン完了時は SPA 遷移で実行コンテキストが破棄される。
            if "Execution context was destroyed" in str(e):
                # SPA 遷移中。完了画面に変わるまで条件待ちする。
                finish_deadline = time.monotonic() + config.LESSON_PAGE_READY_TIMEOUT
                while not lesson_finished(page) and time.monotonic() < finish_deadline:
                    time.sleep(config.NAV_POLL_SEC)
                if lesson_finished(page):
                    log_finish_speed(started_at, passed_steps)
                    return True
                log("ページが遷移しましたが、終了画面を確認できませんでした。")
                return False
            raise
        prof["read"] += time.perf_counter() - _t0
        prof["count"] += 1
        if profile and prof["count"] % 100 == 0:
            _wall = time.monotonic() - started_at
            log(
                f"[profile] {prof['count']} 周 / {_wall:.1f} 秒"
                f"（1 周 {_wall / prof['count'] * 1000:.0f} ms・"
                f"read {prof['read'] / prof['count'] * 1000:.1f} /"
                f" skip {prof['skip'] / prof['count'] * 1000:.1f} /"
                f" enter {prof['enter'] / prof['count'] * 1000:.1f} /"
                f" action {prof['action'] / prof['count'] * 1000:.1f} ms）"
            )
        if not state["in_lesson"]:
            if lesson_finished(page):
                log_finish_speed(started_at, passed_steps)
                return True
            log("レッスンページを離れたため終了します（完了画面は未確認）。")
            return False

        # ステップ番号はシナリオ JSON（int）と突き合わせるので int にそろえる。
        step = int(state["step"]) if state["step"] else None
        now = time.monotonic()
        if step != last_step:
            log(f"ステップ {step} を通過しました。")
            profile_log(
                f"step {last_step} → {step}: {now - step_started:.3f} 秒 / {step_loops} 周"
            )
            if step_log_path:
                write_step_log(
                    step_log_path,
                    step_number,
                    now - step_started,
                    step_loops,
                    step_marks,
                    structure.get(step_number),
                    step_handled,
                )
                step_number = step
                step_marks = {}
                step_handled = set()
            step_started = now
            step_loops = 0
            passed_steps += 1
            if calib_delays and passed_steps - calib_block_steps >= 60:
                _dt = now - calib_block_started
                _n = passed_steps - calib_block_steps
                log(
                    f"[calib] Enter 間隔 {calib_delays[calib_index] * 1000:.0f} ms →"
                    f" {_n} ステップ / {_dt:.1f} 秒 = {_dt / _n * 1000:.0f} ms/ステップ"
                )
                calib_index = (calib_index + 1) % len(calib_delays)
                calib_block_started = now
                calib_block_steps = passed_steps
            last_step = step
            last_change = now

        # 観測用: そのステップで吹き出し・ボタン・クイズ・エディタが現れた時刻。
        if step_log_path:
            if state["talk"]:
                step_marks.setdefault("talk", round(now - step_started, 3))
            if state["action"]:
                step_marks.setdefault("button", round(now - step_started, 3))
            if state["quiz"] or state["image_quiz"]:
                step_marks.setdefault("quiz", round(now - step_started, 3))
            if state["editor"]:
                step_marks.setdefault("editor", round(now - step_started, 3))

        if state["quiz"] or state["image_quiz"]:
            # 「disabled のみ・chosen なし」で固まったモーダルは Enter では
            # 復帰できないため、一定時間で再読込して立て直す。
            if state["quiz_locked"]:
                locked_since = locked_since or now
                if now - locked_since > config.BROKEN_QUIZ_WAIT:
                    locked_since = None
                    if reload_and_reset("壊れたクイズ"):
                        continue
            else:
                locked_since = None
            # 同じ問題のまま進まないクイズは、Enter 連打では解決しない。
            # 一定時間で諦めて再読込し、それでも駄目なら停止する。
            key = (step, normalize(state.get("quiz_question") or ""))
            if key != quiz_key:
                quiz_key = key
                quiz_since = now
            elif quiz_since is not None and now - quiz_since > config.QUIZ_GIVEUP_AFTER:
                tries = quiz_reloads.get(step, 0) + 1
                quiz_reloads[step] = tries
                if tries > config.QUIZ_MAX_RELOADS:
                    log(f"クイズを解けないため停止します（step {step}）。手動で確認してください。")
                    return False
                log(f"クイズが反応しないため再読込します（step {step}・{tries} 回目）。")
                if not reload_and_reset("クイズが応答しない"):
                    return False
                continue

            step_handled.add("quiz")
            if state["image_quiz"]:
                handle_image_quiz(page, image_answers, state)
            else:
                handle_quiz(page, answers, state)
            continue
        locked_since = None
        quiz_key = None
        quiz_since = None

        # ヒントを読んで自分で書く課題は、正解がシナリオに無いので自動解答できない。
        # ここで早めに止めないと 30 秒×2 待ってから諦めることになる。
        if step and step in free_inputs:
            log(f"自由入力のコード課題のため自動解答できません（step {step}）。")
            return False

        # コード課題: エディタに解答を入れて「コードチェック」を押す。
        if step and step in code_tasks and has_code_check(page):
            tries = code_attempts.get(step, 0)
            if tries < config.CODE_MAX_ATTEMPTS:
                code_attempts[step] = tries + 1
                log(f"コード課題に解答します（step {step}・{tries + 1} 回目）。")
                step_handled.add("code")
                started_task = time.perf_counter()
                if apply_code_task(page, code_tasks[step]):
                    last_change = time.monotonic()
                profile_log(f"コード課題 {time.perf_counter() - started_task:.2f} 秒（step {step}）")
                # 固定待機はしない。次の周回で状態を読み直す。
                continue
            if tries == config.CODE_MAX_ATTEMPTS:
                code_attempts[step] = tries + 1
                log(f"コード課題が通りません（step {step}）。手動確認してください。")

        # 「この要素をクリック」で進むステップ（右上の OK ボタンなど）。
        if step and step in click_tasks and step not in clicked_steps:
            tries = click_attempts.get(step, 0)
            if tries < config.CLICK_MAX_ATTEMPTS:
                click_attempts[step] = tries + 1
                log(f"クリック課題に解答します（step {step}・{tries + 1} 回目）。")
                step_handled.add("click")
                # 1 回目で見つからなければ、2 回目以降は短く待つだけにする。
                wait = config.CLICK_WAIT_SEC if tries == 0 else config.CLICK_RETRY_WAIT_SEC
                if click_step(page, click_tasks[step], wait_sec=wait):
                    clicked_steps.add(step)
                    last_change = time.monotonic()
                    continue

        # プレビュー型の課題（表計算など）: 成功条件のセルへ入力する。
        if step and step in preview_tasks and step not in preview_unsupported:
            tries = preview_attempts.get(step, 0)
            if tries >= config.PREVIEW_MAX_ATTEMPTS:
                log(f"プレビューの課題が通りません（step {step}）。手動確認してください。")
                return False
            preview_attempts[step] = tries + 1
            log(f"プレビューの課題に解答します（step {step}・{tries + 1} 回目）。")
            step_handled.add("preview")
            applied, supported = apply_preview_task(page)
            if applied:
                last_change = time.monotonic()
                continue
            if not supported:
                preview_unsupported.add(step)
                log(f"未対応のプレビュー課題です（step {step}）。通常の進行処理に任せます。")

        # 進行ボタン（Quiz / Next）は、押しても反応しないことがある。
        # ステップが変わるまで短い間隔で押し直して、反応待ちの空白を無くす。
        if state["action"]:
            if now - clicked_action_steps.get(step, 0.0) >= config.ACTION_RECLICK_DELAY:
                clicked_action_steps[step] = now
                _t = time.perf_counter()
                if click_action_button(page):
                    last_change = time.monotonic()
                prof["action"] += time.perf_counter() - _t

        # アニメーション完了待ち（約900ms）を飛ばす。
        # 毎周回だと React の再描画が無駄に走るので短い間隔で間引く。
        if now - skip_state["t"] >= config.SKIP_ANIMATION_INTERVAL:
            skip_state["t"] = now
            _t = time.perf_counter()
            skip_animations(page)
            prof["skip"] += time.perf_counter() - _t

        # ここが本命: Enter 連打（待機なし）。
        _t = time.perf_counter()
        press_enter(page)
        prof["enter"] += time.perf_counter() - _t

        now = time.monotonic()
        # OK ボタンは exitCondition に Click が無いステップでも出るので、
        # 明確に詰まったときだけ 1 ステップ 1 回の保険として押す。
        if now - last_change > 1.5 and step not in confirm_pressed:
            if press_confirm_if_visible(page):
                confirm_pressed.add(step)
                last_change = time.monotonic()
        if now - last_change > config.STUCK_GIVEUP_AFTER:
            log("画面が長時間変化しないため終了します。")
            return False

        human_sleep(calib_delays[calib_index] if calib_delays else config.ENTER_SPAM_DELAY)


def auto_lesson():
    log("--- レッスン自動化を実行します（Enter 連打モード） ---")
    if not acquire_instance():
        return False
    try:
        with ChromeSession(config.LESSON_PAGE_MARKER) as page:
            return run_lesson(page)
    except KeyboardInterrupt:
        log("処理を中断しました。")
        return False
    except RuntimeError as e:
        # 対象タブが無い（ChromeSession）など、続行できない状態。
        log(str(e))
        return False


if __name__ == "__main__":
    sys.exit(0 if auto_lesson() else 1)
