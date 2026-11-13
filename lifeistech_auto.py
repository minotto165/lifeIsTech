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
import dom
import poll
import ui
from ai_analyzer import get_ai_response
from dom import click_nth, click_visible, count, normalize
from chrome_session import ChromeSession
from human_utils import human_click, human_sleep
from instance_guard import acquire as acquire_instance


# ステップ処理の戻り値。None は「この種別の処理はしていない」。
NEXT = "next"   # 処理したので次の周回へ
ABORT = "abort"  # これ以上進めないので終了


def log(message):
    # 色分けは ui 側で行う（rich が無い環境では素の print にフォールバック）。
    ui.log(message)


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


def wait_for_step_change(page, before, timeout):
    """ステップが before から変わるまで最大 timeout 秒待つ。変われば True。

    固定 sleep ではなく、変わった瞬間に戻る条件待ち。timeout は上限としてだけ使う。
    """
    return poll.poll_true(lambda: current_step(page) != before, timeout)


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
    limit = (
        (config.CLICK_WAIT_SEC if _needs_render_wait(targets) else config.CLICK_SHORT_WAIT_SEC)
        if wait_sec is None
        else wait_sec
    )
    found = _find_click_target(page, targets, limit)
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


def _find_click_target(page, targets, timeout):
    """クリックできる候補が描画されるまで待ち、見つかった 1 件を返す。"""

    def probe():
        try:
            return page.evaluate(CLICK_FIND_JS, {"targets": targets})
        except Exception:
            return None

    return poll.poll_until(probe, timeout)


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
    limit = config.PREVIEW_READY_TIMEOUT if timeout is None else timeout
    return poll.poll_until(lambda: _find_preview_frame(page), limit)


def _find_preview_frame(page):
    """プレビューのフレームを 1 回探す。無ければ None。"""
    try:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            if "html_playground" in (frame.url or ""):
                return frame
        element = page.locator(config.SEL_PREVIEW_IFRAME).first
        if element.count():
            handle = element.element_handle()
            if handle is not None:
                return handle.content_frame()
    except Exception:
        return None
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
    return poll.poll_true(lambda: has_chosen(page), timeout)


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
    limit = config.QUIZ_FEEDBACK_WAIT if timeout is None else timeout
    return poll.poll_true(lambda: has_feedback(page), limit)


# クイズが閉じたか（モーダルが消えたか）を 1 往復で見る。
QUIZ_CLOSE_JS = r"""() => {
    const open = !!(document.querySelector('%(quiz)s') || document.querySelector('%(image_quiz)s'));
    const text = document.querySelector('%(quiz_text)s');
    return {open: open, question: text ? (text.textContent || '').trim() : ''};
}"""


def _quiz_closed_or_changed(page, js, prev_question):
    """クイズが閉じた、または別の問題に切り替わったか。"""
    try:
        state = page.evaluate(js) or {}
    except Exception:
        return False
    if not state.get("open"):
        return True
    return normalize(state.get("question") or "") != normalize(prev_question or "")


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
        if poll.poll_true(
            lambda: _quiz_closed_or_changed(page, js, prev_question),
            config.QUIZ_ENTER_INTERVAL,
        ):
            return True
        if time.monotonic() >= deadline:
            return False


PROFILE = bool(os.environ.get("LIFEISTECH_PROFILE"))


def profile_log(message):
    if PROFILE:
        log(f"[profile] {message}")


def _resolve_quiz_index(kind, step, key, state, answers, image_answers, is_image):
    """正解の選択肢 index を返す。決められないときは None（次の周回で再試行）。"""
    if is_image:
        info = image_answers["by_text"].get(key[1]) or image_answers["by_step"].get(step)
        if not info:
            log(f"画像選択クイズの正解を特定できませんでした（step {step}）。")
            return None
        try:
            index = info["labels"].index(info["correct"])
        except ValueError:
            log(f"画像選択クイズの選択肢番号が不正です（step {step}）。")
            return None
        if (state.get("image_quiz_count") or 0) <= index:
            log(f"画像選択クイズの選択肢が見つかりません（step {step}）。")
            return None
        return index

    labels = state.get("quiz_choices") or []
    if not labels:
        # まだ描画されていない。次の周回（数 ms 後）で読み直す。
        return None
    correct = answers["by_text"].get(key[1]) or answers["by_step"].get(step)
    index = find_choice_index(labels, correct)
    if index is None:
        # シナリオから引けなかったときだけ AI に聞く。
        index = find_choice_index(labels, ask_ai(state.get("quiz_question") or "", labels))
    if index is None:
        log(f"クイズの正解を特定できませんでした（step {step}）。次の周回で再試行します。")
    return index


