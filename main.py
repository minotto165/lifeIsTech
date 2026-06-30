import subprocess
import time
import os
import sys
import platform
import shutil

def get_chrome_path():
    if platform.system() == "Windows":
        return r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    elif platform.system() == "Darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    else:
        # PATHから探す
        path = shutil.which("google-chrome")
        return path if path else "google-chrome"

def launch_and_monitor():
    chrome_path = get_chrome_path()
    user_data_dir = os.path.abspath("./chrome_profile")
    
    cmd = [
        chrome_path,
        f"--user-data-dir={user_data_dir}",
        "--remote-debugging-port=9222",
        "https://member.lifeistech-lesson.jp/home"
    ]
    
    print("--- ブラウザを起動しています... ---")
    with open(os.devnull, 'w') as devnull:
        proc = subprocess.Popen(cmd, stdout=devnull, stderr=devnull)
    time.sleep(5)
    
    try:
        while True:
            print("\n実行モードを選択してください")
            print("1: レッスン自動化を実行")
            print("2: 理解度チェックワーク")
            choice = input("選択してください (1/2): ")
            
            if choice == "1":
                print("レッスン自動化を開始します")
                auto_script = os.path.abspath("lifeistech_auto.py")
                try:
                    subprocess.run([sys.executable, auto_script])
                except KeyboardInterrupt:
                    print("\n自動化を停止しました")
            elif choice == "2":
                print("理解度チェックワークを開始します")
                work_script = os.path.abspath("work_automation.py")
                try:
                    subprocess.run([sys.executable, work_script])
                except KeyboardInterrupt:
                    print("\n自動化を停止しました")
            else:
                print("無効な選択です")
    except KeyboardInterrupt:
        print("\nプログラムを終了します")

if __name__ == "__main__":
    launch_and_monitor()

