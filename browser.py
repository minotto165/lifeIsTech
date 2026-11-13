"""Chrome の起動・接続・ログイン待ちをまとめたモジュール。

main.py からも各スクリプト（lifeistech_auto.py / auto_runner.py など）からも
同じ手順で使えるように、起動まわりの処理をここへ集約している。
"""

import base64
import contextlib
import json
import os
import platform
import shutil
import socket
import subprocess
import time
import urllib.request

import config
import ui

LOGIN_HOST = "b2clogin.com"


def get_chrome_path():
    """OS ごとに Chrome の実行ファイルパスを解決する。"""
    system = platform.system()
    if system == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    elif system == "Darwin":
        candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        candidates = [shutil.which("google-chrome"), shutil.which("chromium")]

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise FileNotFoundError(
        "Chrome が見つかりませんでした。Google Chrome をインストールしてください。"
    )


def is_debug_port_open(timeout=0.5):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", config.CHROME_DEBUG_PORT)) == 0


def wait_for_page_ready(timeout=None):
    """デバッグポートが開いても、まだページが無いことがあるので描画側も待つ。"""
    timeout = config.CHROME_STARTUP_TIMEOUT if timeout is None else timeout
    url = f"http://127.0.0.1:{config.CHROME_DEBUG_PORT}/json/list"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as res:
                pages = json.loads(res.read() or b"[]")
        except Exception:
            # 起動直後はまだ接続できないのが正常なので、待機を続ける。
            pages = []
        # 初回起動のプロファイルでは chrome://intro/ が開くことがあるので、
        # URL は問わずタブが 1 枚でもあれば「起動した」とみなす。
        for page in pages:
            if page.get("type") == "page":
                return True
        time.sleep(config.NAV_POLL_SEC)
    return False


def profile_owner_pid():
    """chrome_profile/SingletonLock から、このプロファイルを使っている Chrome の PID を読む。"""
    lock = os.path.join(os.path.abspath(config.CHROME_PROFILE_DIR), "SingletonLock")
    try:
        target = os.readlink(lock)
    except FileNotFoundError:
        # ロックファイルが無い＝このプロファイルを使っている Chrome はいない（正常）。
        return None
    except OSError as exc:
        # ここで黙って None を返すと、別プロファイルの Chrome に繋いでしまう。
        ui.out(f"プロファイルのロックを読めませんでした（{exc}）。", "yellow")
        return None
    try:
        return int(str(target).rsplit("-", 1)[1])
    except (IndexError, ValueError):
        ui.out("プロファイルのロックから PID を読み取れませんでした。", "yellow")
        return None


def is_process_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def ensure_chrome_running():
    """デバッグポートが閉じていれば Chrome を起動する。起動した場合のみプロセスを返す。"""
    if is_debug_port_open():
        owner = profile_owner_pid()
        if owner is None or is_process_alive(owner):
            ui.out(f"既存の Chrome (ポート {config.CHROME_DEBUG_PORT}) に接続します。", "dim")
            return None
        # ポートは開いているが、それは別プロファイルの Chrome（他ツールの内蔵ブラウザ等）。
        # そのまま繋ぐとログインしていない別人のブラウザを操作してしまう。
        raise RuntimeError(
            f"ポート {config.CHROME_DEBUG_PORT} は別の Chrome が使用中です。"
            "その Chrome を終了するか、環境変数 LIFEISTECH_CHROME_PORT で別のポートを指定してください。"
        )

    chrome_path = get_chrome_path()
    user_data_dir = os.path.abspath(config.CHROME_PROFILE_DIR)
    os.makedirs(user_data_dir, exist_ok=True)

    cmd = [
        chrome_path,
        f"--user-data-dir={user_data_dir}",
        f"--remote-debugging-port={config.CHROME_DEBUG_PORT}",
    ]
    if config.HEADLESS:
        # 画面を出さない。ヘッドレスでも CDP 経由の操作・スクショは同じように動く。
        cmd += ["--headless=new", "--window-size=1600,1000"]
    cmd += [
        # 新規プロファイルで初回案内（chrome://intro/）が開いてタブが出ないのを防ぐ
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-search-engine-choice-screen",
        # URL を引数で渡すと初回起動時にタブが作られないことがあるので、
        # まず空タブで起動し、ホームページは Playwright 側で開く。
        "about:blank",
    ]
    ui.out("ブラウザを起動しています…", "cyan")
    with open(os.devnull, "w") as devnull:
        proc = subprocess.Popen(cmd, stdout=devnull, stderr=devnull)

    deadline = time.time() + config.CHROME_STARTUP_TIMEOUT
    while time.time() < deadline:
        if is_debug_port_open():
            # ポートが開いた直後はまだタブが無いことがある。
            if wait_for_page_ready():
                return proc
            break
        if proc.poll() is not None:
            raise RuntimeError("Chrome の起動に失敗しました。")
        time.sleep(config.NAV_POLL_SEC)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    raise RuntimeError(
        f"Chrome のデバッグポート ({config.CHROME_DEBUG_PORT}) が開きませんでした。"
    )


