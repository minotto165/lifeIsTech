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
    """人間らしい待機と座標移動を挟んで要素をクリックする。"""
    if base_delay > 0:
        human_sleep(max(base_delay, config.MIN_ACTION_DELAY))
    try:
        box = element.bounding_box(timeout=timeout_ms)
    except Exception:
        box = None
    if box:
        x = box["x"] + random.uniform(box["width"] * 0.2, box["width"] * 0.8)
        y = box["y"] + random.uniform(box["height"] * 0.2, box["height"] * 0.8)
        try:
            element.page.mouse.move(x, y, steps=5)
        except Exception:
            pass
    element.click(timeout=timeout_ms)
