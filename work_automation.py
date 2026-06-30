import os
import time
from playwright.sync_api import sync_playwright
from ai_analyzer import get_ai_response
from human_utils import human_sleep, human_click

def solve_work():
    print("--- 理解度チェックワーク自動化を開始します ---")
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    context = browser.contexts[0]
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = context.pages[0]

    while True:
        # 採点画面の検知 (問題文がない画面)
        grade_btn = page.locator('button:has-text("この内容で採点する")')
        if grade_btn.count() > 0:
            human_click(grade_btn.first, base_delay=2.0)
            break

        # 1. 問題文の取得
        question_locs = page.locator('span.css-mzz7lz')
        
        # 待機ループ
        retry_count = 0
        while question_locs.count() < 2 and retry_count < 5:
            if grade_btn.count() > 0:
                break
            human_sleep(2.0)
            retry_count += 1
        
        # 採点画面判定の再確認
        if grade_btn.count() > 0:
            continue

        if question_locs.count() < 2:
            break
        
        # 2番目の要素（インデックス1）を問題文として採用
        question_text = question_locs.nth(1).inner_text().strip()
        
        if not question_text:
            break
        
        # 2. 選択肢を取得
        radio_labels = page.locator('label')
        options = {}
        for i in range(radio_labels.count()):
            label = radio_labels.nth(i)
            text = label.inner_text().strip()
            if text and len(text) < 50:
                input_el = label.locator('input')
                options[text] = input_el
            
        # AIに回答を依頼
        prompt = f"問題: {question_text}\n選択肢: {list(options.keys())}\n正解の選択肢のテキストを一つだけ、余計な言葉なしで返してください。"
        result = get_ai_response(prompt)
        
        if result:
            result = result.strip()
            
            # 回答をクリック
            import random
            is_intentional_error = random.random() < 0.15 # 15%の確率で誤答
            
            correct_items = []
            for text, input_el in options.items():
                if text in result or result in text:
                    correct_items.append((text, input_el))
            
            if is_intentional_error and len(options) > 1:
                # 誤答対象を選定（正解以外から選ぶ）
                wrong_items = [item for item in options.items() if item not in correct_items]
                if wrong_items:
                    _, target_input = random.choice(wrong_items)
                    human_click(target_input, base_delay=0.1)
                    human_sleep(0.1)
                else:
                    if correct_items:
                        _, input_el = correct_items[0]
                        human_click(input_el, base_delay=0.1)
                        human_sleep(0.1)
            else:
                if correct_items:
                    _, input_el = correct_items[0]
                    human_click(input_el, base_delay=0.1)
                    human_sleep(0.1)

        # 遷移ボタンをクリック
        next_btn = page.locator('button:has-text("次へ進む")')
        confirm_btn = page.locator('button:has-text("解答内容を確認"), button:has-text("解答確認へ")')
        grade_btn = page.locator('button:has-text("この内容で採点する")')
        
        if next_btn.count() > 0:
            human_click(next_btn.first, base_delay=1.5)
        elif confirm_btn.count() > 0:
            human_click(confirm_btn.first, base_delay=0.5)
        elif grade_btn.count() > 0:
            human_click(grade_btn.first, base_delay=0.5)
            break
        else:
            break


if __name__ == "__main__":
    solve_work()
