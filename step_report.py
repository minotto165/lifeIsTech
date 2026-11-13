"""ステップごとの所要時間ログ（LIFEISTECH_STEP_LOG）を集計する。

使い方:
    LIFEISTECH_STEP_LOG=/tmp/steps.jsonl uv run python main.py   # 記録する
    uv run python step_report.py /tmp/steps.jsonl                # 集計する

ログは 1 ステップ 1 行の JSON で、`step` が小さくなったところで別の実行とみなします。
"""

import collections
import json
import sys

DEFAULT_PATH = "/tmp/lifeistech_steps.jsonl"


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def step_number(row):
    try:
        return int(row.get("step", 0))
    except (TypeError, ValueError):
        return 0


def split_runs(rows):
    """`step` が戻ったら新しい実行として分割する。"""
    runs = []
    current = []
    for row in rows:
        if current and step_number(row) <= step_number(current[-1]):
            # ページを開いた直後の 1 行（0 秒）は実行の区切りとして扱わない。
            if not (len(current) == 1 and current[0].get("sec", 0) < 0.05):
                runs.append(current)
            current = []
        current.append(row)
    if current:
        runs.append(current)
    return runs


def summarize(rows):
    total = sum(r.get("sec", 0) for r in rows)
    print(f"{len(rows)} ステップ / 合計 {total:.1f} 秒 / 平均 {total / max(len(rows), 1) * 1000:.0f} ms per step")
    if not rows:
        return

    by_exit = collections.defaultdict(list)
    by_elements = collections.defaultdict(list)
    for r in rows:
        by_exit["+".join(map(str, r.get("exit") or ["(none)"]))].append(r.get("sec", 0))
        by_elements["+".join(r.get("elements") or ["(none)"])].append(r.get("sec", 0))

    print("--- 終了条件べつ")
    for key, secs in sorted(by_exit.items(), key=lambda kv: -sum(kv[1])):
        print(f"{key[:34]:34s} n={len(secs):3d} total={sum(secs):7.1f}s avg={sum(secs) / len(secs):5.2f}s max={max(secs):5.2f}s")

    print("--- 要素構成べつ（合計時間の多い順）")
    for key, secs in sorted(by_elements.items(), key=lambda kv: -sum(kv[1]))[:15]:
        print(f"{key[:60]:60s} n={len(secs):3d} total={sum(secs):7.1f}s avg={sum(secs) / len(secs):5.2f}s")

    print("--- 遅いステップ 20 件")
    for r in sorted(rows, key=lambda r: -r.get("sec", 0))[:20]:
        print(
            f"{r.get('sec', 0):6.2f}s step {step_number(r):4d} loops {r.get('loops', 0)} "
            f"exit {r.get('exit')} els {r.get('elements')} talk {r.get('talk_chars')} "
            f"handled {r.get('handled')} marks {r.get('marks')}"
        )

    talks = [r for r in rows if "TalkFinish" in (r.get("exit") or [])]
    slow = sorted((r for r in talks if r.get("sec", 0) > 0.5), key=lambda r: -r["sec"])
    print(f"--- 吹き出し（TalkFinish）{len(talks)} 件中、0.5 秒を超えたもの {len(slow)} 件")
    for r in slow[:12]:
        print(
            f"  {r['sec']:5.2f}s step {step_number(r):4d} 吹き出し出現 {r.get('marks', {}).get('talk')} 秒 "
            f"文字数 {r.get('talk_chars')}"
        )


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    rows = load(path)
    if not rows:
        print(f"{path} にログがありません（LIFEISTECH_STEP_LOG を設定して実行してください）。")
        return 1
    runs = split_runs(rows)
    for i, run in enumerate(runs, 1):
        print(f"=== 実行 {i} ===")
        summarize(run)
    if len(runs) > 1:
        print("=== 全実行の合計 ===")
        summarize(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
