import sys
sys.stdout.reconfigure(line_buffering=True)
from playwright.sync_api import sync_playwright
import time
from ai_analyzer import get_ai_response

def auto_lesson():
    print("--- AI統合・解析強化版 ---")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        
        while True:
            # 記述問題ページ（スキップ対象）の検知
            if page.locator('.css-y7m4we').count() > 0:
                print("DEBUG: 記述問題ページを検知しました。自動化を終了します。")
                print("手動で入力するか、スキップしてください。")
                sys.exit(0)

            # 1. 遷移ボタン(btn-next)のチェック
            next_btn = page.locator('button.btn-next')
            if next_btn.count() > 0:
                print("DEBUG: 'Next'ボタンを検知。クリックして遷移します。")
                try:
                    next_btn.first.click(timeout=1000)
                    time.sleep(1)
                    continue # 遷移後はループの先頭へ
                except:
                    pass

            # クイズ解析とボタン操作をLocatorベースに統一
            quiz_text_el = page.query_selector('.quiz-text')
            if quiz_text_el:
                quiz_text = quiz_text_el.inner_text().strip()
                
                # ボタンのテキストを取得
                buttons = page.locator('div.quiz-button.btn.btn-primary')
                btn_texts = []
                for i in range(buttons.count()):
                    btn_texts.append(buttons.nth(i).inner_text().strip())
                
                print(f"--- [AI解析] クイズテキスト: {quiz_text} ---")
                print(f"--- [AI解析] 選択肢テキスト: {btn_texts} ---")
                
                prompt = f"クイズ: {quiz_text}\n選択肢: {btn_texts}\n正解の選択肢のテキストを一つだけ、余計な言葉なしで返してください。"
                result = get_ai_response(prompt)
                
                if result:
                    result = result.strip()
                    print(f"--- [AI解析結果] AIが選んだ回答: {result} ---")
                    
                    # 再度ボタンを特定してクリック
                    for i in range(buttons.count()):
                        text = buttons.nth(i).inner_text().strip()
                        if text in result or result in text:
                            print(f"DEBUG: AIの回答 '{result}' に基づき '{text}' をクリック")
                            buttons.nth(i).click()
                            time.sleep(2)
                            page.keyboard.press("Enter")
                            time.sleep(2)
                            break
            
            # 2. クイズ回答 logic (修正: 既存ロジックとAI回答をマージして Locator で統一)
            buttons = page.locator('div.quiz-button.btn.btn-primary')
            for i in range(buttons.count()):
                btn = buttons.nth(i)
                try:
                    text = btn.inner_text().strip()
                    # キーワード回答
                    if any(k in text for k in ["Society5.0", "Society 5.0", "ユビキタスコンピューティング"]):
                        print(f"DEBUG: キーワード正解 '{text}' をクリック")
                        btn.click(timeout=1000)
                        page.keyboard.press("Enter")
                        break
                except:
                    continue
            
            # 3. その他ボタン
            other_btns = page.locator('button, div[role="button"]')
            for i in range(other_btns.count()):
                try:
                    text = other_btns.nth(i).inner_text().strip()
                    if any(t in text for t in ["レッスンスタート", "続きから再開", "Quiz"]):
                        print(f"DEBUG: ボタンクリック: '{text}'")
                        other_btns.nth(i).click(timeout=1000)
                except:
                    continue
            
            # 常時Enter (sleepなしで高速化)
            page.keyboard.press("Enter")
            # 完全削除


if __name__ == "__main__":
    auto_lesson()
