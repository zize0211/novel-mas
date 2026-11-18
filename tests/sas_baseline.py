"""SAS 对照组 —— 一个大 prompt，一次生成。

**这不是第二个项目**，是同一个项目里的对照实验：

    输入：跟 MAS 的写手**一模一样**（同一份设定、同一个大纲、同一段文风）
    唯一变量：MAS 走完整链路（三裁判 + 可能重写），SAS 一次成型

为什么要一模一样：不一样就没法比。所以 prompt 是照着 orchestrator 里
那段 base_prompt 抄的 —— 改那边的时候记得同步这边。

跑法：
    $env:PYTHONPATH = 'E:\\novel-mas\\src'
    $env:PYTHONIOENCODING = 'utf-8'
    python tests\\sas_baseline.py --book demo --chapter 1

产物落 data/out/ch01.sas.txt，跟 MAS 的 ch01.txt 并排放。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novel_mas.db import base as db
from novel_mas.llm import OpenAIProvider

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"


def read_materials(book_id: str, chapter: int) -> dict:
    """从三个库读材料。**只读**，不改任何东西。

    这是实验脚本，直接连库不算破隔离 —— 它不在 agent 体系里，
    也没有任何工具白名单。真正的隔离测的是 agent 那五个。
    """
    conn = db.connect("world.db")
    try:
        rules = conn.execute(
            "SELECT title, body FROM world_bible WHERE book_id = ? ORDER BY id",
            (book_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT kind, title, summary, constraints, chapter FROM events"
            " WHERE book_id = ? ORDER BY id",
            (book_id,),
        ).fetchall()
    finally:
        conn.close()

    conn = db.connect("char.db")
    try:
        chars = conn.execute(
            "SELECT name, importance, profile, state FROM characters"
            " WHERE book_id = ? ORDER BY id",
            (book_id,),
        ).fetchall()
        rule = conn.execute(
            "SELECT threshold FROM score_rules WHERE book_id = ?",
            (book_id,),
        ).fetchone()
    finally:
        conn.close()

    conn = db.connect("trend.db")
    try:
        curve = conn.execute(
            "SELECT target, tolerance FROM curve WHERE book_id = ? AND chapter = ?",
            (book_id, chapter),
        ).fetchone()
        intent = conn.execute(
            "SELECT hard_constraints FROM ending_intent WHERE book_id = ?",
            (book_id,),
        ).fetchone()
    finally:
        conn.close()

    if curve is None:
        raise SystemExit(f"第 {chapter} 章没有曲线，先设一条")

    # 硬约束 = 结局意图里的 + 大事件锁死的
    constraints: list[str] = []
    if intent and intent["hard_constraints"]:
        import json

        constraints.extend(json.loads(intent["hard_constraints"]))
    for e in events:
        if e["kind"] == "major" and e["constraints"]:
            import json

            constraints.extend(json.loads(e["constraints"]))

    return {
        # 文风不落库（writer.set_style 只返回个 id），由命令行传，
        # 要跟 MAS 那次的 --style 一模一样才可比
        "style": "日常",
        "target": curve["target"],
        "tolerance": curve["tolerance"],
        "rules": rules,
        "events": events,
        "chars": chars,
        "constraints": constraints,
    }


def build_prompt(m: dict, chapter: int, brief: str) -> str:
    """照着 orchestrator 的 base_prompt 拼 —— 两边必须一样，不然比的不是一回事。"""
    char_block = "\n".join(
        f"- {c['name']}（{c['importance']}）：{c['profile']}；当前 {c['state'] or '未记'}"
        for c in m["chars"]
    ) or "- （无人物档）"
    world_block = "\n".join(f"- {r['body']}" for r in m["rules"]) or "- （无世界观）"

    # 前情：跟 writer.get_worldview 的 recap 口径一致（最近 8 条 minor，按章倒序再翻正）
    minor = [e for e in m["events"] if e["kind"] == "minor" and e["summary"]]
    minor = minor[-8:]
    recap_block = "\n".join(
        f"- 第 {e['chapter']} 章：{e['summary']}" for e in reversed(minor)
    ) or "- （这是第一章，前面没有故事）"

    return (
        f"【文风】{m['style']}\n"
        f"【本章目标】第 {chapter} 章，走向分目标 {m['target']}"
        f"（容差 ±{m['tolerance']}）\n"
        f"【前情】\n{recap_block}\n"
        f"【大纲】{brief}\n"
        f"【人物】\n{char_block}\n"
        f"【世界观】\n{world_block}\n"
        f"【硬约束】{m['constraints'] or '无'}\n"
        f"请写这一章的正文。"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="SAS 对照组：一次生成一章")
    ap.add_argument("--book", default="demo")
    ap.add_argument("--chapter", type=int, required=True)
    ap.add_argument("--brief", required=True, help="本章大纲")
    ap.add_argument("--style", default="日常", help="文风，跟 MAS 那次一致才可比")
    ap.add_argument(
        "--dry-run", action="store_true", help="只打印 prompt，不调模型（省钱，用来核对两组输入是否一样）"
    )
    args = ap.parse_args()

    m = read_materials(args.book, args.chapter)
    m["style"] = args.style
    prompt = build_prompt(m, args.chapter, args.brief)

    if args.dry_run:
        print(prompt)
        print()
        print(f"（dry-run：上面这段就是 SAS 唯一的输入，{len(prompt)} 字）")
        return 0

    llm = OpenAIProvider.from_env()
    text = llm.complete(prompt, system="你是小说写手。只写正文，不要解释。")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"ch{args.chapter:02d}.sas.txt"
    out.write_text(text, encoding="utf-8")

    print(f"第 {args.chapter} 章（SAS，一次成型）")
    print(f"  正文 {len(text)} 字")
    print(f"  用掉 token {llm.tokens_used}")
    print(f"  落盘 {out}")
    print()
    print("下一步：拿它跟 MAS 的正文比 —— 用同一把尺子")
    print(f"  python tests\\audit_chapter.py --book {args.book} --chapter {args.chapter} "
          f"--file data\\out\\ch{args.chapter:02d}.sas.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
