from playwright.sync_api import sync_playwright

def debug_connection():
    try:
        with sync_playwright() as p:
            # 1. CDP接続確認
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            page = browser.contexts[0].pages[0]
            
            print(f"--- 接続成功 ---")
            print(f"Title: {page.title()}")
            print(f"URL: {page.url}")
            
            # 2. ボタン構造の確認
            buttons = page.query_selector_all('button, div[role="button"]')
            print(f"Found {len(buttons)} interactable elements.")
            for b in buttons:
                print(f"Button/Div: '{b.inner_text().strip()}'")
                
    except Exception as e:
        print(f"DEBUG ERROR: {e}")

if __name__ == "__main__":
    debug_connection()
