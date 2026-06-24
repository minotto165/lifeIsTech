import sys
sys.stdout.reconfigure(line_buffering=True)
from playwright.sync_api import sync_playwright
import time

def auto_lesson():
    print("--- 最終安定版: クイズ解析ロジック強化 ---")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        
        while True:
            # クイズ選択肢を事前に取得
            options = page.query_selector_all('div.quiz-button.btn.btn-primary')
            
            # 1. クイズ解析 (AI連携準備)
            quiz_text_el = page.query_selector('.quiz-text')
            if quiz_text_el:
                print(f"--- [AI解析用] クイズテキスト: {quiz_text_el.inner_text().strip()} ---")
                option_texts = [o.inner_text().strip() for o in options]
                print(f"--- [AI解析用] 選択肢一覧: {option_texts} ---")

            # 2. クイズ回答 logic
            for el in options:
                text = el.inner_text().strip()
                # 正解候補のキーワードリスト
                if any(k in text for k in ["Society5.0", "Society 5.0", "ユビキタスコンピューティング"]):
                    print(f"DEBUG: 正解選択肢 '{text}' をクリックします")
                    el.click()
                    time.sleep(2)
                    page.keyboard.press("Enter")
                    time.sleep(2)

            # 3. その他ボタン (スタート、Quiz開始など)
            btns = page.query_selector_all('button, div[role="button"]')
            for b in btns:
                text = b.inner_text().strip()
                if any(t in text for t in ["レッスンスタート", "続きから再開", "Quiz"]):
                    print(f"DEBUG: ボタンをクリック: '{text}'")
                    b.click()
                    time.sleep(2)
            
            # 4. 常時Enterキー送信
            page.keyboard.press("Enter")
            time.sleep(2)

if __name__ == "__main__":
    auto_lesson()
