"""DOM を 1 往復で読み書きする共通部品。

クリックはここに集約する。まず JS で押し（往復 1 回で速い）、押せないときだけ
Playwright の実クリックへフォールバックする。実クリックも駄目なら最後に JS。
"""

import re

import config
import ui
from human_utils import human_click


def log(message):
    # 色分けは ui 側で行う（rich が無い環境では素の print）。
    ui.log(message)


def count(page, selector):
    """セレクタに一致する要素数。Chrome が落ちた場合は 0 と取り違えない。"""
    try:
        return page.locator(selector).count()
    except Exception as e:
        text = str(e)
        if "Target closed" in text or "has been closed" in text:
            raise
        return 0


def normalize(text):
    return re.sub(r"\s+", "", text or "")


def safe_page_url(page):
    """今の URL。SPA 遷移直後は page.url が古いのでブラウザ側の location を見る。"""
    try:
        href = page.evaluate("() => location.href")
        if href:
            return href
    except Exception:
        pass
    try:
        return page.url
    except Exception:
        return ""


# 見えている要素を JS で探して押す（往復 1 回）。
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
    return label || ' ';
}"""


CLICK_NTH_JS = """([selector, index]) => {
    const el = [...document.querySelectorAll(selector)][index];
    if (!el) return false;
    el.click();
    return true;
}"""


def click_visible(page, selector, text=None, disabled_class=None):
    """見えているボタンを 1 往復で JS クリックし、押したボタンの文言を返す。

    見つからない／disabled のときは None。
    """
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


def human_locator(page, selector, text=None, nth=None):
    locator = page.locator(selector)
    if text:
        locator = locator.filter(has_text=text)
    if nth is None:
        return locator.first
    return locator.nth(nth)


def click(
    page,
    selector,
    *,
    text=None,
    disabled_class=None,
    nth=None,
    human=True,
    timeout_ms=None,
):
    """要素をクリックする。押せたら True。

    human=False は JS だけ試す（演出待ちを作らない）。既定は JS 失敗時に実クリック。
    """
    if nth is None:
        if click_visible(page, selector, text, disabled_class) is not None:
            return True
    elif click_nth(page, selector, nth):
        return True
    if not human:
        return False
    try:
        human_click(
            human_locator(page, selector, text, nth),
            base_delay=0,
            timeout_ms=timeout_ms or config.ACTION_CLICK_TIMEOUT_MS,
        )
        return True
    except Exception:
        pass
    # モーダルに覆われている等で実クリックできない場合は JS で押す。
    return click_nth(page, selector, nth if nth is not None else 0)
