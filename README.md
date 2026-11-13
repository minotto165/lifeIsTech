# lifeIsTech

Life is Tech! のレッスン画面・理解度チェックワークを自動操作する個人用ツールです。
Chrome を CDP (Chrome DevTools Protocol) 経由で操作し、クイズの回答生成に [OpenCode Go](https://opencode.ai/docs/console/go)（または Gemini API）を使います。
パッケージ管理には [uv](https://docs.astral.sh/uv/) を使用しています。

## 動作の概要

1. `browser.py` が Chrome を起動する（`chrome_profile/` にログイン情報を保持）。
   `main.py` だけでなく各スクリプト（`auto_runner.py` など）を単体で実行しても、
   Chrome が起動していなければ自動で起動する
2. Playwright がデバッグポート 9300 で既存 Chrome に接続する。
   セッションが切れていてログイン画面（`b2clogin.com`）に飛んだ場合は、
   開いている Chrome でログインするまで待つ（`LOGIN_WAIT_SEC`）
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

### 自動ログイン（任意）

セッションが切れてログイン画面（`b2clogin.com`）になったとき、次の順で自動ログインを試みます。

1. `.env` の `LIFEISTECH_ID` / `LIFEISTECH_PASSWORD` を入力して送信
2. 未設定なら、Chrome のパスワード保存で自動入力済みの値があればそれを送信

どちらも失敗した場合は「開いている Chrome でログインしてください」と表示し、
`LOGIN_WAIT_SEC`（既定 300 秒）まで手動ログインを待ちます。
パスワードを平文で保存することになるため、`.env` はコミットしないでください（`.gitignore` 済み）。

### ヘッドレスで動かす

`LIFEISTECH_HEADLESS=1 uv run python main.py` で Chrome が `--headless=new` で起動し、画面を出さずに実行します。
CDP 経由の操作・スクリーンショットはヘッドフルと同じです。

- 画面が無いので**手動ログインはできません**。セッション切れに備えて `.env` の
  `LIFEISTECH_ID` / `LIFEISTECH_PASSWORD` を設定しておくのが安全です
  （Chrome の保存パスワードの自動入力はヘッドレスでは効かないことがあります）。
- 同じ `chrome_profile` を同時に 2 つ起動できないため、ヘッドフルと切り替えるときは
  先に Chrome を終了してください。
- 実行速度はヘッドフルとほぼ同じでした（レッスン 04_02_01 を最初から:
  ヘッドフル 97〜103 秒 / 411〜423 ms per step、ヘッドレス 99.1 秒 / 411 ms・102.9 秒 / 445 ms）。

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
3: 全自動（未完了レッスン＋理解度チェックワークを連続実行）
4: レッスンを選んで実行（一覧から番号で選択・チェックワーク込み）
0: 終了
```

- `1` レッスン自動化: 次へ進む・クイズ解答・開始ボタン操作を繰り返す
- `2` 理解度チェックワーク: 問題文から正解を選び、確認・採点まで進める
- `3` 全自動: 未完了レッスンを API から探し、開始ボタンを押して完了まで連続実行し、続けてそのレッスンの理解度チェックワークも解く
- `4` レッスンを選んで実行: コースの全レッスンを状態付きで一覧表示し、番号を入力して 1 本だけ実行する（チェックワークも続けて解く）
- `0` 終了（このツールが起動した Chrome は終了時に閉じられる）

個別に実行することもできます（Chrome がデバッグポートで起動済みであること）。

```bash
uv run python lifeistech_auto.py
uv run python work_automation.py
uv run python auto_runner.py --dry-run   # 対象の一覧だけ表示
uv run python auto_runner.py             # 未完了レッスンを連続実行
uv run python auto_runner.py --select    # レッスン一覧から番号で選んで 1 本実行
uv run python main.py 3 --max 1          # メニューを出さずにモード3を引数付きで起動
uv run python main.py 4                  # メニューを出さずにモード4（レッスン選択）を起動
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
7. クリアを確認したら、そのレッスンに理解度チェックワークがあれば「理解度チェックワークを始める」から開き、
   設問を AI で解いて「この内容で採点する」まで進める（`--no-checkwork` で無効化）。
   未回答の問題があると採点ボタンは押せないので、各設問で「選択肢が選ばれているか」を見てから次へ進む。
   問題にコードや図の画像が付いている場合は、設問カードをそのまま画像にして AI（Muse Spark の画像入力）に読ませ、
   正解の選択肢の番号を受け取ってクリックする（画像だけの選択肢にも対応）。
   同じ問題文と選択肢の組は `.cache/work_answers.json` にキャッシュし、2 回目以降は AI を呼ばずに数秒で採点まで進む

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
| `--select` / `--choose` | 完了済みも含めたレッスン一覧を番号付きで表示し、選んだ 1 本だけを実行する（`q` で終了） |
| `--checkwork` / `--no-checkwork` | レッスン完了後に理解度チェックワークも解くか（既定は解く。`config.AUTO_RUN_CHECKWORK`） |

### レッスンを選んで実行（`--select`）

メニューの `4`、または `--select` を付けると、章ごとに区切った一覧を出して番号の入力待ちになります。

```
--- 第2章 コミュニケーションと情報デザイン
 14. 09①「情報デザイン①」 [完了]
 15. 09②「情報デザイン②」 [完了]
```

状態は `完了` / `途中` / `未着手` / `ロック` の 4 種類です。ロック中のレッスンは選べません。
完了済みのレッスンを選んだ場合は「最初からレッスンをやり直す」から再生し直します
（進捗を残したまま試したいときは `3` の全自動モードを使ってください）。
`--chapter` / `--course` で一覧を絞り込めます。
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

待機は「固定時間ねばる」のではなく「状態が変わるまで 10ms 間隔で見る」方式です。
クイズの選択肢・進行ボタン・コード課題の結果ウィンドウ・実行ボタン・プレイヤーの描画は、
押せる／進んだ瞬間に次の動作へ移ります（タイムアウト値は打ち切り上限としてだけ使います）。
間隔は `FAST_POLL_SEC`（DOM）・`NAV_POLL_SEC`（画面遷移）・`API_POLL_SEC`（会員 API）で調整できます。

そのほか、`LIFEISTECH_PROFILE=1` で 100 周ごとのループ内訳（read / skip / enter / action のミリ秒）とクイズ・コード課題の所要時間、
`LIFEISTECH_CALIB=1` で Enter の送信間隔を変えながらスループットを比較する計測が有効になります。

### 表示と色分け

CLI の表示は [ui.py](ui.py) にまとめてあり、[rich](https://github.com/Textualize/rich) で色分けしています。
成功（クリア・正解・完了）は緑、警告（スキップ・未対応）は黄、エラー（失敗・停止）は赤、ステップ通過などの
定型的なログは淡色で出ます。レッスン一覧や実行モードの選択は表形式です。
`rich` が入っていない環境でも素の `print` に落ちてそのまま動きます。
色を止めたいときは `NO_COLOR=1` を設定してください（`TERM=dumb` でも無効になります）。

### 依存関係の更新

```bash
uv add <package>      # 追加
uv lock --upgrade     # 更新
uv sync               # 環境へ反映
```

ターミナル表示には `rich` を使っています（`uv add rich` 済み）。

## 設定

挙動・セレクタ・タイムアウト・誤答率などは [config.py](config.py) に集約しています。
サイトのフロントエンドが更新されて動かなくなった場合は、まず `config.py` のセレクタを確認してください。

| 設定 | 既定値 | 説明 |
| --- | --- | --- |
| `CHROME_DEBUG_PORT` | 9300 | Chrome のリモートデバッグポート（環境変数 `LIFEISTECH_CHROME_PORT` で上書き可） |
| `CHROME_PROFILE_DIR` | chrome_profile | Chrome のプロファイル（環境変数 `LIFEISTECH_CHROME_PROFILE` で上書き可） |
| `HEADLESS` | False | Chrome をヘッドレスで起動する（環境変数 `LIFEISTECH_HEADLESS=1`） |
| `LOGIN_WAIT_SEC` | 300.0 | ログイン画面だったときに手動ログインを待つ上限（秒、環境変数 `LIFEISTECH_LOGIN_WAIT`） |
| `AI_PROVIDER` | opencode_go | 回答生成に使うプロバイダ（`opencode_go` / `gemini`） |
| `OPENCODE_GO_MODEL` | muse-spark-1.3-contributor | OpenCode Go で使うモデル |
| `OPENCODE_GO_REASONING_EFFORT` | minimal | 推論量（`minimal` で 1.5 秒前後、`None` で送らない） |
| `OPENCODE_GO_MAX_TOKENS` | 1024 | 出力上限。推論トークンも含むので小さすぎると本文が空になる |
| `GEMINI_MODEL` | gemini-2.5-flash-lite | Gemini に切り替えたときのモデル |
| `ENTER_SPAM_DELAY` | 0.0 | Enter 連打の間隔（0 で待機なし） |
| `FAST_POLL_SEC` | 0.01 | 条件待ち（クイズ・ボタン・エディタなど DOM）のポーリング間隔 |
| `NAV_POLL_SEC` | 0.05 | 画面遷移・プレイヤー描画待ちのポーリング間隔 |
| `API_POLL_SEC` | 0.25 | 会員 API（クリア確認など）のポーリング間隔 |
| `QUIZ_ENTER_INTERVAL` | 0.01 | クイズを閉じるときに Enter を送り直す間隔（閉じたら即終了。正解表示とモーダルの2段階で閉じるため短いほど速い） |
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
| `CODE_EDITOR_READY_TIMEOUT` | 5.0 | コード課題のエディタが操作できるようになるまで待つ上限（秒） |
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
| `AUTO_RUN_CHECKWORK` | True | レッスン完了後に理解度チェックワークも続けて解く（`--no-checkwork` で無効化） |
| `WORK_STEP_TIMEOUT` | 5.0 | チェックワークで設問が切り替わるまで待つ上限（秒） |
| `WORK_ANSWER_CACHE` | `.cache/work_answers.json` | チェックワークの解答キャッシュ（同じ問題は AI を呼ばない） |
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
「コードチェック」を押すと判定結果のウィンドウが開きますが、開いている間はステップが進まないため、開いた瞬間（実測 0.02〜0.04 秒）に閉じ、Python のレッスンでは続けて「実行」を押します。ステップ番号・結果ウィンドウ・実行ボタンは 1 往復の JS でまとめて監視しています。

**注意**: `.python-version` は Python 3.15.2 を指定しています。別のバージョンを使いたい場合はこのファイルと `pyproject.toml` の `requires-python` を変更し、`uv sync --python <version>` を実行してください。

## 注意

- 本ツールは学習サービス側の利用規約に抵触する可能性があります。自己責任で利用してください。
- `chrome_profile/` にはログインセッションが含まれます。共有・コミットしないでください（`.gitignore` 済み）。
- クリックは `dom.click()`、待機は `poll.poll_until()` / `poll.poll_true()` に集約しています。新しい処理を足すときは個別に `sleep` を書かず、この 2 つを使ってください（タイムアウトは打ち切り上限として渡す）。
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
| `browser.py` | Chrome の起動・CDP 接続前の準備・ログイン待ち |
| `chrome_session.py` | CDP 接続の共通化（対象タブが見つからなければ停止） |
| `dom.py` | DOM 操作の共通化（可視要素のクリック・1 往復の検索とクリック） |
| `poll.py` | 「状態が変わるまで待つ」の共通化（固定 sleep の置き換え） |
| `human_utils.py` | 待機・クリックの人間らしい揺らぎ |
| `instance_guard.py` | 二重起動の防止（ロックファイル） |
| `step_report.py` | ステップごとの所要時間ログの集計 |
| `ui.py` | CLI の表示（色分け・一覧表）。rich が無ければ素の print |
| `config.py` | 設定とセレクタの集約 |
| `pyproject.toml` | プロジェクト定義・依存関係 |
| `uv.lock` | 依存関係のロックファイル |
| `.python-version` | 使用する Python バージョン |
