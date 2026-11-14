"""Life is Tech! レッスン自動化ツールのエントリーポイント。"""

import os
import subprocess
import sys
import traceback

import browser
import config
import ui

# 値は (表示名, スクリプト名[, 起動時に渡す引数]) の 2〜3 要素。引数は省略できる。
MENU = {
    "1": ("レッスン自動化", "lifeistech_auto.py"),
    "2": ("理解度チェックワーク", "work_automation.py"),
    "3": (
        "全自動（未完了レッスン＋理解度チェックワークを連続実行）",
        "auto_runner.py",
    ),
    "4": (
        "レッスンを選んで実行（一覧から番号で選択・チェックワーク込み）",
        "auto_runner.py",
        ("--select",),
    ),
    "5": (
        "未実施の理解度チェックワークをまとめて実行",
        "auto_runner.py",
        ("--checkworks",),
    ),
}


def run_script(script_name, extra_args=()):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, script_name)
    if not os.path.isfile(path):
        ui.out(f"スクリプトが見つかりません: {path}", "bold red")
        return 1
    cmd = [sys.executable, str(path), *extra_args]
    try:
        result = subprocess.run(cmd, cwd=base_dir, check=False)
    except KeyboardInterrupt:
        ui.out("\n自動化を停止しました。", "yellow")
        return 130
    if result.returncode != 0:
        ui.out(f"スクリプトが異常終了しました（終了コード {result.returncode}）。", "bold red")
    return result.returncode


def choose_mode():
    ui.blank()
    ui.menu(
        "実行モードを選択してください",
        [(key, entry[0]) for key, entry in MENU.items()] + [("0", "終了")],
    )
    keys = "".join(sorted([*MENU.keys(), "0"]))
    try:
        choice = ui.ask(f"選択してください ({keys}): ").strip()
    except EOFError:
        return None
    return choice


def launch_and_monitor(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # 第1引数がモード番号ならメニューを出さずに起動する（例: main.py 3 --max 1）。
    forced = None
    if argv and argv[0] in MENU:
        forced = argv.pop(0)

    proc = None
    try:
        proc = browser.ensure_chrome_running()
        while True:
            choice = forced if forced is not None else choose_mode()
            forced = None
            if choice is None:
                ui.out("\n入力が終了したためプログラムを終了します。", "dim")
                return 0
            if choice == "0":
                ui.out("プログラムを終了します。", "dim")
                return 0
            entry = MENU.get(choice)
            if entry is None:
                ui.out("無効な選択です。", "yellow")
                continue
            label, script_name, *rest = entry
            default_args = rest[0] if rest else ()
            ui.out(f"{label}を開始します。", "bold green")
            code = run_script(script_name, [*default_args, *argv])
            argv = []
            if code:
                return code
    except KeyboardInterrupt:
        ui.out("\nプログラムを終了します。", "dim")
        return 130
    except Exception as e:
        ui.out(f"エラーが発生しました: {e}", "bold red")
        traceback.print_exc()
        return 1
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(launch_and_monitor())