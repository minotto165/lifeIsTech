"""共通設定。挙動やDOMセレクタを変えたいときはこのファイルだけを編集する。"""

# --- Chrome / CDP ---
CHROME_DEBUG_PORT = 9222
CDP_ENDPOINT = f"http://127.0.0.1:{CHROME_DEBUG_PORT}"
CHROME_PROFILE_DIR = "chrome_profile"
CHROME_STARTUP_TIMEOUT = 20.0
LESSON_HOME_URL = "https://member.lifeistech-lesson.jp/home"

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
POLL_INTERVAL = 1.5
ACTION_TIMEOUT_MS = 10_000
MIN_ACTION_DELAY = 0.15

# --- レッスン自動化 (lifeistech_auto.py) ---
# プレイヤー (player.lifeistech-lesson.jp/player/step) は React 製で、
# ボタンは <button> ではなく div + クラスで描画される。
LESSON_PAGE_MARKER = "/player/step"
SCENARIO_API_PATH = "/api/lesson_player/scenario"
SEL_LESSON_NEXT_BTN = ".progressbar_step-btn"
SEL_LESSON_ACTION_BTN = ".btn-next"
LESSON_NEXT_TEXT = "次へ"
LESSON_DISABLED_CLASS = "progressbar_step-btn_disabled"
SEL_QUIZ_MODAL = ".quiz-modal-element"
SEL_QUIZ_TEXT = ".quiz-text"
SEL_QUIZ_BUTTON = ".quiz-button"
# 画像から選ぶクイズは専用のモーダルで描画される（.quiz-text / .quiz-button は共通）。
SEL_IMAGE_QUIZ_MODAL = ".image-select-quiz-element"
SEL_IMAGE_QUIZ_CHOICE = ".image-select-quiz-element .quiz-button"
# Enter 連打の間隔。0 で最速（待機なし）。
ENTER_SPAM_DELAY = 0.0
# 進行ボタンのクリック待ち（ms）。ステップが先に進んでボタンが消えることがあるため短くする。
# human_click は bounding_box と click で 2 回待つので、最悪この 2 倍で諦める。
ACTION_CLICK_TIMEOUT_MS = 800
# これ以上ステップが進まなければ「次へ」クリックに切り替える（秒）。
STUCK_CLICK_AFTER = 1.0
# これ以上進まなければ諦めて終了する（秒）。
STUCK_GIVEUP_AFTER = 30.0
# 回答クリック後、「正解／不正解」が出るまで待つ上限（秒）。
# 正解表示より先に Enter を送ると、次の問題のモーダルが disabled のまま固まる。
QUIZ_FEEDBACK_WAIT = 4.0
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
SEL_WORK_NEXT_BTN = 'button:has-text("次へ進む")'
SEL_WORK_CONFIRM_BTN = 'button:has-text("解答内容を確認"), button:has-text("解答確認へ")'
WORK_QUESTION_INDEX = 1
QUESTION_RETRY_COUNT = 5
QUESTION_RETRY_INTERVAL = 2.0
MAX_OPTION_TEXT_LENGTH = 150
WRONG_ANSWER_RATE = 0.15

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
# レッスン完了後に理解度チェックワークも続けて解く。
AUTO_RUN_CHECKWORK = False

# レッスン画面（member ドメイン）のボタン。素の <button> で描画される。
SEL_LESSON_START_BTN = 'button:has-text("レッスンスタート")'
SEL_LESSON_RESUME_BTN = 'button:has-text("レッスンを続きから再開")'
SEL_LESSON_RESTART_BTN = 'button:has-text("最初からレッスンをやり直す")'
SEL_LESSON_CHECKWORK_BTN = 'button:has-text("理解度チェックワークを始める")'
# コード課題（Ace エディタ）の操作。
SEL_CODE_EDITOR = ".ace_editor"
SEL_CODE_CHECK_BTN = "button.editor-modal-button"
SEL_CODE_TAB_CSS = "li.label-text.css"
SEL_CODE_TAB_HTML = "li.label-text.html"
SEL_CODE_RESULT_CLOSE = "a.editor-modal-footer-close"
CODE_MAX_ATTEMPTS = 5
CODE_CHECK_INTERVAL = 0.6
# 解答を入力してからサーバー検証が返るまでの待ち時間（秒）。
CODE_APPLY_WAIT = 6.0

# プレイヤーではなくワーク画面に遷移したかを見る URL の目印。
WORK_PAGE_MARKER = "variant_check_work"

# --- 検知回避 ---
WEBDRIVER_PATCH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
