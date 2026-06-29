from playwright.sync_api import sync_playwright
import time

def debug_structure():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        
        # DOM全体を検索して、css-mzz7lz がどうなっているか確認
        elements = page.locator('.css-mzz7lz').all()
        print(f"Found {len(elements)} elements with class css-mzz7lz")
        
        for i, el in enumerate(elements):
            print(f"--- Element {i} ---")
            print(f"OuterHTML: {el.evaluate('el => el.outerHTML')}")
            print(f"InnerText: {el.inner_text()}")

if __name__ == "__main__":
    debug_structure()
