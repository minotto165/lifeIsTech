"""共通設定。挙動やDOMセレクタを変えたいときはこのファイルだけを編集する。"""

import os

from dotenv import load_dotenv

# .env を先に読み込む（各スクリプトは config を最初に import するため）
load_dotenv()

# --- Chrome / CDP ---
# 9222 は他のツール（DSH の内蔵ブラウザ等）が使っていることがあるため、
# 既定を 9300 にして環境変数で差し替えられるようにしている。
CHROME_DEBUG_PORT = int(os.environ.get("LIFEISTECH_CHROME_PORT", "9300"))
CDP_ENDPOINT = f"http://127.0.0.1:{CHROME_DEBUG_PORT}"
# 起動する Chrome のプロファイル（ログイン状態はここに残る）
CHROME_PROFILE_DIR = os.environ.get("LIFEISTECH_CHROME_PROFILE", "chrome_profile")
# ヘッドレスで起動する（LIFEISTECH_HEADLESS=1）。画面は出ないが CDP 操作は同じ。
# 手動ログインができないので、セッション切れ対策に .env の ID / パスワードを推奨。
HEADLESS = os.environ.get("LIFEISTECH_HEADLESS", "0") not in ("0", "", "false", "False")
CHROME_STARTUP_TIMEOUT = 20.0
LESSON_HOME_URL = "https://member.lifeistech-lesson.jp/home"
# ログイン画面（b2clogin.com）だったときに、手動ログインを待つ上限（秒）
LOGIN_WAIT_SEC = float(os.environ.get("LIFEISTECH_LOGIN_WAIT", "300"))
# ログイン済み判定に使うセッション cookie（member_api.py と共通）
SESSION_COOKIE_NAME = "mozermovie"
# .env に書くとログイン画面で自動入力する（未設定なら手動ログインを待つ）
LIFEISTECH_ID = os.environ.get("LIFEISTECH_ID", "")
LIFEISTECH_PASSWORD = os.environ.get("LIFEISTECH_PASSWORD", "")

# --- AI ---
# "opencode_go" か "gemini"。opencode_go はキー不足・認証失敗時に gemini へ落ちる。
AI_PROVIDER = "opencode_go"

# OpenCode Go（OpenAI 互換エンドポイント）
OPENCODE_GO_BASE_URL = "https://opencode.ai/inference/go/openai/v1/chat/completions"
OPENCODE_GO_MODEL = "muse-spark-1.3-contributor"
OPENCODE_GO_TIMEOUT_SEC = 20.0
OPENCODE_GO_MAX_RETRIES = 2
# Muse Spark は推論トークンも max_tokens を食うので、小さすぎると本文が空になる。
OPENCODE_GO_MAX_TOKENS = 1024
# "minimal" / "low" / "medium" / "high"。None なら送らない。
OPENCODE_GO_REASONING_EFFORT = "minimal"
# Cloudflare が urllib 既定の User-Agent を弾くため、opencode CLI を名乗る。
OPENCODE_GO_USER_AGENT = "opencode/2.2.0 (darwin arm64)"
# OPENCODE_GO_API_KEY が未設定なら、opencode CLI の認証情報から読む。
OPENCODE_GO_AUTH_PATH = "~/.local/share/opencode/auth.json"
AI_FALLBACK_TO_GEMINI = True

GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_TIMEOUT_MS = 30_000
GEMINI_MAX_RETRIES = 2

# --- 共通の自動化設定 ---
ACTION_TIMEOUT_MS = 10_000
MIN_ACTION_DELAY = 0.15

