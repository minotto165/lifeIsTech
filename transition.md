# Life is Tech! 自動化プロジェクト 引継ぎ資料

## プロジェクト概要
高校情報Iの学習プラットフォーム「Life is Tech! Lesson」の学習プロセスを自動化するためのプロジェクトです。生徒が手動で行っている「スライドの遷移（Enterキー入力）」や「クイズの選択（Society 5.0などの特定のキーワード検出）」、「レッスン開始時のボタン操作」をAIとブラウザ自動化ツール（Playwright）を用いて自律的に完遂させることを目標としています。

## 現在のステータス
- **目的:** Life is Tech! レッスンの自動化（スライド遷移、クイズ回答）。
- **技術スタック:** Python 3.12, Playwright, Gemini API (gemini-2.5-flash-lite)。
- **現在の進捗:** 
  - ブラウザ自動接続（CDP経由）の実装完了。
  - クイズの「Society 5.0」「ユビキタスコンピューティング」等の正解選択ロジックの実装完了。
  - 遷移ボタンのクリックとEnterキー送信によるスライド送りロジックの実装完了。

## ファイル構造
- `lifeistech_auto.py`: メインの自動化スクリプト。

## 直近の課題
- **スライド遷移の不具合:** 前回の修正で `if not clicked:` の条件分岐を入れたことにより、遷移が不安定になった。
- **改善案:** `if not clicked:` を削除し、ボタンクリックの有無に関わらず `Enter` を確実に送信するロジックに戻すことで安定する。

## 次のセッションで行うべきこと
1. `lifeistech_auto.py` の修正（`if not clicked:` の削除）。
2. レッスン画面での動作確認。
3. 必要に応じて API キーの環境変数設定を再確認する。

## 注意事項
- `G:\マイドライブ\nhs\others\lifeIsTech` にすべてのファイルがあります。
- Python環境は `C:\Users\minotto\AppData\Local\Programs\Python\Python312\python.exe` を使用してください。
