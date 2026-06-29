from playwright.sync_api import sync_playwright

def debug_work_page():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        
        print(f"--- 現在のURL: {page.url} ---")
        
        # 画面内のテキスト要素をすべてダンプ
        elements = page.locator('div, span, button, label').all()
        print(f"--- 画面内の要素数: {len(elements)} ---")
        
        # テキストを持つ重要な要素を探す
        for el in elements:
            try:
                text = el.inner_text().strip()
                if text:
                    print(f"Found: {text}")
            except:
                continue

if __name__ == "__main__":
    debug_work_page()