def _answer_quiz(page, answers, image_answers, state=None, image=False):
    """クイズに答えて閉じる（テキスト選択と画像選択で共通）。"""
    _started = time.perf_counter()
    state = state or read_state(page)
    step = int(state["step"]) if state.get("step") else current_step(page)
    question = state.get("quiz_question") or ""
    key = (step, normalize(question))
    kind = "画像選択クイズ" if image else "クイズ"
    selector = config.SEL_IMAGE_QUIZ_CHOICE if image else config.SEL_QUIZ_BUTTON

    if not state.get("quiz_chosen"):
        # まだ答えていない問題。選択肢を選ぶところからやり直す。
        index = _resolve_quiz_index(
            kind, step, key, state, answers, image_answers, image
        )
        if index is None:
            return

        # まず JS クリック（1 往復）。反映されなければ実クリックで押し直す。
        click_nth(page, selector, index)
        if not wait_for_chosen(page, timeout=0.25):
            try:
                human_click(
                    page.locator(selector).nth(index),
                    base_delay=0,
                    timeout_ms=config.ACTION_CLICK_TIMEOUT_MS,
                )
            except Exception as e:
                log(f"{kind}のクリックに失敗しました: {e}")
                return
            if not wait_for_chosen(page):
                log(f"{kind}のクリックが反映されませんでした（step {step}）。再試行します。")
                return

        if image:
            log(f"{kind}に回答しました（{index + 1} 番目）。")
        else:
            labels = state.get("quiz_choices") or [""]
            log(f"{kind}に回答しました: {labels[index]}")

        _chosen = time.perf_counter()
        if not wait_for_feedback(page):
            log(f"正解表示を確認できませんでした（step {step}）。Enter を送ります。")
        _feedback = time.perf_counter()
        if not close_quiz(page, key[1]):
            log(f"{kind}が閉じませんでした（step {step}）。次の周回で再試行します。")
        profile_log(
            f"{kind}処理 {time.perf_counter() - _started:.2f} 秒（step {step}・選択"
            f" {_chosen - _started:.2f} / 正解待ち {_feedback - _chosen:.2f} /"
            f" 閉じ {time.perf_counter() - _feedback:.2f}）"
        )
        return

    # 回答済み（正解表示中）。正解表示が出ていることを確認してから Enter で閉じる。
    wait_for_feedback(page, timeout=1.0)
    if not close_quiz(page, key[1]):
        log(f"{kind}が閉じませんでした（step {step}）。次の周回で再試行します。")


def handle_quiz(page, answers, state=None):
    """テキスト選択のクイズに答える。"""
    _answer_quiz(page, answers, {}, state, image=False)


def handle_image_quiz(page, image_answers, state=None):
    """画像から選ぶクイズに答える。"""
    _answer_quiz(page, {}, image_answers, state, image=True)


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
    if not poll.poll_true(
        lambda: dom.click(page, config.SEL_CODE_RUN_BTN, human=False), timeout
    ):
        return False
    log("「実行」ボタンを押しました。")
    return True


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
    return dom.click(page, config.SEL_CODE_CHECK_BTN, human=False)


def close_code_result(page, timeout=3.0):
    """コードチェック後に出る結果ウィンドウを閉じる。"""
    return poll.poll_true(
        lambda: dom.click(page, config.SEL_CODE_RESULT_CLOSE, human=False), timeout
    )


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
    if not dom.click(page, config.SEL_CONFIRM_BTN, human=False):
        return False
    log("「OK」ボタンを押しました。")
    return True


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
    # 見えている要素が JS 側で取れない場合だけ実クリックを試す。
    if not dom.click(page, config.SEL_LESSON_ACTION_BTN, human=True):
        return False
    log("進行ボタンを押しました。")
    return True


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


