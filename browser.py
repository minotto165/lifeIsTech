"""Chrome の起動・接続・ログイン待ちをまとめたモジュール。

main.py からも各スクリプト（lifeistech_auto.py / auto_runner.py など）からも
同じ手順で使えるように、起動まわりの処理をここへ集約している。
"""

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


def open_home_if_blank(page):
    """空タブならレッスンのホームを開く。"""
    href = current_href(page)
    if href and href.startswith("about:"):
        try:
            page.goto(config.LESSON_HOME_URL, wait_until="domcontentloaded")
        except Exception as exc:
            # 開けなくても現在のページのままログイン判定・遷移を続けられる。
            ui.out(
                f"ホームページを開けませんでした（{exc}）。現在のページのまま続行します。",
                "yellow",
            )
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


def has_session(page):
    """ログイン済みかどうかを cookie で判定する（会員サイトのセッション cookie）。"""
    global _session_cookie_warned
    try:
        return config.SESSION_COOKIE_NAME in page.evaluate("() => document.cookie")
    except Exception as exc:
        # 取得できないと「未ログイン」と誤判定して待ち続けるので、一度だけ知らせる。
        if not _session_cookie_warned:
            _session_cookie_warned = True
            ui.out(
                f"セッション cookie を取得できませんでした（{exc}）。ログイン判定を続けます。",
                "yellow",
            )
        return False


# ログイン画面のセレクタは config.py に置いてある（DOM セレクタは config に集約する方針）。
LOGIN_ID_SELECTOR = config.LOGIN_ID_SELECTOR
LOGIN_PASSWORD_SELECTOR = config.LOGIN_PASSWORD_SELECTOR
LOGIN_SUBMIT_SELECTOR = config.LOGIN_SUBMIT_SELECTOR


def has_credentials():
    return bool(config.LIFEISTECH_ID and config.LIFEISTECH_PASSWORD)


def fill_login_form(page, submit=True):
    """ログイン画面に ID / パスワードを入れる（submit=False は入力だけ）。"""
    form = page.locator(LOGIN_ID_SELECTOR)
    if form.count() == 0:
        # フォームが畳まれている場合は「ID / パスワードでログイン」で開く
        opener = page.locator("#next")
        if opener.count() == 0:
            return False
        opener.first.click(timeout=3000)
        page.wait_for_timeout(300)
        form = page.locator(LOGIN_ID_SELECTOR)
        if form.count() == 0:
            return False
    page.fill(LOGIN_ID_SELECTOR, config.LIFEISTECH_ID)
    page.fill(LOGIN_PASSWORD_SELECTOR, config.LIFEISTECH_PASSWORD)
    if submit:
        page.locator(LOGIN_SUBMIT_SELECTOR).first.click(timeout=3000)
    return True


FILLED_JS = """() => {
  const id = document.querySelector('#signInName');
  const pw = document.querySelector('#password');
  return {id: id ? (id.value || '') : '', pw: pw ? (pw.value || '').length : 0};
}"""


def try_auto_login(page):
    """ .env の ID / パスワードで自動ログインする。成功しても失敗しても 1 回だけ試す。"""
    if not has_credentials():
        # Chrome のパスワード保存が自動入力済みなら、それをそのまま送信する。
        try:
            filled = page.evaluate(FILLED_JS)
        except Exception as exc:
            # 保存済みパスワードの自動入力は補助なので、失敗したら手動ログインに任せる。
            ui.out(
                f"自動入力の状態を確認できませんでした（{exc}）。手動ログインを待ちます。",
                "yellow",
            )
            return False
        if filled.get("id") and filled.get("pw", 0) >= 4:
            try:
                page.locator(LOGIN_SUBMIT_SELECTOR).first.click(timeout=3000)
                ui.out("Chrome に保存された ID / パスワードで自動ログインしています…", "cyan")
                return True
            except Exception as exc:
                ui.out(f"自動ログインに失敗しました（{exc}）。手動でログインしてください。", "yellow")
        return False
    try:
        if not fill_login_form(page):
            return False
        ui.out("ID とパスワードで自動ログインしています…", "cyan")
        return True
    except Exception as exc:
        ui.out(f"自動ログインに失敗しました（{exc}）。手動でログインしてください。", "yellow")
        return False


def wait_for_login(page, timeout=None):
    """ログイン画面なら、ログインが終わるまで待つ（ブラウザは開いたまま）。"""
    timeout = config.LOGIN_WAIT_SEC if timeout is None else timeout
    # ホームを開いた直後はリダイレクトの途中で、まだログイン画面に見えないことがある。
    settle = time.time() + 2.0
    while time.time() < settle:
        if needs_login(page):
            break
        time.sleep(0.1)
    if not needs_login(page) and has_session(page):
        return page
    deadline = time.time() + timeout
    attempted = False
    announced = False
    while time.time() < deadline:
        # ログイン画面を抜けただけでは不十分（リダイレクト途中）なので cookie も見る。
        if not needs_login(page) and has_session(page):
            ui.out("ログインを確認しました。続行します。", "green")
            return page
        if not attempted and needs_login(page):
            attempted = True
            if try_auto_login(page):
                continue
        if not announced:
            announced = True
            ui.out(
                f"ログインが必要です。開いている Chrome でログインしてください（最大 {int(timeout)} 秒待ちます）…",
                "yellow",
            )
        time.sleep(0.5)
    raise RuntimeError(
        "ログインを確認できませんでした。Chrome でログインしてから、もう一度実行してください。"
    )