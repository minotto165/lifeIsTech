"""Life is Tech! レッスン自動化ツールのエントリーポイント。"""

import os
import platform
import shutil
import socket
import subprocess
import sys
import time

import config

MENU = {
    "1": ("レッスン自動化", "lifeistech_auto.py"),
    "2": ("理解度チェックワーク", "work_automation.py"),
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


def ensure_chrome_running():
    """デバッグポートが閉じていれば Chrome を起動する。起動した場合のみプロセスを返す。"""
    if is_debug_port_open():
        print(f"既存の Chrome (ポート {config.CHROME_DEBUG_PORT}) に接続します。")
        return None

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
            return proc
        if proc.poll() is not None:
            raise RuntimeError("Chrome の起動に失敗しました。")
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError(
        f"Chrome のデバッグポート ({config.CHROME_DEBUG_PORT}) が開きませんでした。"
    )


def run_script(script_name):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, script_name)
    if not os.path.isfile(path):
        print(f"スクリプトが見つかりません: {path}")
        return
    try:
        subprocess.run([sys.executable, path], cwd=base_dir, check=False)
    except KeyboardInterrupt:
        print("\n自動化を停止しました。")


def choose_mode():
    print("\n実行モードを選択してください")
    for key, (label, _) in MENU.items():
        print(f"{key}: {label}")
    print("0: 終了")
    try:
        choice = input("選択してください (0/1/2): ").strip()
    except EOFError:
        return None
    return choice


def launch_and_monitor():
    proc = None
    try:
        proc = ensure_chrome_running()
        while True:
            choice = choose_mode()
            if choice is None:
                print("\n入力が終了したためプログラムを終了します。")
                return
            if choice == "0":
                print("プログラムを終了します。")
                return
            entry = MENU.get(choice)
            if entry is None:
                print("無効な選択です。")
                continue
            label, script_name = entry
            print(f"{label}を開始します。")
            run_script(script_name)
    except KeyboardInterrupt:
        print("\nプログラムを終了します。")
    except Exception as e:
        print(f"エラーが発生しました: {e}")
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    launch_and_monitor()
