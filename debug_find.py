from playwright.sync_api import sync_playwright

def inspect_all():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        
        # ページ全体のテキストを取得して、問題文の一部で検索してみる
        # さっき確認した問題文: "簡単にコピーできるという情報の特性はどれかな？"
        
        body = page.locator('body')
        print("Searching for question text...")
        
        # テキストで要素を探してみる
        question = page.locator('text=簡単にコピーできるという情報の特性はどれかな？')
        if question.count() > 0:
            print("Found question by text!")
            # エンコードエラーを避けるためにReprを使う
            print(f"Parent tag: {question.first.evaluate('el => el.parentElement.tagName')}")
            print(f"Parent class: {question.first.evaluate('el => el.parentElement.className')}")
        else:
            print("Question not found by text.")

if __name__ == "__main__":
    inspect_all()
