import os
import time
from playwright.sync_api import sync_playwright
from ai_analyzer import get_ai_response

def solve_work():
    print("--- 理解度チェックワーク自動化を開始します ---")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]

        while True:
            # 採点画面の検知 (問題文がない画面)
            grade_btn = page.locator('button:has-text("この内容で採点する")')
            if grade_btn.count() > 0:
                print("DEBUG: 採点画面を検知。採点します。")
                grade_btn.first.click()
                time.sleep(2)
                break

            # 1. 問題文の取得: .css-mzz7lz を探し、問題文と思われる長いテキストを採用する
            question_locs = page.locator('span.css-mzz7lz')
            
            # 待機ループ
            retry_count = 0
            while question_locs.count() < 2 and retry_count < 5:
                # 待機中に採点ボタンが出現しないか再チェック
                if grade_btn.count() > 0:
                    break
                print("DEBUG: 問題文を待機中...")
                time.sleep(2)
                retry_count += 1
            
            # 採点画面判定の再確認
            if grade_btn.count() > 0:
                continue

            if question_locs.count() < 2:
                print("DEBUG: 問題文が見つかりません。")
                break
            
            # 2番目の要素（インデックス1）を問題文として採用
            question_text = question_locs.nth(1).inner_text().strip()
            
            if not question_text:
                print("DEBUG: 問題文の特定に失敗しました。")
                break
            
            # 2. 選択肢を取得
            radio_labels = page.locator('label')
            options = {}
            for i in range(radio_labels.count()):
                label = radio_labels.nth(i)
                text = label.inner_text().strip()
                if text and len(text) < 50: # あまり長すぎるラベルは除外
                    input_el = label.locator('input')
                    options[text] = input_el


            
            print(f"--- [AI解析] 問題: {question_text} ---")
            print(f"--- [AI解析] 選択肢: {list(options.keys())} ---")
            
            # AIに回答を依頼
            prompt = f"問題: {question_text}\n選択肢: {list(options.keys())}\n正解の選択肢のテキストを一つだけ、余計な言葉なしで返してください。"
            result = get_ai_response(prompt)
            
            if result:
                result = result.strip()
                print(f"--- [AI解析結果] AIの回答: {result} ---")
                
                # 回答をクリック
                for text, input_el in options.items():
                    if text in result or result in text:
                        print(f"DEBUG: '{text}' を選択")
                        input_el.click()
                        # 回答後のsleepは最小限にする
                        time.sleep(0.1)
                        break
            
            # 「次へ進む」または「解答内容を確認/解答確認へ」または「この内容で採点する」をクリック
            next_btn = page.locator('button:has-text("次へ進む")')
            confirm_btn = page.locator('button:has-text("解答内容を確認"), button:has-text("解答確認へ")')
            grade_btn = page.locator('button:has-text("この内容で採点する")')
            
            if next_btn.count() > 0:
                print("DEBUG: 次へ進む")
                next_btn.first.click()
                time.sleep(0.5)
            elif confirm_btn.count() > 0:
                print("DEBUG: 解答内容を確認")
                confirm_btn.first.click()
                time.sleep(0.5)
            elif grade_btn.count() > 0:
                print("DEBUG: この内容で採点する")
                grade_btn.first.click()
                time.sleep(0.5)
                # 採点後はワークが終了するため、ここでループを抜けるのが望ましい
                print("DEBUG: 採点ボタン押下によりワーク自動化を終了します")
                break
            else:
                print("DEBUG: 遷移ボタンが見つかりません。終了した可能性があります。")
                break

if __name__ == "__main__":
    solve_work()
