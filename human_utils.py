import time
import random

def human_sleep(base_seconds):
    """固定時間に対して±30%のランダムな揺らぎを加えた待機を行う"""
    if base_seconds == 0:
        return
    fluctuation = base_seconds * 0.5
    sleep_time = random.uniform(base_seconds - fluctuation, base_seconds + fluctuation)
    time.sleep(sleep_time)

def human_click(element, base_delay=1.0):
    """要素をクリックする前に人間らしいランダムな座標計算と待機を行う"""
    human_sleep(base_delay)
    # 要素のバウンディングボックスを取得し、ランダムな位置を計算
    box = element.bounding_box()
    if box:
        x = box['x'] + random.uniform(box['width'] * 0.2, box['width'] * 0.8)
        y = box['y'] + random.uniform(box['height'] * 0.2, box['height'] * 0.8)
        # マウスを移動してからクリック
        element.page.mouse.move(x, y, steps=5)
        element.click()
    else:
        element.click()
