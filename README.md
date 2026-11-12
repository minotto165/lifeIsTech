# lifeIsTech

Life is Tech! のレッスン画面・理解度チェックワークを自動操作する個人用ツールです。
Chrome を CDP (Chrome DevTools Protocol) 経由で操作し、クイズの回答生成に Gemini API を使います。
パッケージ管理には [uv](https://docs.astral.sh/uv/) を使用しています。

## 動作の概要

1. `main.py` が Chrome を起動する（`chrome_profile/` にログイン情報を保持）
2. Playwright がデバッグポート 9222 で既存 Chrome に接続する
3. 選択したモードのスクリプトが DOM を走査し、クイズ回答・画面遷移を自動化する
4. 記述問題のページ（自動化対象外）を検知すると終了する

## セットアップ

uv が未インストールの場合:

```bash
brew install uv
```

依存関係をインストールします（`.python-version` に従い Python 3.15 の仮想環境 `.venv` が作られます）。

```bash
uv sync
```

`.env` を作成し、Gemini API キーを設定します。

```bash
cp .env.example .env
# .env を編集して GEMINI_API_KEY を設定
```

キーが未設定の場合、AI 解析はスキップされ画面遷移のみ行われます。

### エディタの設定

エディタの Python インタープリタを `.venv/bin/python` に切り替えてください
（VS Code は `.vscode/settings.json` で設定済み）。
切り替え後は言語サーバーを再起動すると `Unable to import 'playwright.sync_api'` は解消します。

## 使い方

```bash
uv run python main.py
```

```
実行モードを選択してください
1: レッスン自動化
2: 理解度チェックワーク
0: 終了
```

- `1` レッスン自動化: 次へ進む・クイズ解答・開始ボタン操作を繰り返す
- `2` 理解度チェックワーク: 問題文から正解を選び、確認・採点まで進める
- `0` 終了（このツールが起動した Chrome は終了時に閉じられる）

個別に実行することもできます（Chrome がポート 9222 で起動済みであること）。

```bash
uv run python lifeistech_auto.py
uv run python work_automation.py
```

### 依存関係の更新

```bash
uv add <package>      # 追加
uv lock --upgrade     # 更新
uv sync               # 環境へ反映
```

## 設定

挙動・セレクタ・タイムアウト・誤答率などは [config.py](config.py) に集約しています。
サイトのフロントエンドが更新されて動かなくなった場合は、まず `config.py` のセレクタを確認してください。

| 設定 | 既定値 | 説明 |
| --- | --- | --- |
| `CHROME_DEBUG_PORT` | 9222 | Chrome のリモートデバッグポート |
| `GEMINI_MODEL` | gemini-2.5-flash-lite | 回答生成に使うモデル |
| `MAX_LOOP_ITERATIONS` | 50000 | 自動操作ループの上限 |
| `ENTER_SPAM_DELAY` | 0.0 | Enter 連打の間隔（0 で待機なし） |
| `STUCK_CLICK_AFTER` | 1.0 | これだけ進まなければ「次へ」クリックに切り替える秒数 |
| `STUCK_GIVEUP_AFTER` | 30.0 | これだけ進まなければ終了する秒数 |
| `QUIZ_FEEDBACK_WAIT` | 4.0 | 回答後「正解／不正解」が出るまで待つ上限（秒） |
| `BROKEN_QUIZ_WAIT` | 2.5 | クイズが固まってからページ再読込するまでの秒数 |
| `SKIP_ANIMATION_WAIT` | True | 演出（CSS トランジション）の完了待ちを飛ばす |
| `ACTION_CLICK_TIMEOUT_MS` | 800 | 進行ボタンのクリックを諦めるまでのミリ秒数 |
| `WRONG_ANSWER_RATE` | 0.15 | わざと誤答する確率 |

プレイヤーは各ステップの終了条件に「AnimationFinish」を持っており、実際には要素の CSS トランジション（既定 900ms）が終わるまで次へ進みません。Enter では縮められないため、`SKIP_ANIMATION_WAIT` を有効にすると `transitionend` を擬似的に発火させて待ちを飛ばします（キャラクターの移動などの演出は瞬間的になります）。演出をきちんと見たいときは `False` にしてください。

クイズは「回答 → 正解表示が出るまで待つ → Enter」の順で進めます。正解表示より先に Enter を送ると、次の問題のモーダルが選択肢 disabled のまま固まり、Enter では復帰しないためです（`BROKEN_QUIZ_WAIT` を超えたら自動でページを再読込します）。

**注意**: `.python-version` は Python 3.15 を指定しています。別のバージョンを使いたい場合はこのファイルと `pyproject.toml` の `requires-python` を変更し、`uv sync --python <version>` を実行してください。

## 注意

- 本ツールは学習サービス側の利用規約に抵触する可能性があります。自己責任で利用してください。
- `chrome_profile/` にはログインセッションが含まれます。共有・コミットしないでください（`.gitignore` 済み）。
- セレクタは `config.py` に集約しています。サイト更新で壊れることがあるので、動かないときはまず実 DOM を確認してください（プレイヤーは React 製で、`<button>` は使われていません）。

## 構成

| ファイル | 役割 |
| --- | --- |
| `main.py` | Chrome 起動・モード選択のエントリーポイント |
| `lifeistech_auto.py` | レッスン画面の自動進行 |
| `work_automation.py` | 理解度チェックワークの自動解答 |
| `ai_analyzer.py` | Gemini API 呼び出し（リトライ・タイムアウト付き） |
| `chrome_session.py` | CDP 接続の共通化 |
| `human_utils.py` | 待機・クリックの人間らしい揺らぎ |
| `config.py` | 設定とセレクタの集約 |
| `pyproject.toml` | プロジェクト定義・依存関係 |
| `uv.lock` | 依存関係のロックファイル |
| `.python-version` | 使用する Python バージョン |