class StepMetrics:
    """速度計測（LIFEISTECH_PROFILE / CALIB / STEP_LOG）を制御から切り離す。

    制御ループは「何をするか」だけを持ち、時間の記録はここに集める。
    計測が無効なとき（既定）は記録の呼び出しだけが残り、判定には影響しない。
    """

    def __init__(self):
        self.enabled = PROFILE
        self.totals = {"read": 0.0, "skip": 0.0, "enter": 0.0, "action": 0.0}
        self.loops = 0
        self.log_path = os.environ.get("LIFEISTECH_STEP_LOG")
        if self.log_path == "1":
            self.log_path = "/tmp/lifeistech_steps.jsonl"
        self.calib_delays = (
            [0.0, 0.005, 0.01, 0.02, 0.04]
            if os.environ.get("LIFEISTECH_CALIB") == "1"
            else None
        )
        self.calib_index = 0
        self.calib_started = time.monotonic()
        self.calib_steps = 0
        self.step_started = time.monotonic()
        self.step_loops = 0
        self.step_number = None
        self.marks = {}
        self.handled_kinds = set()

    def begin(self, step):
        self.step_number = step
        self.step_started = time.monotonic()
        self.marks = {}
        self.handled_kinds = set()

    def count(self, kind, elapsed):
        self.totals[kind] += elapsed
        if kind == "read":
            self.loops += 1
            self.step_loops += 1

    def log_profile(self, started_at):
        if not self.enabled or self.loops == 0 or self.loops % 100:
            return
        wall = time.monotonic() - started_at
        loops = self.loops
        log(
            f"[profile] {loops} 周 / {wall:.1f} 秒"
            f"（1 周 {wall / loops * 1000:.0f} ms・"
            f"read {self.totals['read'] / loops * 1000:.1f} /"
            f" skip {self.totals['skip'] / loops * 1000:.1f} /"
            f" enter {self.totals['enter'] / loops * 1000:.1f} /"
            f" action {self.totals['action'] / loops * 1000:.1f} ms）"
        )

    def observe(self, state, now):
        """そのステップで吹き出し・ボタン・クイズ・エディタが現れた時刻を残す。"""
        if not self.log_path:
            return
        marks = {
            "talk": state["talk"],
            "button": state["action"],
            "quiz": state["quiz"] or state["image_quiz"],
            "editor": state["editor"],
        }
        for key, present in marks.items():
            if present:
                self.marks.setdefault(key, round(now - self.step_started, 3))

    def handled(self, kind):
        self.handled_kinds.add(kind)

    def next_step(self, step, now, structure, passed_steps):
        """ステップが変わったときの記録。更新後の通過数を返す。"""
        profile_log(
            f"step {self.step_number} → {step}: {now - self.step_started:.3f} 秒"
            f" / {self.step_loops} 周"
        )
        if self.log_path:
            write_step_log(
                self.log_path,
                self.step_number,
                now - self.step_started,
                self.step_loops,
                self.marks,
                structure.get(self.step_number),
                self.handled_kinds,
            )
            self.step_number = step
            self.marks = {}
            self.handled_kinds = set()
        self.step_started = now
        self.step_loops = 0
        return self._calibrate(now, passed_steps + 1)

    def _calibrate(self, now, passed_steps):
        if not self.calib_delays or passed_steps - self.calib_steps < 60:
            return passed_steps
        elapsed = now - self.calib_started
        count = passed_steps - self.calib_steps
        log(
            f"[calib] Enter 間隔 {self.calib_delays[self.calib_index] * 1000:.0f} ms →"
            f" {count} ステップ / {elapsed:.1f} 秒"
            f" = {elapsed / count * 1000:.0f} ms/ステップ"
        )
        self.calib_index = (self.calib_index + 1) % len(self.calib_delays)
        self.calib_started = now
        self.calib_steps = passed_steps
        return passed_steps

    def delay(self):
        if self.calib_delays:
            return self.calib_delays[self.calib_index]
        return config.ENTER_SPAM_DELAY