def current_href(page):
    """page.url は pushState やリダイレクトの途中で古いことがあるので、DOM から読む。"""
    # 遷移中は evaluate が失敗するのが普通なので、その場合は page.url で妥協する。
    with contextlib.suppress(Exception):
        href = page.evaluate("() => location.href")
        if href:
            return href
    try:
        return page.url or ""
    except Exception:
        return ""


def is_blank_href(href):
    """まだ何も開いていないタブかどうか。"""
    return not href or href.startswith(("about:", "chrome:"))


def open_home(page):
    """ホームを開く。開いたかどうかは URL で確認し、空タブのままなら開き直す。"""
    for attempt in range(1, config.HOME_OPEN_ATTEMPTS + 1):
        try:
            page.goto(
                config.LESSON_HOME_URL,
                wait_until="domcontentloaded",
                timeout=config.HOME_OPEN_TIMEOUT_MS,
            )
        except Exception as exc:
            # 開けなくても現在のページのままログイン判定・遷移を続けられる。
            ui.out(
                f"ホームページを開けませんでした（{exc}）。現在のページのまま続行します。",
                "yellow",
            )
        # goto が返っても遷移が始まった直後は URL が空タブのままのことがある。
        deadline = time.time() + config.HOME_OPEN_VERIFY_SEC
        while time.time() < deadline:
            if not is_blank_href(current_href(page)):
                return page
            time.sleep(config.NAV_POLL_SEC)
        if attempt < config.HOME_OPEN_ATTEMPTS:
            ui.out(f"空のタブのままなので、ホームを開き直します（{attempt} 回目）。", "yellow")
    ui.out(
        f"ホームを開けませんでした（現在のページ: {current_href(page) or 'なし'}）。",
        "yellow",
    )
    return page


def open_home_if_blank(page):
    """空タブならレッスンのホームを開く。"""
    if is_blank_href(current_href(page)):
        return open_home(page)
    return page


def needs_login(page):
    """ログイン画面（b2clogin.com）に居るかどうか。"""
    href = current_href(page)
    if LOGIN_HOST in href:
        return True
    # リダイレクト途中は URL がまだ会員サイトのままなので、本文でも判定する。
    # これは補助判定なので、失敗しても URL 判定の結果（False）で構わない。
    with contextlib.suppress(Exception):
        if page.locator("input[type=password]").count() > 0:
            return True
    return False


_session_cookie_warned = False


def session_cookie(page):
    """会員サイトのセッション cookie を返す（無ければ None）。

    document.cookie ではなく Playwright の cookie 一覧を見る。こちらの方が
    遷移中でも読めるうえ、cookie 自身の有効期限も分かる。
    """
    global _session_cookie_warned
    try:
        # 現在のページが about:blank でも判定できるよう、会員サイトの URL で引く。
        cookies = page.context.cookies(config.LESSON_HOME_URL)
    except Exception as exc:
        # 取得できないと「未ログイン」と誤判定して待ち続けるので、一度だけ知らせる。
        if not _session_cookie_warned:
            _session_cookie_warned = True
            ui.out(
                f"セッション cookie を取得できませんでした（{exc}）。ログイン判定を続けます。",
                "yellow",
            )
        return None
    for cookie in cookies:
        if cookie.get("name") == config.SESSION_COOKIE_NAME:
            return cookie
    return None


def jwt_expires_at(value):
    """cookie の値から JWT の exp（UNIX 秒）を取り出す。取れなければ None。"""
    # 値は "<ログイン ID>_<JWT>" の形。JWT は "eyJ..." で始まる a.b.c の 3 つ組。
    start = value.find("eyJ")
    if start < 0:
        return None
    parts = value[start:].split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    exp = data.get("exp")
    return exp if isinstance(exp, (int, float)) and not isinstance(exp, bool) else None


_expired_warned = False


