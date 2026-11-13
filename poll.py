"""状態が変わるまで待つ共通ヘルパー。

固定 sleep の代わりに「条件が満たされた瞬間に返る」待機をここへ集約する。
timeout は打ち切り上限としてだけ使う。
"""

import time

import config


def poll_until(fn, timeout, interval=None, label=None):
    """fn() が真を返すまで待ち、その戻り値（真）を返す。時間切れは None。

    label は失敗時ログ用の任意の名前（呼び出し側で使う）。
    """
    interval = config.FAST_POLL_SEC if interval is None else interval
    deadline = time.monotonic() + timeout
    while True:
        value = fn()
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def poll_true(fn, timeout, interval=None, label=None):
    """bool が欲しい場合の薄いラッパ。"""
    return poll_until(fn, timeout, interval, label) is not None
