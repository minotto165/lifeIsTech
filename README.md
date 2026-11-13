# lifeIsTech

Life is Tech! のレッスン画面・理解度チェックワークを自動操作する個人用ツールです。
Chrome を CDP (Chrome DevTools Protocol) 経由で操作し、クイズの回答生成に [OpenCode Go](https://opencode.ai/docs/console/go)（または Gemini API）を使います。
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

`.env` を作成し、回答生成に使う API キーを設定します。

```bash
cp .env.example .env
# .env を編集して OPENCODE_GO_API_KEY を設定
```

OpenCode Go のキーは [OpenCode Console](https://opencode.ai/console/account#api-keys) で発行します。
`.env` に書かずに opencode CLI でログイン済みの場合、`~/.local/share/opencode/auth.json` の
`opencode-go` のキーをそのまま使います（期限切れだと 401 になるので、その場合は Console で作り直してください）。

Gemini に切り替えたいときは `config.py` の `AI_PROVIDER` を `"gemini"` にし、`GEMINI_API_KEY` を設定します。
OpenCode Go が使えない場合は `AI_FALLBACK_TO_GEMINI` に従って自動で Gemini に落ちます。
どちらのキーも無い場合、AI 解析はスキップされ画面遷移のみ行われます。

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
3: 全自動（未完了レッスンを自動選択して連続実行）
0: 終了
```

- `1` レッスン自動化: 次へ進む・クイズ解答・開始ボタン操作を繰り返す
- `2` 理解度チェックワーク: 問題文から正解を選び、確認・採点まで進める
- `3` 全自動: 未完了レッスンを API から探し、開始ボタンを押して完了まで連続実行する
- `0` 終了（このツールが起動した Chrome は終了時に閉じられる）

個別に実行することもできます（Chrome がポート 9222 で起動済みであること）。

```bash
uv run python lifeistech_auto.py
uv run python work_automation.py
uv run python auto_runner.py --dry-run   # 対象の一覧だけ表示
uv run python auto_runner.py             # 未完了レッスンを連続実行
uv run python main.py 3 --max 1          # メニューを出さずにモード3を引数付きで起動
```

同じツールが二重に走ると同じレッスンへ同時に Enter を送ってしまうため、実行中は
`.lifeistech.lock` をロックします（2 つ目のプロセスは起動時に終了します）。

### 全自動モード（`auto_runner.py`）

受講者向け API（`https://api.lifeistech-lesson.jp/api`）に Chrome の Cookie `mozermovie` を
Bearer トークンとして付けて進捗を取得し、次の順で実行します。

1. `players/textbooks/courses` からコース（既定は「情報Ⅰ」）を選ぶ
2. 章を `sort_no` 順に見て、`status` が `cleared` でないレッスンを集める（既定 `AUTO_SCOPE = "chapter"` では
   最初の未完了章が見つかった時点でその章だけに絞る）
3. レッスン画面を開き、「レッスンスタート」「レッスンの続きから再開」「最初からレッスンをやり直す」の
   うち表示されているものを押してプレイヤーへ遷移する
4. 既存の高速自動進行で完了まで進め、API の `player_lesson.status` が `cleared` になったことを確認する
5. 未クリアのときは `continue_link.player_url` から続きを 1 回だけ再開する。それでも駄目なら、
   そのレッスンは未完了のまま記録して次のレッスンへ進む（`--stop-on-failure` を付けたときだけ停止する）
6. EX① のように最初に「端末の環境を選択」が出るレッスンは、実行中の OS（Mac / Windows / Chromebook）に
   合うリンクとボタンを自動で選ぶ

停止したときや自動化できなかったときは理由を表示し、画面のスクリーンショットを `screenshots/stop_<日時>_<理由>.png` に残します
（ディレクトリは所有者のみ読める権限で作り、古いものは `SCREENSHOT_KEEP` 枚を超えた分から消します）。
ログインセッションが切れていると `AuthError` で停止するので、Chrome でログインし直してから再実行してください。

| オプション | 説明 |
| --- | --- |
| `--dry-run` / `--list` | 対象レッスンの一覧だけ表示して終了する |
| `--course NAME` | コース名を変更する（既定は `config.AUTO_COURSE_NAME`） |
| `--scope chapter\|course` | `chapter` は最初の未完了章のみ、`course` はコース全体 |
| `--chapter "第5章"` | 章を明示的に絞り込む |
| `--max N` | 実行するレッスン数の上限（0 で無制限） |
| `--lesson ID` | そのレッスン ID だけを実行する（対象一覧に無ければエラー） |
| `--stop-on-failure` | 自動化できなかったレッスンで停止する（既定は記録して次へ進む） |

レッスン画面の URL は `https://member.lifeistech-lesson.jp/textbooks/{textbookId}/chapters/{chapterId}/lessons/{lessonId}` です。
開始は画面のボタンから行う必要があります（SPA が受講レコードを作ってからプレイヤーへ遷移するため、
プレイヤーの URL を直接開くと受講状態が更新されません）。

### 速度を計測する

ステップごとの所要時間と、そのときの画面構成を記録できます。

```bash
LIFEISTECH_STEP_LOG=/tmp/steps.jsonl uv run python main.py   # 記録する
uv run python step_report.py /tmp/steps.jsonl                # 集計する
```

1 ステップ 1 行の JSON（`step` / `sec` / `loops` / `marks` / `handled` / `exit` / `elements` / `talk_chars`）で、
`exit` は終了条件（`TalkFinish` / `AnimationFinish` / `ValidationFinish` / `Click`）、`elements` はそのステップの要素構成、
`marks` はステップ開始から「吹き出し・ボタン・クイズ・エディタ」が初めて現れた秒数、`handled` は自動処理した種類です。
`step` が戻ったところで別の実行として分割して集計します。

そのほか、`LIFEISTECH_PROFILE=1` で 100 周ごとのループ内訳（read / skip / enter / action のミリ秒）とクイズ・コード課題の所要時間、
`LIFEISTECH_CALIB=1` で Enter の送信間隔を変えながらスループットを比較する計測が有効になります。

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
| `CHROME_DEBUG_PORT` | 9300 | Chrome のリモートデバッグポート（環境変数 `LIFEISTECH_CHROME_PORT` で上書き可） |
| `AI_PROVIDER` | opencode_go | 回答生成に使うプロバイダ（`opencode_go` / `gemini`） |
| `OPENCODE_GO_MODEL` | muse-spark-1.3-contributor | OpenCode Go で使うモデル |
| `OPENCODE_GO_REASONING_EFFORT` | minimal | 推論量（`minimal` で 1.5 秒前後、`None` で送らない） |
| `OPENCODE_GO_MAX_TOKENS` | 1024 | 出力上限。推論トークンも含むので小さすぎると本文が空になる |
| `GEMINI_MODEL` | gemini-2.5-flash-lite | Gemini に切り替えたときのモデル |
| `ENTER_SPAM_DELAY` | 0.0 | Enter 連打の間隔（0 で待機なし） |
| `STUCK_CLICK_AFTER` | 0.35 | これだけ進まなければ進行ボタンのクリックに切り替える秒数 |
| `STUCK_GIVEUP_AFTER` | 30.0 | これだけ進まなければ終了する秒数 |
| `QUIZ_FEEDBACK_WAIT` | 4.0 | 回答後「正解／不正解」が出るまで待つ上限（秒） |
| `BROKEN_QUIZ_WAIT` | 2.5 | クイズが固まってからページ再読込するまでの秒数 |
| `SKIP_ANIMATION_WAIT` | True | 演出（CSS トランジション）の完了待ちを飛ばす |
| `DISABLE_CSS_ANIMATION` | True | キャラクターの移動・要素のフェードなど CSS アニメーションをほぼ 0 秒にする |
| `SHORTEN_LONG_TIMERS` | True | ステップの `waitMilliseconds` など長い `setTimeout` 待ちを短縮する |
| `LONG_TIMER_TRIGGER_MS` | 1000 | この値以上の待ちを短縮の対象にする（ミリ秒） |
| `LONG_TIMER_CAP_MS` | 200 | 短縮後の待ち時間（ミリ秒） |
| `ACTION_CLICK_TIMEOUT_MS` | 800 | 進行ボタンのクリックを諦めるまでのミリ秒数 |
| `CLICK_SHORT_WAIT_SEC` | 0.2 | クリック課題の要素を待つ時間（confirm-button 以外） |
| `CLICK_WAIT_SEC` | 2.5 | クリック課題の要素を待つ上限（confirm-button）。OK ボタンは約 2 秒後に描画される |
| `WRONG_ANSWER_RATE` | 0.0 | わざと誤答する確率（既定は正答のみ） |
| `QUIZ_GIVEUP_AFTER` | 15.0 | 同じクイズが解けずに残ったら再読込するまでの秒数 |
| `QUIZ_MAX_RELOADS` | 2 | 1 つのクイズで再読込する回数の上限 |
| `MAX_WORK_STEPS` | 200 | チェックワークで進める設問数の上限 |
| `WORK_STALL_LIMIT` | 5 | 同じ問題が続いたら停止するまでの連続回数 |
| `MAX_OPTION_TEXT_LENGTH` | 300 | 選択肢として扱うテキストの最大長 |
| `CODE_VERIFY_APPLIED` | True | コード課題の入力後にエディタ内容を照合する |
| `PREVIEW_READY_TIMEOUT` | 10.0 | プレビュー（表計算など）の成功条件を読むまで待つ上限（秒） |
| `PREVIEW_MAX_ATTEMPTS` | 3 | 同じプレビュー課題を解き直す回数の上限 |
| `MEMBER_API_BASE` | https://api.lifeistech-lesson.jp/api | 進捗取得に使う受講者 API |
| `AUTO_COURSE_NAME` | 情報Ⅰ | 全自動で対象にするコース名 |
| `AUTO_SCOPE` | chapter | `chapter` は最初の未完了章のみ、`course` はコース全体 |
| `AUTO_CHAPTER_LABEL` | None | 章を明示的に絞る（例 `"第5章"`） |
| `AUTO_MAX_LESSONS` | 0 | 実行するレッスン数の上限（0 で無制限） |
| `AUTO_RESTART_PARTIAL` | True | 途中まで進んだレッスンを最初からやり直すことを許可する |
| `AUTO_CLEAR_WAIT_SEC` | 30.0 | 完了（`cleared`）を待つ上限（秒） |
| `LESSON_PAGE_READY_TIMEOUT` | 30.0 | レッスン画面の開始ボタンを待つ上限（秒） |
| `AUTO_RUN_CHECKWORK` | False | レッスン完了後に理解度チェックワークも続けて解く |
| `SCREENSHOT_DIR` | screenshots | 停止時のスクリーンショットの保存先 |
| `SCREENSHOT_KEEP` | 30 | 保存しておくスクリーンショットの枚数 |
| `LOCK_FILE` | .lifeistech.lock | 二重起動を防ぐロックファイル |
| `APPLY_WEBDRIVER_PATCH` | False | `navigator.webdriver` を隠す init script を入れる |

プレイヤーは各ステップの終了条件に「AnimationFinish」を持っており、実際には要素の CSS トランジション（既定 900ms）が終わるまで次へ進みません。Enter では縮められないため、`SKIP_ANIMATION_WAIT` を有効にすると `transitionend` を擬似的に発火させて待ちを飛ばします（キャラクターの移動などの演出は瞬間的になります）。演出をきちんと見たいときは `False` にしてください。

ページには 2 種類の注入を行います。1 つは CSS（`DISABLE_CSS_ANIMATION`）で、`transition-duration` と `animation-duration` を 1ms に潰します。0 秒にすると `transitionend` が発火せず、かえって止まるので 1ms 残しています。もう 1 つは JS（`SHORTEN_LONG_TIMERS`）で、`window.setTimeout` を包み、1 秒以上の待ちを 200ms に縮めます。シナリオの `exitBehaviourList` に `waitMilliseconds: 8000` のような「無操作で待つ」指定が入っていることがあり（例: 04_02_01 の最終ステップ）、それだけで 8 秒失うためです。

進行ボタンはシナリオ JSON の `ButtonElement` だけです（`<button class="btn-next btn-next-console-up-right ...">` のように描画され、文言もシナリオ次第で「Next」「OK」などになります）。プレイヤー下部のプログレスバーにある `progressbar_step-btn` はステップ移動用のボタンで、進行ボタンではありません。`exitCondition` の `Click:<domId>`（例: `Click:confirm-button`）は、そのステップの `elementList` にある `ButtonElement` の `domId` を指すので、domId から実際のクラスと文言を引いてクリックします。

Muse Spark 1.3 Contributor は推論モデルのため、`max_tokens` を消費して本文が空になることがあります（既定の 1024 と `reasoning_effort: minimal` で 1.5〜2.5 秒程度）。また Console の Settings › Privacy で **Allow paid endpoints that train on request data** を有効にしないと利用できません。

クイズは「回答 → 正解表示が出るまで待つ → Enter」の順で進めます。正解表示より先に Enter を送ると、次の問題のモーダルが選択肢 disabled のまま固まり、Enter では復帰しないためです（`BROKEN_QUIZ_WAIT` を超えたら自動でページを再読込します）。正解表示とモーダル本体は 2 段階で閉じるため、Enter は閉じるまで最大 3 回まで送ります。

正解はシナリオ JSON から取得するので AI は使いません。通常のクイズ（`QuizModalElement`）に加えて、画像から選ぶクイズ（`ImageSelectQuizElement`・選択肢にテキストが無い）にも対応しています。

プレビュー課題（`DevelopWindowsElement` の iframe 内で、表計算ソフトなどを操作させるステップ）は、iframe の `window.options` に書かれた成功条件（例: `x === '0' && y === '0' && newValue === '=0.3-0.2'`）を読み取り、該当セルをダブルクリックして値を入力します。成功すると iframe 内の `send_success.js` が `parent.postMessage({type:'PreviewStatus'})` を送り、ステップが完了します。入力の要らない「見るだけ」のプレビューは自動で完了するので、条件が読めない場合は通常の進行処理に任せます。

EX① のような操作練習レッスンのプレビューは、iframe 内のスクリプトを解析して自動で操作します
（スクロール、正解セレクタのクリック、`var ans = [...]` の順番どおりのクリック、p5.play の図形を
シルエットへ重ねるドラッグ）。ただしコードを手入力して検証する形式（`ValidationFinish` +
`DevelopWindowsElement`）は正解がシナリオに入っていないため自動化できません。その場合は
「自動化できなかったレッスン」として一覧に出して次へ進みます。

コード課題（`EditorModalElement`）は、シナリオの `content.correct.codes`（ファイル名と行ごとの解答）を読み、Ace エディタの該当行を選択して **実際のキー入力** で書き込みます。`editor.setValue()` では React 側のスナップショットが更新されず、サーバー検証（`ValidationFinish`）が通らないためです。入力中は自動補完（behaviours）を切り、終わったら元に戻します。

**注意**: `.python-version` は Python 3.15.2 を指定しています。別のバージョンを使いたい場合はこのファイルと `pyproject.toml` の `requires-python` を変更し、`uv sync --python <version>` を実行してください。

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
| `auto_runner.py` | 未完了レッスンの自動選択と連続実行 |
| `member_api.py` | 受講者 API のクライアント（コース・章・レッスンの進捗取得） |
| `ai_analyzer.py` | 回答生成（OpenCode Go / Gemini、リトライ・タイムアウト付き） |
| `chrome_session.py` | CDP 接続の共通化（対象タブが見つからなければ停止） |
| `human_utils.py` | 待機・クリックの人間らしい揺らぎ |
| `instance_guard.py` | 二重起動の防止（ロックファイル） |
| `step_report.py` | ステップごとの所要時間ログの集計 |
| `config.py` | 設定とセレクタの集約 |
| `pyproject.toml` | プロジェクト定義・依存関係 |
| `uv.lock` | 依存関係のロックファイル |
| `.python-version` | 使用する Python バージョン |
