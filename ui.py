"""CLI の見た目（色分け・一覧表）をまとめた小さなヘルパー。

rich が入っていない環境でもそのまま動くように、無いときは素の print に
フォールバックする。レッスンの自動化ログは量が多いので、通過ログは控えめに、
成功・失敗・警告は色で区別できるようにしている。
"""

import os
import re
import sys

# MCP サーバーは stdout を JSON-RPC 専用に使うため、ログは stderr へ逃がす。
# mcp_server.py が config より先にこの環境変数を立てる。
_LOG_TO_STDERR = os.environ.get("LIFEISTECH_LOG_STREAM", "").lower() == "stderr"

try:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    _console = Console(highlight=False, stderr=_LOG_TO_STDERR)
except Exception:  # pragma: no cover - rich が無い環境向け
    _console = None
    Table = None
    Text = None


# 「ステップ 123 を通過しました。」のような定型的なログは目立たせない。
_STEP_RE = re.compile(r"^ステップ \d+ を通過しました。$")
# 「未完了レッスン」の「完了」を成功扱いしないための除外。
_UNFINISHED_RE = re.compile(r"未完了")
# URL だけの行は控えめに。
_URL_RE = re.compile(r"^https?://")

_ERROR_WORDS = (
    "エラー",
    "失敗",
    "不正解",
    "停止",
    "できません",
    "見つかりません",
    "タイムアウト",
)
_WARN_WORDS = ("警告", "スキップ", "未対応", "見つかりませんでした", "できませんでした")
_OK_WORDS = ("クリア", "正解", "完了", "到達", "通過しました", "押しました", "選びました")

# レッスンの状態ごとの色。
STATUS_STYLES = {
    "完了": "green",
    "途中": "yellow",
    "未着手": "cyan",
    "ロック": "dim red",
}


def style_for(message):
    """ログ 1 行の色を決める。"""
    text = str(message)
    if _STEP_RE.match(text):
        return "dim"
    if text.startswith("[profile]") or text.startswith("[calib]"):
        return "dim cyan"
    if _UNFINISHED_RE.search(text):
        return ""
    if _URL_RE.match(text.strip()):
        return "dim"
    for word in _ERROR_WORDS:
        if word in text:
            return "bold red"
    for word in _WARN_WORDS:
        if word in text:
            return "yellow"
    for word in _OK_WORDS:
        if word in text:
            return "green"
    return ""


def out(message="", style=None):
    """1 行出力する（markup は解釈しない）。"""
    text = str(message)
    if style is None:
        style = style_for(text)
    if _console is None:
        print(text, file=sys.stderr if _LOG_TO_STDERR else sys.stdout, flush=True)
        return
    _console.print(Text(text, style=style))


def log(message):
    """自動化のログ。内容に応じて色を付ける。"""
    out(message)


def blank():
    out("")


def header(text):
    """見出し（太字＋下線代わりの罫線）。"""
    if _console is None:
        print(f"--- {text} ---", file=sys.stderr if _LOG_TO_STDERR else sys.stdout, flush=True)
        return
    _console.rule(Text(str(text), style="bold"), style="cyan")


def chapter(text):
    """章の見出し。"""
    out(text, "bold magenta")


def menu(title, items):
    """キーと説明の 2 列メニュー。"""
    if _console is None:
        stream = sys.stderr if _LOG_TO_STDERR else sys.stdout
        print(title, file=stream, flush=True)
        for key, label in items:
            print(f"{key}: {label}", file=stream, flush=True)
        return
    table = Table(show_header=False, box=None, padding=(0, 2), expand=False)
    table.add_column(justify="right", style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    for key, label in items:
        table.add_row(str(key), str(label))
    _console.print(Text(title, style="bold"))
    _console.print(table)


def status_text(status):
    """状態（完了・途中・未着手・ロック）を色付きで返す。"""
    style = STATUS_STYLES.get(status, "")
    if _console is None:
        return status
    return Text(str(status), style=style)


def lesson_table(rows):
    """レッスン一覧を「番号・ラベル・タイトル・状態」で並べて出す。

    rows は (index, label, title, status) の並び。
    """
    if _console is None:
        for index, label, title, status in rows:
            stream = sys.stderr if _LOG_TO_STDERR else sys.stdout
            print(f"{index:3d}. {label}{title} [{status}]", file=stream, flush=True)
        return
    table = Table(show_header=False, box=None, padding=(0, 1), expand=False)
    table.add_column(justify="right", style="dim", no_wrap=True)
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")
    table.add_column(no_wrap=True)
    for index, label, title, status in rows:
        table.add_row(str(index), label, title, status_text(status))
    _console.print(table)


def ask(prompt):
    """入力待ち。表示だけ色を付ける。"""
    if _console is None:
        return input(prompt)
    return _console.input(f"[bold cyan]{prompt}[/]")
