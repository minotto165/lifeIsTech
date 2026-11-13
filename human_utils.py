"""人間らしい操作を再現するヘルパー。"""

import random
import time

import config


def human_sleep(base_seconds, fluctuation_ratio=0.5):
    """基準時間に対して ±fluctuation_ratio の揺らぎを加えて待機する。"""
    if base_seconds <= 0:
        return
    low = max(base_seconds * (1 - fluctuation_ratio), 0.0)
    high = base_seconds * (1 + fluctuation_ratio)
    time.sleep(random.uniform(low, high))


def human_click(element, base_delay=1.0, timeout_ms=config.ACTION_TIMEOUT_MS):
    """人間らしい待機とマウス移動を挟んで要素をクリックする。

    スクロールしてから位置を測り、その座標へマウスを動かしてから
    element.click() で押す（位置合わせと当たり判定は Playwright に任せる）。
    base_delay=0 は「待たずに押す」明示的な指定（高速化のため）。
    """
    if base_delay > 0:
        human_sleep(max(base_delay, config.MIN_ACTION_DELAY))
    try:
        element.scroll_into_view_if_needed(timeout=timeout_ms)
    except Exception:
        pass
    try:
        box = element.bounding_box(timeout=timeout_ms)
    except Exception:
        box = None
    if not box:
        element.click(timeout=timeout_ms)
        return
    x = box["x"] + random.uniform(box["width"] * 0.2, box["width"] * 0.8)
    y = box["y"] + random.uniform(box["height"] * 0.2, box["height"] * 0.8)
    # マウス移動だけ人間らしく揺らし、クリック自体は element.click() に任せる。
    # 座標クリックは「覆われていて届かない」場合も成功扱いになり、押し損ねが
    # 検知できないため、当たり判定とリトライのある Playwright の経路を使う。
    try:
        element.page.mouse.move(x, y, steps=5)
    except Exception:
        pass
    element.click(timeout=timeout_ms)