# --- レッスン自動化 (lifeistech_auto.py) ---
# キャラクターの移動・要素のフェードなどの CSS アニメーション/トランジションを
# ほぼ 0 秒に潰す（プレイヤー側の演出待ちを短くする）。0 秒にすると
# transitionend / animationend が発火しなくなるため、1ms 残してある。
DISABLE_CSS_ANIMATION = True
# プレイヤーはステップの exitBehaviourList に waitMilliseconds（例: 8000）を
# 持ち、その間 setTimeout で待たされる。長い待ちだけを短縮する。
SHORTEN_LONG_TIMERS = True
LONG_TIMER_TRIGGER_MS = 1000
LONG_TIMER_CAP_MS = 200
# プレイヤー (player.lifeistech-lesson.jp/player/step) は React 製で、
# ボタンは <button> ではなく div + クラスで描画される。
LESSON_PAGE_MARKER = "/player/step"
SCENARIO_API_PATH = "/api/lesson_player/scenario"
# 進行ボタンはシナリオの ButtonElement（<button class="btn-next ...">）だけ。
# .progressbar_step-btn は下部プログレスバーのステップ移動ボタンで、進行ボタンではない。
SEL_LESSON_ACTION_BTN = ".btn-next"
# クイズの選択肢が「回答済みで操作不可」になったときのクラス。
QUIZ_DISABLED_CLASS = "disabled"
SEL_QUIZ_MODAL = ".quiz-modal-element"
SEL_QUIZ_TEXT = ".quiz-text"
SEL_QUIZ_BUTTON = ".quiz-button"
# 画像から選ぶクイズは専用のモーダルで描画される（.quiz-text / .quiz-button は共通）。
SEL_IMAGE_QUIZ_MODAL = ".image-select-quiz-element"
SEL_IMAGE_QUIZ_CHOICE = ".image-select-quiz-element .quiz-button"
# Enter 連打の間隔。0 で最速（待機なし）。
ENTER_SPAM_DELAY = 0.0
# 条件待ちのポーリング間隔（秒）。固定 sleep で待たず、状態が変わるまで
# この間隔で 1 往復の JS を投げ、「押せる／進んだ」瞬間に次の動作へ移る。
FAST_POLL_SEC = 0.01
# 画面遷移（タブが現れる・プレイヤーが描画される）待ちの間隔（秒）。
NAV_POLL_SEC = 0.05
# 会員 API のポーリング間隔（秒）。サーバー負荷を考えて DOM より長めにする。
API_POLL_SEC = 0.25
# 同じステップで進行ボタンを押し直すまでの最短間隔（秒）。
# 押した直後はまだ反応していないことがあるため、少し置いて再度押す。
ACTION_RECLICK_DELAY = 0.15
# 擬似 transitionend を送る最短間隔（秒）。0 にすると毎周回送る。
SKIP_ANIMATION_INTERVAL = 0.03
# 進行ボタンのクリック待ち（ms）。ステップが先に進んでボタンが消えることがあるため短くする。
# human_click は bounding_box と click で 2 回待つので、最悪この 2 倍で諦める。
ACTION_CLICK_TIMEOUT_MS = 800
# これ以上ステップが進まなければ「次へ」クリックに切り替える（秒）。
STUCK_CLICK_AFTER = 0.35
# これ以上進まなければ諦めて終了する（秒）。
STUCK_GIVEUP_AFTER = 30.0
# クイズが開いたまま同じ問題のまま何秒経過したら「解けない」と判断するか（秒）。
# Enter 連打は止めず、この時間を超えたときだけ復帰処理を挟む。
QUIZ_GIVEUP_AFTER = 15.0
# 解けないクイズをページ再読込で立て直す最大回数。超えたら停止する。
QUIZ_MAX_RELOADS = 2
# 「正解／不正解」表示とみなすテキストの最大長（問題文の誤検知を避ける）。
FEEDBACK_MAX_LENGTH = 60
# レッスン終了とみなす URL の目印（完了画面）。
FINISH_URL_MARKERS = ("/finish", "#finish", "lesson_finish")
# 回答クリック後、「正解／不正解」が出るまで待つ上限（秒）。
# 正解表示より先に Enter を送ると、次の問題のモーダルが disabled のまま固まる。
QUIZ_FEEDBACK_WAIT = 4.0
# クイズを閉じるために Enter を送り続ける上限（秒）。
QUIZ_CLOSE_TIMEOUT = 1.5
# クイズを閉じるとき、次の Enter を送るまでの最長間隔（秒）。
# 閉じたかどうかは FAST_POLL_SEC 間隔で見るので、これは「反応が無いときの再送間隔」。
# 正解表示とモーダルの 2 段階で閉じるため、間隔を空けるとその分だけ待たされる。
QUIZ_ENTER_INTERVAL = 0.01
# クイズが「disabled のみ・chosen なし」で固まったまま何秒続いたらページ再読込で復帰するか。
BROKEN_QUIZ_WAIT = 2.5
# アニメーション待ち（既定 900ms の CSS トランジション）を飛ばす。
# プレイヤーは各ステップで「AnimationFinish」を待って進行するため、Enter では縮められない。
# transitionend を擬似的に発火させて待ち時間をなくす（演出は省かれ、移動が瞬間的になる）。
SKIP_ANIMATION_WAIT = True

