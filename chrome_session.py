"""起動済み Chrome への Playwright 接続を共通化するモジュール。"""

from playwright.sync_api import sync_playwright

import browser
import config


def safe_page_url(page):
    """page.url が取れない（閉じた・遷移中）場合に備えた安全な取得。"""
    try:
        return page.url or "(about:blank)"
    except Exception:
        return "(不明)"


class ChromeSession:
    """CDP 経由で既存 Chrome に接続し、操作対象のページを返す。"""

    def __init__(self, url_marker=None):
        self._playwright = None
        self.browser = None
        self.page = None
        self.url_marker = url_marker

    def connect(self):
        # Chrome が起動していなければここで起動する（各スクリプトを単体で実行しても動く）。
        browser.ensure_chrome_running()
        self._playwright = sync_playwright().start()
        try:
            self.browser = self._playwright.chromium.connect_over_cdp(config.CDP_ENDPOINT)
            if not self.browser.contexts:
                raise RuntimeError("Chrome にブラウザコンテキストが見つかりません")
            context = self.browser.contexts[0]
            # 全タブに効く init script なので、明示的に有効化したときだけ入れる。
            # 既に読み込み済みのページには効かないが、以降の遷移から有効になる。
            if config.APPLY_WEBDRIVER_PATCH:
                context.add_init_script(config.WEBDRIVER_PATCH)
            self.page = self._select_page(context)
            # 空タブで起動した場合はホームを開き、セッションが切れていれば
            # ログイン画面になるので手動ログインを待つ。
            browser.open_home_if_blank(self.page)
            self.page = browser.wait_for_login(self.page)
            return self.page
        except Exception:
            self.close()
            raise

    def _select_page(self, context):
        pages = context.pages
        if not pages:
            return context.new_page()
        if not self.url_marker:
            return pages[0]
        for page in pages:
            try:
                if self.url_marker in page.url:
                    return page
            except Exception:
                continue
        # 目印の付いたタブが無いまま pages[0] を使うと、無関係なタブに
        # キー入力やクリックを送ってしまうので、黙って続行しない。
        urls = ", ".join(safe_page_url(page) for page in pages) or "(なし)"
        raise RuntimeError(
            f"{self.url_marker} を含むタブが見つかりません。"
            f"Chrome で対象のページを開いてから実行してください。開いているタブ: {urls}"
        )

    def close(self):
        # connect_over_cdp では browser.close() が Chrome 自体を終了させるため呼ばない。
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False
