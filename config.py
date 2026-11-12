"""共通設定。挙動やDOMセレクタを変えたいときはこのファイルだけを編集する。"""

# --- Chrome / CDP ---
CHROME_DEBUG_PORT = 9222
CDP_ENDPOINT = f"http://127.0.0.1:{CHROME_DEBUG_PORT}"
CHROME_PROFILE_DIR = "chrome_profile"
CHROME_STARTUP_TIMEOUT = 20.0
LESSON_HOME_URL = "https://member.lifeistech-lesson.jp/home"

# --- AI ---
GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_TIMEOUT_MS = 30_000
GEMINI_MAX_RETRIES = 2

# --- 共通の自動化設定 ---
MAX_LOOP_ITERATIONS = 50_000
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

# --- 検知回避 ---
WEBDRIVER_PATCH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
