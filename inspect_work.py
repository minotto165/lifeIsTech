from playwright.sync_api import sync_playwright

def inspect_page():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        # ワークページと思われるタブを探す (URLに "variant_check_work" を含む)
        target_page = None
        for context in browser.contexts:
            for page in context.pages:
                if "variant_check_work" in page.url:
                    target_page = page
                    break
        
        if not target_page:
            print("Target page not found")
            return

        print(f"--- Title: {target_page.title()} ---")
        # 直接CSSセレクタで問題文を取得してみる
        # さっき教えてもらった構造: <div class="css-dt6mr"><span class="css-mzz7lz"><div>...</div></span></div>
        question = target_page.locator('div.css-dt6mr').first.inner_text()
        print(f"Question text: {question}")
        
        # ラジオボタンのラベルを探す
        labels = target_page.locator('label').all()
        for label in labels:
            print(f"Label: {label.inner_text().strip()}")

if __name__ == "__main__":
    inspect_page()
