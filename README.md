# lifeIsTech

Life is Tech! のレッスン画面と理解度チェックワークを自動操作する個人用ツールです。
Chrome を CDP 経由で Playwright から操作し、回答生成に [OpenCode Go](https://opencode.ai/docs/console/go)（または Gemini）を使います。パッケージ管理は [uv](https://docs.astral.sh/uv/) です。

## セットアップ

```bash
brew install uv          # 未インストールなら
uv sync                  # .venv を作成（Python 3.15）
cp .env.example .env     # OPENCODE_GO_API_KEY を設定
uv run python main.py
```

- OpenCode Go のキーは [Console](https://opencode.ai/console/account#api-keys) で発行します。`.env` に書かない場合は `~/.local/share/opencode/auth.json` の `opencode-go` を使います。
- Gemini にするときは `config.py` の `AI_PROVIDER = "gemini"` と `GEMINI_API_KEY`。
- ログインは Chrome の `chrome_profile/` に保存されます。切れたときはログイン画面が出るまで待つので、そのまま Chrome でログインしてください（`.env` の `LIFEISTECH_ID` / `LIFEISTECH_PASSWORD` があれば自動ログインを試します）。
- 画面を出さずに動かすなら `LIFEISTECH_HEADLESS=1`。速度はヘッドフルとほぼ同じです。

## 使い方

```bash
uv run python main.py
```

| 番号 | モード |
| --- | --- |
| 1 | 開いているレッスンを自動で進める |
| 2 | 理解度チェックワークを解く |
| 3 | 未完了レッスンを探して連続実行（チェックワーク込み） |
| 4 | 一覧から番号で選んで 1 本実行 |
| 0 | 終了 |

メニューを飛ばして直接起動できます。引数はそのまま渡ります。

```bash
uv run python main.py 3 --max 1
uv run python auto_runner.py --dry-run      # 対象の一覧だけ表示
uv run python auto_runner.py --select       # レッスンを選んで実行
```

主なオプション（`auto_runner.py`）:

| オプション | 説明 |
| --- | --- |
| `--dry-run` / `--list` | 対象レッスンの一覧だけ表示 |
| `--scope chapter\|course` | 最初の未完了章だけ / コース全体（既定は `config.AUTO_SCOPE`） |
| `--chapter "第5章"` | 章を指定 |
| `--max N` | 実行する本数の上限（0 で無制限） |
| `--no-checkwork` | レッスン完了後の理解度チェックワークを解かない |
| `--stop-on-failure` | 自動化できなかった時点で停止する |

同じツールの二重起動は `.lifeistech.lock` で防いでいます（2 つ目は起動時に終了します）。

## 仕組み（要点）

- クイズの正解はレッスンのシナリオ JSON から取得するので AI は使いません。AI はチェックワークの回答生成にだけ使います（コードや図が画像の問いは、設問カードを画像として読ませます）。
- コード課題はシナリオの正解を Ace エディタへ実際のキー入力で書き込みます。プレビュー課題（表計算など）は iframe 内の成功条件を読んでセルを操作します。
- 待機は固定 sleep ではなく「状態が変わるまで 10ms 間隔で見る」方式です。演出（CSS トランジション）と長い `setTimeout` はページ内に注入して短縮しています（`config.py` の `DISABLE_CSS_ANIMATION` / `SHORTEN_LONG_TIMERS`）。
- 自由入力のコード検証のように正解がシナリオに無いレッスンは自動化できないため、スキップして次へ進みます。

## MCP サーバー

MCP クライアント（Claude Desktop など）から一覧取得と実行を呼べます。

```json
{
  "mcpServers": {
    "lifeistech": {
      "command": "/Users/minotto/dev/lifeIsTech/.venv/bin/python",
      "args": ["/Users/minotto/dev/lifeIsTech/mcp_server.py"]
    }
  }
}
```

ツールは `chrome_status` / `list_courses` / `list_lessons` / `start_lesson` / `start_checkwork` / `start_auto` / `job_status` / `job_stop` / `list_jobs` の 9 個。
実行はジョブ（子プロセス）に任せてすぐ `job_id` を返すので、進捗は `job_status` でポーリングしてください。同時実行は 1 ジョブです。Chrome が起動していないと一覧系は失敗します。

## 設定

挙動・セレクタ・タイムアウトは [config.py](config.py) に集約しています。サイトの更新で動かなくなったら、まずセレクタを確認してください。詳細な設定値はコード内のコメントに書いてあります。

計測したいときは `LIFEISTECH_STEP_LOG=/tmp/steps.jsonl uv run python main.py` で記録し、`uv run python step_report.py /tmp/steps.jsonl` で集計できます。

## 注意

- 学習サービス側の利用規約に抵触する可能性があります。自己責任で利用してください。
- `chrome_profile/` にはログインセッションが入ります。共有・コミットしないでください（`.gitignore` 済み）。
- 新しい処理を足すときは、クリックは `dom.click()`、待機は `poll.poll_until()` / `poll.poll_true()` を使ってください（個別の `sleep` は書かない）。

## 構成

| ファイル | 役割 |
| --- | --- |
| `main.py` | Chrome 起動・モード選択 |
| `auto_runner.py` | 未完了レッスンの選択と連続実行 |
| `lifeistech_auto.py` | レッスン画面の自動進行 |
| `work_automation.py` | 理解度チェックワークの自動解答 |
| `member_api.py` | 受講者 API のクライアント |
| `ai_analyzer.py` | 回答生成（OpenCode Go / Gemini） |
| `browser.py` / `chrome_session.py` | Chrome 起動・ログイン待ち・CDP 接続 |
| `dom.py` / `poll.py` | クリックと待機の共通化 |
| `ui.py` | 表示（rich による色分け） |
| `mcp_server.py` / `jobrunner.py` | MCP サーバーとジョブ実行 |
| `config.py` | 設定とセレクタ |