def has_session(page):
    """ログイン済みかどうかをセッション cookie で判定する（会員サイトのセッション cookie）。

    cookie は残っていても期限切れなら未ログイン扱い。残骸で「ログイン済み」と
    誤判定すると、自動ログインを飛ばしてあとで 401 になるため。
    """
    global _expired_warned
    cookie = session_cookie(page)
    if cookie is None:
        return False
    # cookie 自身の期限（-1 はセッション cookie）。あればこちらを優先する。
    expires = cookie.get("expires", -1)
    if isinstance(expires, (int, float)) and expires > 0:
        exp = expires
    else:
        exp = jwt_expires_at(cookie.get("value", ""))
    if exp is None:
        # 期限が読めない形式の cookie は、あるだけで有効とは判断できない。
        return False
    remaining = exp - time.time()
    if remaining <= config.SESSION_EXPIRY_SKEW_SEC:
        if not _expired_warned:
            _expired_warned = True
            if remaining > 0:
                ui.out(
                    f"セッションの有効期限が近いので（残り {int(remaining / 60)} 分）、"
                    "ログインし直します。",
                    "dim",
                )
            else:
                ui.out("セッションの有効期限が切れているので、ログインし直します。", "dim")
        return False
    return True


# ログイン画面のセレクタは config.py に置いてある（DOM セレクタは config に集約する方針）。
LOGIN_ID_SELECTOR = config.LOGIN_ID_SELECTOR
LOGIN_PASSWORD_SELECTOR = config.LOGIN_PASSWORD_SELECTOR
LOGIN_SUBMIT_SELECTOR = config.LOGIN_SUBMIT_SELECTOR


def has_credentials():
    return bool(config.LIFEISTECH_ID and config.LIFEISTECH_PASSWORD)