# --- 理解度チェック (work_automation.py) ---
SEL_WORK_QUESTION = "span.css-mzz7lz"
SEL_WORK_OPTION_LABEL = "label"
SEL_WORK_GRADE_BTN = 'button:has-text("この内容で採点する")'
GRADE_BTN_TEXT = "この内容で採点する"
# 同じ問題文と選択肢の組は AI に聞き直さずキャッシュを使う（2 回目以降が速い）。
WORK_ANSWER_CACHE = ".cache/work_answers.json"
SEL_WORK_NEXT_BTN = 'button:has-text("次へ進む")'
SEL_WORK_CONFIRM_BTN = 'button:has-text("解答内容を確認"), button:has-text("解答確認へ")'
WORK_QUESTION_INDEX = 1
QUESTION_RETRY_COUNT = 5
QUESTION_RETRY_INTERVAL = 2.0
# 選択肢として扱うテキストの最大長。長すぎるものは設問文の巻き込みとみなす。
MAX_OPTION_TEXT_LENGTH = 300
# 意図的に誤答する確率。採点スコアを汚すので既定は 0（人間味が欲しいときだけ上げる）。
WRONG_ANSWER_RATE = 0.0
# ワーク 1 回あたりの最大設問数と、同じ問題が続いたときに諦める回数。
MAX_WORK_STEPS = 200
WORK_STALL_LIMIT = 5

# --- 全自動 (auto_runner.py) ---
# 受講者向け API。cookie `mozermovie` を Bearer トークンとして GET する。
MEMBER_API_BASE = "https://api.lifeistech-lesson.jp/api"
MEMBER_API_TIMEOUT_SEC = 15.0
MEMBER_API_MAX_RETRIES = 2

AUTO_COURSE_NAME = "情報Ⅰ"
# "chapter": 最初の未完了レッスンを含む章だけ / "course": コース全体
AUTO_SCOPE = "chapter"
# "第5章" などで絞る。None なら自動。
AUTO_CHAPTER_LABEL = None
# 0 は上限なし。
AUTO_MAX_LESSONS = 0
# 途中まで進んだレッスンを「最初からやり直す」ことも許可する。
AUTO_RESTART_PARTIAL = True
# レッスン完了（API の status が cleared）になるまで待つ上限（秒）。
AUTO_CLEAR_WAIT_SEC = 30.0
# レッスン画面のボタンが描画されるまで待つ上限（秒）。
LESSON_PAGE_READY_TIMEOUT = 30.0
# レッスン完了後に理解度チェックワークも続けて解く（設問はランダムなので AI で回答）。
# CLI の --checkwork / --no-checkwork で上書きできる。
AUTO_RUN_CHECKWORK = True
# チェックワークで設問が切り替わるまで待つ上限（秒）。
WORK_STEP_TIMEOUT = 5.0

