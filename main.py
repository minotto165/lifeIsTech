"""Life is Tech! レッスン自動化ツールのエントリーポイント。"""

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import traceback
import urllib.request

import config

MENU = {
    "1": ("レッスン自動化", "lifeistech_auto.py"),
    "2": ("理解度チェックワーク", "work_automation.py"),
    "3": ("全自動（未完了レッスンを自動選択して連続実行）", "auto_runner.py"),
}


def get_chrome_path():
    """OS ごとに Chrome の実行ファイルパスを解決する。"""
    system = platform.system()
    if system == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    elif system == "Darwin":
        candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        candidates = [shutil.which("google-chrome"), shutil.which("chromium")]

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise FileNotFoundError(
        "Chrome が見つかりませんでした。Google Chrome をインストールしてください。"
    )


def is_debug_port_open(timeout=0.5):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", config.CHROME_DEBUG_PORT)) == 0


def wait_for_page_ready(timeout=None):
    """デバッグポートが開いても、まだページが無いことがあるので描画側も待つ。"""
    timeout = config.CHROME_STARTUP_TIMEOUT if timeout is None else timeout
    url = f"http://127.0.0.1:{config.CHROME_DEBUG_PORT}/json/list"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as res:
                pages = json.loads(res.read() or b"[]")
        except Exception:
            pages = []
        for page in pages:
            found_url = str(page.get("url") or "")
            if page.get("type") == "page" and found_url.startswith("http"):
                return True
        time.sleep(0.3)
    return False


def profile_owner_pid():
    """chrome_profile/SingletonLock から、このプロファイルを使っている Chrome の PID を読む。"""
    lock = os.path.join(os.path.abspath(config.CHROME_PROFILE_DIR), "SingletonLock")
    try:
        target = os.readlink(lock)
    except OSError:
        return None
    try:
        return int(str(target).rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


def is_process_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def ensure_chrome_running():
    """デバッグポートが閉じていれば Chrome を起動する。起動した場合のみプロセスを返す。"""
    if is_debug_port_open():
        owner = profile_owner_pid()
        if owner is None or is_process_alive(owner):
            print(f"既存の Chrome (ポート {config.CHROME_DEBUG_PORT}) に接続します。")
            return None
        # ポートは開いているが、それは別プロファイルの Chrome（他ツールの内蔵ブラウザ等）。
        # そのまま繋ぐとログインしていない別人のブラウザを操作してしまう。
        raise RuntimeError(
            f"ポート {config.CHROME_DEBUG_PORT} は別の Chrome が使用中です。"
            "その Chrome を終了するか、環境変数 LIFEISTECH_CHROME_PORT で別のポートを指定してください。"
        )

    chrome_path = get_chrome_path()
    user_data_dir = os.path.abspath(config.CHROME_PROFILE_DIR)
    os.makedirs(user_data_dir, exist_ok=True)

    cmd = [
        chrome_path,
        f"--user-data-dir={user_data_dir}",
        f"--remote-debugging-port={config.CHROME_DEBUG_PORT}",
        config.LESSON_HOME_URL,
    ]
    print("--- ブラウザを起動しています... ---")
    with open(os.devnull, "w") as devnull:
        proc = subprocess.Popen(cmd, stdout=devnull, stderr=devnull)

    deadline = time.time() + config.CHROME_STARTUP_TIMEOUT
    while time.time() < deadline:
        if is_debug_port_open():
            # ポートが開いた直後はまだ about:blank しか無いことがある。
            if wait_for_page_ready():
                return proc
            break
        if proc.poll() is not None:
            raise RuntimeError("Chrome の起動に失敗しました。")
        time.sleep(0.5)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    raise RuntimeError(
        f"Chrome のデバッグポート ({config.CHROME_DEBUG_PORT}) が開きませんでした。"
    )


def run_script(script_name, extra_args=()):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, script_name)
    if not os.path.isfile(path):
        print(f"スクリプトが見つかりません: {path}")
        return 1
    cmd = [sys.executable, str(path), *extra_args]
    try:
        result = subprocess.run(cmd, cwd=base_dir, check=False)
    except KeyboardInterrupt:
        print("\n自動化を停止しました。")
        return 130
    if result.returncode != 0:
        print(f"スクリプトが異常終了しました（終了コード {result.returncode}）。")
    return result.returncode


def choose_mode():
    print("\n実行モードを選択してください")
    for key, (label, _) in MENU.items():
        print(f"{key}: {label}")
    print("0: 終了")
    keys = "".join(sorted([*MENU.keys(), "0"]))
    try:
        choice = input(f"選択してください ({keys}): ").strip()
    except EOFError:
        return None
    return choice


def launch_and_monitor(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # 第1引数がモード番号ならメニューを出さずに起動する（例: main.py 3 --max 1）。
    forced = None
    if argv and argv[0] in MENU:
        forced = argv.pop(0)

    proc = None
    try:
        proc = ensure_chrome_running()
        while True:
            choice = forced if forced is not None else choose_mode()
            forced = None
            if choice is None:
                print("\n入力が終了したためプログラムを終了します。")
                return 0
            if choice == "0":
                print("プログラムを終了します。")
                return 0
            entry = MENU.get(choice)
            if entry is None:
                print("無効な選択です。")
                continue
            label, script_name = entry
            print(f"{label}を開始します。")
            code = run_script(script_name, argv)
            argv = []
            if code:
                return code
    except KeyboardInterrupt:
        print("\nプログラムを終了します。")
        return 130
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        traceback.print_exc()
        return 1
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(launch_and_monitor())