def wait_for_form(page, selector, timeout):
    """セレクタの要素が出るまで待つ（遷移中で evaluate が失敗するのは想定内）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            pass
        time.sleep(config.FAST_POLL_SEC)
    return False


def fill_login_form(page, submit=True):
    """ログイン画面に ID / パスワードを入れる（submit=False は入力だけ）。

    B2C は URL がログイン画面に変わった直後は本文がまだ描画されていないので、
    入力欄が出るまで待ってから入力する。
    """
    if not wait_for_form(page, LOGIN_ID_SELECTOR, config.LOGIN_FORM_WAIT_SEC):
        # フォームが畳まれている場合は「ID / パスワードでログイン」で開く
        if not wait_for_form(page, "#next", config.LOGIN_FORM_WAIT_SEC):
            return False
        try:
            page.locator("#next").first.click(timeout=3000)
        except Exception:
            return False
        if not wait_for_form(page, LOGIN_ID_SELECTOR, config.LOGIN_FORM_WAIT_SEC):
            return False
    try:
        page.fill(LOGIN_ID_SELECTOR, config.LIFEISTECH_ID, timeout=5000)
        page.fill(LOGIN_PASSWORD_SELECTOR, config.LIFEISTECH_PASSWORD, timeout=5000)
        filled = page.evaluate(FILLED_JS)
        if not filled.get("id") or filled.get("pw", 0) < 1:
            return False
    except Exception:
        # 遷移の途中で入力欄が消えた場合など。呼び出し側がやり直す。
        return False
    if not submit:
        return True
    # クリックしても反応しないことがある（B2C のハンドラがまだ付いていない等）ので、
    # 画面が動き出すまで見て、動かなければ押し直す。
    for _ in range(config.LOGIN_SUBMIT_TRIES):
        if submit_login_form(page) and wait_login_started(page):
            return True
    return False


# 送信後にエラーが出たか（＝押せてはいる）
LOGIN_ERROR_JS = """() => {
  const el = document.querySelector('#claimVerificationServerError, .error, .errorMessage');
  return !!(el && (el.innerText || '').trim());
}"""


def wait_login_started(page, timeout=None):
    """送信ボタンを押したあと、画面が動き出す（＝送信された）かどうか。"""
    timeout = config.LOGIN_SUBMIT_START_SEC if timeout is None else timeout
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if not needs_login(page):
                return True
            if page.evaluate(LOGIN_ERROR_JS):
                return True
        except Exception:
            # 遷移中は evaluate が落ちる。それ自体が「動いた」証拠。
            return True
        time.sleep(config.FAST_POLL_SEC)
    return False


# B2C のフォームはアニメーション中だとクリックが「要素が安定するまで待つ」で
# タイムアウトすることがあるので、その場合は JS で直接押す。
SUBMIT_JS = """() => {
  const form = document.querySelector('#localAccountForm');
  const el = (form && form.querySelector('button[type=submit]')) || document.querySelector('#next');
  if (!el) return false;
  el.click();
  return true;
}"""


def submit_login_form(page):
    """ログインフォームを送信する。押せたかどうかを返す。"""
    try:
        page.locator(LOGIN_SUBMIT_SELECTOR).first.click(
            timeout=config.LOGIN_SUBMIT_TIMEOUT_MS
        )
        return True
    except Exception:
        pass
    try:
        return bool(page.evaluate(SUBMIT_JS))
    except Exception:
        return False


FILLED_JS = """() => {
  const id = document.querySelector('#signInName');
  const pw = document.querySelector('#password');
  return {id: id ? (id.value || '') : '', pw: pw ? (pw.value || '').length : 0};
}"""


def try_auto_login(page, announce_failure=True):
    """ .env の ID / パスワードで自動ログインする。

    失敗しても呼び出し側が数回やり直すので、失敗の知らせは最後の 1 回だけ出す。
    """
    if not has_credentials():
        # Chrome のパスワード保存が自動入力済みなら、それをそのまま送信する。
        try:
            filled = page.evaluate(FILLED_JS)
        except Exception as exc:
            # 保存済みパスワードの自動入力は補助なので、失敗したら手動ログインに任せる。
            if announce_failure:
                ui.out(
                    f"自動入力の状態を確認できませんでした（{exc}）。手動ログインを待ちます。",
                    "yellow",
                )
            return False
        if filled.get("id") and filled.get("pw", 0) >= 4:
            if submit_login_form(page):
                ui.out("Chrome に保存された ID / パスワードで自動ログインしています…", "cyan")
                return True
            if announce_failure:
                ui.out("保存されたパスワードの送信ボタンを押せませんでした。手動でログインしてください。", "yellow")
        return False
    try:
        if not fill_login_form(page):
            if announce_failure:
                ui.out(
                    f"ログイン画面の入力欄が見つかりませんでした（現在のページ: {current_href(page) or 'なし'}）。"
                    "手動でログインしてください。",
                    "yellow",
                )
            return False
        ui.out("ID とパスワードで自動ログインしています…", "cyan")
        return True
    except Exception as exc:
        if announce_failure:
            ui.out(f"自動ログインに失敗しました（{exc}）。手動でログインしてください。", "yellow")
        return False


def wait_for_login(page, timeout=None):
    """ログイン画面なら、ログインが終わるまで待つ（ブラウザは開いたまま）。"""
    timeout = config.LOGIN_WAIT_SEC if timeout is None else timeout
    # 既にログイン済みなら、リダイレクトの様子を見る必要はない。
    if not needs_login(page) and has_session(page):
        return page
    # ホームを開いた直後はリダイレクトの途中で、まだログイン画面に見えないことがある。
    settle = time.time() + 2.0
    while time.time() < settle:
        if needs_login(page):
            break
        time.sleep(0.1)
    if not needs_login(page) and has_session(page):
        return page
    deadline = time.time() + timeout
    attempts = 0
    next_try = 0.0
    announced = False
    reopened = False
    while time.time() < deadline:
        # ログイン画面を抜けただけでは不十分（リダイレクト途中）なので cookie も見る。
        if not needs_login(page) and has_session(page):
            ui.out("ログインを確認しました。続行します。", "green")
            return page
        if not reopened and not needs_login(page) and is_blank_href(current_href(page)):
            # タブの生成と競合してホームを開けていないと、ログイン画面にも
            # ならないまま待ち続けてしまう。一度だけホームを開き直す。
            reopened = True
            open_home(page)
            continue
        if needs_login(page) and attempts < config.LOGIN_AUTO_ATTEMPTS and time.time() >= next_try:
            # 入力欄がまだ描画されていないだけのことがあるので、数回やり直す。
            attempts += 1
            if try_auto_login(page, announce_failure=attempts >= config.LOGIN_AUTO_ATTEMPTS):
                continue
            next_try = time.time() + config.LOGIN_RETRY_WAIT_SEC
        # 自動ログインを出し切っても、まだリダイレクト中なら少し待つ（ここで
        # 「手動でログインしてください」を出すと、直後に成功して紛らわしい）。
        if not announced and attempts >= config.LOGIN_AUTO_ATTEMPTS and time.time() >= next_try:
            announced = True
            ui.out(
                f"ログインが必要です。開いている Chrome でログインしてください（最大 {int(timeout)} 秒待ちます）…",
                "yellow",
            )
            ui.out(f"現在のページ: {current_href(page) or 'なし'}", "dim")
        time.sleep(0.5)
    raise RuntimeError(
        "ログインを確認できませんでした。Chrome でログインしてから、もう一度実行してください。"
    )