class LessonRunner:
    """1 レッスン分の制御ループ。

    ステップ種別ごとの処理をメソッドに分け、共有する状態はインスタンスに持つ。
    各メソッドは「処理していない」= None、「処理したので次の周回へ」= NEXT、
    「これ以上進めない」= ABORT を返す。
    """

    def __init__(self, page):
        self.page = page
        self.metrics = StepMetrics()
        self.answers = {}
        self.image_answers = {}
        self.code_tasks = {}
        self.preview_tasks = set()
        self.click_tasks = {}
        self.free_inputs = set()
        self.structure = {}
        self.last_step = None
        self.last_change = 0.0
        self.started_at = 0.0
        self.passed_steps = 0
        self.skip_at = 0.0
        # ステップごとの試行回数・記録。
        self.clicked_action_steps = {}
        self.confirm_pressed = set()
        self.clicked_steps = set()
        self.click_attempts = {}
        self.code_attempts = {}
        self.preview_attempts = {}
        self.preview_unsupported = set()
        self.quiz_reloads = {}
        self.locked_since = None
        self.quiz_key = None
        self.quiz_since = None

    # --- 準備 ---------------------------------------------------------

    def _install_injections(self):
        if config.DISABLE_CSS_ANIMATION:
            install_animation_killer(self.page)
        if config.SHORTEN_LONG_TIMERS:
            install_timer_clamp(self.page)

    def _load_data(self):
        data = load_scenario_data(self.page)
        self.answers = data["answers"]
        self.image_answers = data["image_answers"]
        self.code_tasks = data["codes"]
        self.preview_tasks = data["previews"]
        self.click_tasks = data["clicks"]
        self.free_inputs = data["free_inputs"]
        self.structure = data["structure"]

    def _reset_records(self):
        self.clicked_action_steps.clear()
        self.confirm_pressed.clear()
        self.clicked_steps.clear()
        self.click_attempts.clear()
        self.code_attempts.clear()
        self.preview_attempts.clear()
        self._clear_quiz_tracking()

    def _in_lesson(self):
        try:
            return read_state(self.page)["in_lesson"]
        except Exception:
            return False

    def _reload(self, reason):
        """再読込して、ステップ依存の記録とシナリオの対応表を作り直す。"""
        if not reload_page(self.page, reason):
            return False
        self._reset_records()
        # 再読込直後はプレイヤーの描画を待つ（レッスンとして読めるまで）。
        poll.poll_true(self._in_lesson, config.LESSON_PAGE_READY_TIMEOUT, config.NAV_POLL_SEC)
        self._install_injections()
        # 再読込で別シナリオに移っていることがあるので取り直す。
        self._load_data()
        try:
            self.last_step = read_state(self.page)["step"]
        except Exception:
            self.last_step = None
        self.last_change = time.monotonic()
        return True

    # --- ステップ種別ごとの処理 ---------------------------------------

    def _clear_quiz_tracking(self):
        self.locked_since = None
        self.quiz_key = None
        self.quiz_since = None

    def _quiz_step(self, state, step, now):
        if not (state["quiz"] or state["image_quiz"]):
            return None
        # 「disabled のみ・chosen なし」で固まったモーダルは Enter では
        # 復帰できないため、一定時間で再読込して立て直す。
        if state["quiz_locked"]:
            self.locked_since = self.locked_since or now
            if now - self.locked_since > config.BROKEN_QUIZ_WAIT:
                self.locked_since = None
                if self._reload("壊れたクイズ"):
                    return NEXT
        else:
            self.locked_since = None

        # 同じ問題のまま進まないクイズは、Enter 連打では解決しない。
        # 一定時間で諦めて再読込し、それでも駄目なら停止する。
        key = (step, normalize(state.get("quiz_question") or ""))
        if key != self.quiz_key:
            self.quiz_key = key
            self.quiz_since = now
        elif self.quiz_since is not None and now - self.quiz_since > config.QUIZ_GIVEUP_AFTER:
            tries = self.quiz_reloads.get(step, 0) + 1
            self.quiz_reloads[step] = tries
            if tries > config.QUIZ_MAX_RELOADS:
                log(f"クイズを解けないため停止します（step {step}）。手動で確認してください。")
                return ABORT
            log(f"クイズが反応しないため再読込します（step {step}・{tries} 回目）。")
            if not self._reload("クイズが応答しない"):
                return ABORT
            return NEXT

        self.metrics.handled("quiz")
        if state["image_quiz"]:
            handle_image_quiz(self.page, self.image_answers, state)
        else:
            handle_quiz(self.page, self.answers, state)
        return NEXT

    def _free_input_step(self, step):
        # ヒントを読んで自分で書く課題は、正解がシナリオに無いので自動解答できない。
        # ここで早めに止めないと 30 秒×2 待ってから諦めることになる。
        if step and step in self.free_inputs:
            log(f"自由入力のコード課題のため自動解答できません（step {step}）。")
            return ABORT
        return None

    def _code_step(self, step):
        # コード課題: エディタに解答を入れて「コードチェック」を押す。
        if not (step and step in self.code_tasks and has_code_check(self.page)):
            return None
        tries = self.code_attempts.get(step, 0)
        if tries >= config.CODE_MAX_ATTEMPTS:
            if tries == config.CODE_MAX_ATTEMPTS:
                self.code_attempts[step] = tries + 1
                log(f"コード課題が通りません（step {step}）。手動確認してください。")
            return None
        self.code_attempts[step] = tries + 1
        log(f"コード課題に解答します（step {step}・{tries + 1} 回目）。")
        self.metrics.handled("code")
        started = time.perf_counter()
        if apply_code_task(self.page, self.code_tasks[step]):
            self.last_change = time.monotonic()
        profile_log(f"コード課題 {time.perf_counter() - started:.2f} 秒（step {step}）")
        return NEXT

    def _click_step(self, step):
        # 「この要素をクリック」で進むステップ（右上の OK ボタンなど）。
        if not (step and step in self.click_tasks and step not in self.clicked_steps):
            return None
        tries = self.click_attempts.get(step, 0)
        if tries >= config.CLICK_MAX_ATTEMPTS:
            return None
        self.click_attempts[step] = tries + 1
        log(f"クリック課題に解答します（step {step}・{tries + 1} 回目）。")
        self.metrics.handled("click")
        # 1 回目で見つからなければ、2 回目以降は短く待つだけにする。
        wait = config.CLICK_WAIT_SEC if tries == 0 else config.CLICK_RETRY_WAIT_SEC
        if click_step(self.page, self.click_tasks[step], wait_sec=wait):
            self.clicked_steps.add(step)
            self.last_change = time.monotonic()
            return NEXT
        return None

    def _preview_step(self, step):
        # プレビュー型の課題（表計算など）: 成功条件のセルへ入力する。
        if not (
            step
            and step in self.preview_tasks
            and step not in self.preview_unsupported
        ):
            return None
        tries = self.preview_attempts.get(step, 0)
        if tries >= config.PREVIEW_MAX_ATTEMPTS:
            log(f"プレビューの課題が通りません（step {step}）。手動確認してください。")
            return ABORT
        self.preview_attempts[step] = tries + 1
        log(f"プレビューの課題に解答します（step {step}・{tries + 1} 回目）。")
        self.metrics.handled("preview")
        applied, supported = apply_preview_task(self.page)
        if applied:
            self.last_change = time.monotonic()
            return NEXT
        if not supported:
            self.preview_unsupported.add(step)
            log(f"未対応のプレビュー課題です（step {step}）。通常の進行処理に任せます。")
        return None

    def _progress(self, state, step, now):
        """進行ボタン → アニメ飛ばし → Enter → 詰まり判定を 1 周分まとめて行う。"""
        page = self.page
        if state["action"] and (
            now - self.clicked_action_steps.get(step, 0.0)
            >= config.ACTION_RECLICK_DELAY
        ):
            self.clicked_action_steps[step] = now
            started = time.perf_counter()
            if click_action_button(page):
                self.last_change = time.monotonic()
            self.metrics.count("action", time.perf_counter() - started)

        # アニメーション完了待ち（約900ms）を飛ばす。
        # 毎周回だと React の再描画が無駄に走るので短い間隔で間引く。
        if now - self.skip_at >= config.SKIP_ANIMATION_INTERVAL:
            self.skip_at = now
            started = time.perf_counter()
            skip_animations(page)
            self.metrics.count("skip", time.perf_counter() - started)

        # ここが本命: Enter 連打（待機なし）。
        started = time.perf_counter()
        press_enter(page)
        self.metrics.count("enter", time.perf_counter() - started)

        now = time.monotonic()
        # OK ボタンは exitCondition に Click が無いステップでも出るので、
        # 明確に詰まったときだけ 1 ステップ 1 回の保険として押す。
        if now - self.last_change > 1.5 and step not in self.confirm_pressed:
            if press_confirm_if_visible(page):
                self.confirm_pressed.add(step)
                self.last_change = time.monotonic()
        if now - self.last_change > config.STUCK_GIVEUP_AFTER:
            log("画面が長時間変化しないため終了します。")
            return ABORT
        return NEXT

    # --- 状態遷移 -----------------------------------------------------

    def _advance(self, step, now):
        log(f"ステップ {step} を通過しました。")
        self.last_step = step
        self.last_change = now
        self.passed_steps = self.metrics.next_step(
            step, now, self.structure, self.passed_steps
        )

    def _finish_after_navigation(self):
        """SPA 遷移で実行コンテキストが消えたとき、完了画面になるまで待つ。"""
        finished = poll.poll_true(
            lambda: lesson_finished(self.page),
            config.LESSON_PAGE_READY_TIMEOUT,
            config.NAV_POLL_SEC,
        )
        if finished:
            log_finish_speed(self.started_at, self.passed_steps)
            return True
        log("ページが遷移しましたが、終了画面を確認できませんでした。")
        return False

    def _finish_after_leave(self):
        if lesson_finished(self.page):
            log_finish_speed(self.started_at, self.passed_steps)
            return True
        log("レッスンページを離れたため終了します（完了画面は未確認）。")
        return False

    # --- メインループ -------------------------------------------------

    def run(self):
        page = self.page
        page.set_default_timeout(config.ACTION_TIMEOUT_MS)
        state = read_state(page)
        if not state["in_lesson"]:
            log(
                "レッスンページが開かれていません。"
                "ブラウザでレッスンを開いてから実行してください。"
            )
            return False

        self._install_injections()
        self._load_data()
        self.last_step = state["step"]
        self.metrics.begin(self.last_step)
        self.last_change = time.monotonic()
        self.started_at = time.monotonic()
        log(f"ステップ {self.last_step} から開始します。")

        handlers = (
            self._quiz_step,
            self._free_input_step,
            self._code_step,
            self._click_step,
            self._preview_step,
        )
        while True:
            started = time.perf_counter()
            try:
                state = read_state(page)
            except Exception as e:
                if "Execution context was destroyed" in str(e):
                    return self._finish_after_navigation()
                raise
            self.metrics.count("read", time.perf_counter() - started)
            self.metrics.log_profile(self.started_at)

            if not state["in_lesson"]:
                return self._finish_after_leave()

            # ステップ番号はシナリオ JSON（int）と突き合わせるので int にそろえる。
            step = int(state["step"]) if state["step"] else None
            now = time.monotonic()
            if step != self.last_step:
                self._advance(step, now)
            self.metrics.observe(state, now)

            result = self._quiz_step(state, step, now)
            if result is None:
                self._clear_quiz_tracking()
                for handler in handlers[1:]:
                    result = handler(step)
                    if result is not None:
                        break
            if result is None:
                result = self._progress(state, step, now)
            if result == ABORT:
                return False

            human_sleep(self.metrics.delay())


def _run_lesson_loop(page):
    return LessonRunner(page).run()


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