# レッスン画面（member ドメイン）のボタン。素の <button> で描画される。
SEL_LESSON_START_BTN = 'button:has-text("レッスンスタート")'
SEL_LESSON_RESUME_BTN = 'button:has-text("レッスンを続きから再開")'
SEL_LESSON_RESTART_BTN = 'button:has-text("最初からレッスンをやり直す")'
SEL_LESSON_CHECKWORK_BTN = 'button:has-text("理解度チェックワークを始める")'

# 端末の環境を選ぶダイアログ（EX①など、OS ごとにレッスンが分かれるもの）。
# 開始ボタンを押した後に出るので、押されたら該当する OS を選ぶ。
# ボタンはアイコン（svg）付きのため text-is では当たらない。役割と名前で探す。
OS_CHOICE_LABELS = {
    "mac": "Mac",
    "win": "Windows",
    "chromebook": "Chromebook",
    "ipad": "iPad",
}
# プレビュー型の課題（表計算など、iframe 内のアプリを操作するもの）。
SEL_PREVIEW_IFRAME = ".preview-window iframe"
PREVIEW_READY_TIMEOUT = 10.0
PREVIEW_MAX_ATTEMPTS = 3

# コード課題（Ace エディタ）の操作。
SEL_CODE_EDITOR = ".ace_editor"
SEL_CODE_CHECK_BTN = "button.editor-modal-button"
SEL_CODE_TAB_CSS = "li.label-text.css"
SEL_CODE_TAB_HTML = "li.label-text.html"
SEL_CODE_RESULT_CLOSE = "a.editor-modal-footer-close"
# Python のレッスンは「コードチェック」で正解を確認したあと、結果ウィンドウを閉じて
# 「実行」を押すまでステップが進まない。
SEL_CODE_RUN_BTN = ".update-button"
# シナリオの exitCondition が "Click:confirm-button" のときの OK ボタン。
# 画面右上に出る黄色い「OK」。id が無いのでクラスで探す。
SEL_CONFIRM_BTN = "button.btn-next-console-up-right"
# クリック課題を試す回数の上限（押しても進まない場合に無限に押さない）。
CLICK_MAX_ATTEMPTS = 3
# クリック課題の要素が描画されるまで待つ時間（秒）。
CLICK_WAIT_SEC = 2.5
# 2 回目以降のクリック課題は、既に一度探して無かったものなので短く待つ（秒）。
CLICK_RETRY_WAIT_SEC = 0.8
# confirm-button 以外のクリック課題は、見つからなくても Enter で進めるので短く打ち切る（秒）。
CLICK_SHORT_WAIT_SEC = 0.2
CODE_MAX_ATTEMPTS = 5
# コード課題のエディタはステップ遷移直後まだ操作できないことがあるため、
# 使えるようになるまで待つ（この待ちが無いと 0 秒で失敗して諦めてしまう）。
CODE_EDITOR_READY_TIMEOUT = 5.0
# 解答を入力してから自動検証が返るのを待つ時間（秒）。
# 実測では自動検証はほぼ返らない（毎回「コードチェック」を押すことになる）ので短くした。
CODE_AUTO_VALIDATE_WAIT = 0.15
# 「コードチェック」を押してから判定が返るまでの待ち時間（秒）。
CODE_CHECK_WAIT = 2.0
# 入力後にエディタの内容が解答どおりか確認する。
CODE_VERIFY_APPLIED = True

# 停止時のスクリーンショット置き場（実行ユーザーだけが読める 0700 で作る）。
SCREENSHOT_DIR = "screenshots"
# 残す枚数の上限（古いものから消す）。
SCREENSHOT_KEEP = 30
# 同じ Chrome に複数プロセスが接続しないためのロックファイル。
LOCK_FILE = ".lifeistech.lock"

# プレイヤーではなくワーク画面に遷移したかを見る URL の目印。
WORK_PAGE_MARKER = "variant_check_work"

# --- 検知回避 ---
# 全タブに効く init script なので、通常のブラウジングへの影響を避けて既定は無効。
APPLY_WEBDRIVER_PATCH = False
WEBDRIVER_PATCH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
