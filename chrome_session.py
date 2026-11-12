"""起動済み Chrome への Playwright 接続を共通化するモジュール。"""

from playwright.sync_api import sync_playwright

import config


class ChromeSession:
    """CDP 経由で既存 Chrome に接続し、操作対象のページを返す。"""

    def __init__(self, url_marker=None):
        self._playwright = None
        self.browser = None
        self.page = None
        self.url_marker = url_marker

    def connect(self):
        self._playwright = sync_playwright().start()
        try:
            self.browser = self._playwright.chromium.connect_over_cdp(config.CDP_ENDPOINT)
            if not self.browser.contexts:
                raise RuntimeError("Chrome にブラウザコンテキストが見つかりません")
            context = self.browser.contexts[0]
            # 既に読み込み済みのページには効かないが、以降の遷移から有効になる。
            context.add_init_script(config.WEBDRIVER_PATCH)
            self.page = self._select_page(context)
            return self.page
        except Exception:
            self.close()
            raise

    def _select_page(self, context):
        pages = context.pages
        if not pages:
            return context.new_page()
        if self.url_marker:
            for page in pages:
                if self.url_marker in page.url:
                    return page
        return pages[0]

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
