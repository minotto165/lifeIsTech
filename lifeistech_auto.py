import sys
sys.stdout.reconfigure(line_buffering=True)
from playwright.sync_api import sync_playwright
import time
from ai_analyzer import get_ai_response
from human_utils import human_sleep, human_click

def auto_lesson():
    print("--- レッスン自動化を実行します ---")
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    context = browser.contexts[0]
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = context.pages[0]
    
    while True:
            # 記述問題ページ（スキップ対象）の検知
            if page.locator('.css-y7m4we').count() > 0:
                print("記述問題ページを検知しました。自動化を終了します。")
                sys.exit(0)

            # 1. 遷移ボタン(btn-next)のチェック
            next_btn = page.locator('button.btn-next')
            if next_btn.count() > 0:
                try:
                    human_click(next_btn.first, base_delay=1.0)
                    human_sleep(1.0)
                    continue
                except:
                    pass

            # クイズ解析とボタン操作をLocatorベースに統一
            quiz_text_el = page.query_selector('.quiz-text')
            if quiz_text_el:
                human_sleep(1.0)
                quiz_text = quiz_text_el.inner_text().strip()
                
                # ボタンのテキストを取得
                buttons = page.locator('div.quiz-button.btn.btn-primary')
                
                prompt = f"クイズ: {quiz_text}\n選択肢: {[buttons.nth(i).inner_text().strip() for i in range(buttons.count())]}\n正解の選択肢のテキストを一つだけ、余計な言葉なしで返してください。"
                result = get_ai_response(prompt)
                
                if result:
                    result = result.strip()
                    # 再度ボタンを特定してクリック
                    for i in range(buttons.count()):
                        text = buttons.nth(i).inner_text().strip()
                        if text in result or result in text:
                            human_click(buttons.nth(i), base_delay=2.0)
                            page.keyboard.press("Enter")
                            human_sleep(4.0)
                            break
            
            # 3. その他ボタン
            other_btns = page.locator('button, div[role="button"]')
            for i in range(other_btns.count()):
                try:
                    text = other_btns.nth(i).inner_text().strip()
                    if any(t in text for t in ["レッスンスタート", "続きから再開", "Quiz"]):
                        human_click(other_btns.nth(i), base_delay=1.0)
                except:
                    continue
            
            page.keyboard.press("Enter")



if __name__ == "__main__":
    auto_lesson()
