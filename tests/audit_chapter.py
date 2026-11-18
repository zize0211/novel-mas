"""审一章：用三个裁判跑一遍，报出「命中几条问题」。

为什么要有它：
    不依赖人读的可复现检查。拿它同时测 MAS 生成的正文和单次生成的正文，
    比的就是同一把尺子 —— 不涉及「谁写得更美」。

跑法：
    $env:PYTHONPATH = 'E:\\novel-mas\\src'
    $env:PYTHONIOENCODING = 'utf-8'
    python tests\\audit_chapter.py --book demo --chapter 1 --file data\\out\\ch01.txt

会调真模型（世界裁判 1 次 + 走向 1 次 + 每个人物 2 次）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novel_mas.agents import bootstrap
from novel_mas.llm import OpenAIProvider
from novel_mas.tools.registry import ToolRegistry


def main() -> int:
    ap = argparse.ArgumentParser(description="审一章，报出命中几条问题")
    ap.add_argument("--book", default="demo", help="书号（默认 demo）")
    ap.add_argument("--chapter", type=int, required=True, help="第几章")
    ap.add_argument("--file", required=True, help="正文文件路径")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"找不到文件：{path}")
        return 2

    text = path.read_text(encoding="utf-8")
    llm = OpenAIProvider.from_env()
    # 这个脚本通篇都是判官，没有写作 —— 全程低温，跟 orchestrator 里 judge_mode 一个口径。
    # 实测 0.8 下同一份正文分数在 55~72 之间跳，0.1 下三次都是 64。
    llm.temperature = 0.1
    reg = bootstrap(ToolRegistry(), llm=llm, book_id=args.book)

    print("=" * 60)
    print(f"审：{path.name}    第 {args.chapter} 章    书 {args.book}")
    print(f"正文 {len(text)} 字")
    print("=" * 60)

    hard = 0
    soft = 0

    # ---- 1. 世界一致性 ----
    j = reg.call("world_will", "check_consistency", claim=text, chapter=args.chapter)
    print()
    print(f"[世界] {j.verdict.value} — {j.reason}")
    if j.verdict.value == "hard_conflict":
        hard += 1
    elif j.verdict.value == "soft_warn":
        soft += 1

    # ---- 2. 走向 ----
    d = reg.call(
        "trend", "score_direction", book_id=args.book, text=text, chapter=args.chapter
    )
    print()
    print(
        f"[走向] {d.verdict.value}    {d.score} 分 / 目标 {d.target}"
        f"（偏差 {d.deviation:+d}）"
    )
    for e in d.evidence:
        print(f"        事件 {e['kind']}（{e['weight']:+d}）：{e['what']}")
    if d.verdict.value == "violated":
        hard += 1
    elif d.verdict.value == "drifted":
        soft += 1

    # ---- 3. 人物（只评 lead）----
    chars = reg.call("writer", "get_characters", book_id=args.book)
    leads = [c for c in chars if c.importance == "lead"]
    if not leads:
        print()
        print("[人物] 这本书没有 lead 级角色，跳过")
    for c in leads:
        r = reg.call(
            "character",
            "score_in_character",
            book_id=args.book,
            name=c.name,
            text=text,
            chapter=args.chapter,
        )
        print()
        mark = "过" if r["passed"] else "不过"
        print(f"[人物] {r['name']} {r['score']} 分（阈值 {r['threshold']}，{mark}）")
        for k, v in r["views"].items():
            print(f"        {k:7s} {v['score']:3d}  {v['reason']}")
        for v in r["violations"]:
            print(f"        偏离：{v}")
        if not r["passed"]:
            hard += 1
        elif r["violations"]:
            soft += 1

    # ---- 汇总 ----
    print()
    print("=" * 60)
    print(f"硬问题 {hard} 条（必须改）    软问题 {soft} 条（可以改）")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
