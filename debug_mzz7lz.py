from playwright.sync_api import sync_playwright

def debug_mzz7lz():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        # ワークページと思われるタブを探す
        target_page = None
        for context in browser.contexts:
            for page in context.pages:
                if "variant_check_work" in page.url:
                    target_page = page
                    break
        
        if not target_page:
            print("Target page not found")
            return

        # span.css-mzz7lz を特定できるか
        elements = target_page.locator('span.css-mzz7lz').all()
        print(f"Found {len(elements)} span.css-mzz7lz")
        
        for i, el in enumerate(elements):
            print(f"--- Element {i} ---")
            print(f"Text: {el.inner_text()}")

if __name__ == "__main__":
    debug_mzz7lz()
