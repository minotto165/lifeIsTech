import subprocess
import time
import os
import sys

def launch_and_monitor():
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    user_data_dir = os.path.abspath("./chrome_profile")
    
    cmd = [
        chrome_path,
        f"--user-data-dir={user_data_dir}",
        "--remote-debugging-port=9222",
        "https://member.lifeistech-lesson.jp/home"
    ]
    
    print("--- ブラウザを起動しています... ---")
    proc = subprocess.Popen(cmd)
    time.sleep(5)
    
    while True:
        print("\n--- 実行モードを選択してください ---")
        print("1: レッスン自動化を実行")
        print("2: 理解度チェックワーク (未実装)")
        choice = input("選択してください (1/2): ")
        
        if choice == "1":
            print("--- レッスン自動化スクリプトを開始します ---")
            auto_script = os.path.abspath("lifeistech_auto.py")
            try:
                subprocess.run([sys.executable, auto_script])
            except KeyboardInterrupt:
                print("\n--- 自動化を停止しました ---")
        elif choice == "2":
            print("--- 理解度チェックワークを開始します ---")
            work_script = os.path.abspath("work_automation.py")
            try:
                subprocess.run([sys.executable, work_script])
            except KeyboardInterrupt:
                print("\n--- 自動化を停止しました ---")
        else:
            print("--- 無効な選択です ---")

if __name__ == "__main__":
    launch_and_monitor()

