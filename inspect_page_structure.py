from playwright.sync_api import sync_playwright

def inspect_page_structure():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        
        # 画面内の全要素のテキストとセレクタ情報をダンプ
        print("--- Page content analysis ---")
        # 主要なテキストを含む可能性が高い要素を抽出
        texts = page.locator('div, span, p').all()
        for t in texts:
            try:
                inner = t.inner_text().strip()
                if inner and len(inner) < 100:
                    print(f"Tag: {t.evaluate('el => el.tagName')}, Class: {t.evaluate('el => el.className')}, Text: {inner}")
            except:
                continue

if __name__ == "__main__":
    inspect_page_structure()
