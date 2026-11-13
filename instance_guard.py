"""同じ Chrome に対して自動化プロセスが同時に走らないようにするガード。"""

import atexit
import contextlib
import os

import config

try:
    import fcntl
except ImportError:  # Windows には fcntl が無い（多重起動ガードは効かせない）
    fcntl = None

_handle = None


def _lock_path():
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, config.LOCK_FILE)


def acquire():
    """ロックを取れたら True。既に別プロセスが動いていれば False。

    fcntl が無い環境（Windows）では何もしない。
    """
    global _handle
    if fcntl is None:
        return True
    if _handle is not None:
        return True
    handle = open(_lock_path(), "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        print(
            "別の自動化プロセスが既に実行中です（同じ Chrome を同時に操作すると壊れます）。",
            flush=True,
        )
        return False
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _handle = handle
    atexit.register(release)
    return True


def release():
    global _handle
    if _handle is None:
        return
    if fcntl is not None:
        try:
            fcntl.flock(_handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            # 解放に失敗してもプロセス終了で OS がロックを外すため、ここでは黙って続行する。
            pass
    # 終了時の後始末なので、close の失敗は握り潰してよい（OS が後始末する）。
    with contextlib.suppress(OSError):
        _handle.close()
    _handle = None