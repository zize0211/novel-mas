r"""最小闭环 —— 编排者（orchestrator）。

它做的事，就是把这七步串起来：

    1. 定文风
    2. 取本章曲线（目标分 + 容忍带）
    3. 取人物快照
    4. 取世界观条目 + 本章硬约束
    5. 让模型写正文
    6. 世界意志过闸
    7. 走向打分 → 落库 → 写出文件

跑法：
    $env:PYTHONPATH = 'E:\novel-mas\src'
    $env:PYTHONIOENCODING = 'utf-8'
    python -m novel_mas.orchestrator

设计原则：
    - 用假模型也能跑通（不花钱、可重复）
    - 每一步都真的读写库，不是演示
    - 打回时把「失分项」写清楚，别只说不行
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import base as db
from .llm import FakeProvider, Provider

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "out"

POSITIVE = ("重逢", "温暖", "一起", "笑", "归来", "灯火", "光", "雨停")
NEGATIVE = ("死", "别离", "毁", "血", "断", "哭", "冷", "埋")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class WriteResult:
    chapter: int
    text: str
    verdict: str            # pass | soft_warn | violated
    score: int
    target: int
    deviation: int
    tokens: int
    evidence: list[str] = field(default_factory=list)
    out_path: str | None = None

    def brief(self) -> str:
        lines = [
            f"第 {self.chapter} 章 · {self.verdict.upper()}",
            f"  走向分 {self.score} / 目标 {self.target}（偏差 {self.deviation:+d}）",
            f"  用掉 token {self.tokens}",
        ]
        if self.evidence:
            lines.append("  证据：")
            for e in self.evidence:
                lines.append(f"    - {e}")
        if self.out_path:
            lines.append(f"  落盘：{self.out_path}")
        return "\n".join(lines)


class Orchestrator:
    """最小闭环的调度器。没有 LLM 也能跑。"""

    def __init__(self, book_id: str = "demo", llm: Provider | None = None) -> None:
        self.book_id = book_id
        self.llm: Provider = llm or FakeProvider()
        self.style = "日常"
        db.migrate()

    # ---------------- 配置阶段 ----------------

    def ingest_worldview(self, prompt: str) -> int:
        """把一段世界观拆成条目入库。简化版：按句拆，不调模型。"""
        parts = [p.strip() for p in prompt.replace("；", "。").split("。") if p.strip()]
        conn = db.connect("world.db")
        n = 0
        try:
            for i, body in enumerate(parts, 1):
                conn.execute(
                    "INSERT INTO world_bible(category,title,body,version,source,created_at)"
                    " VALUES(?,?,?,?,?,?)",
                    ("规则", f"条目{i}", body, 1, "user_inject", _now()),
                )
                n += 1
            conn.commit()
        finally:
            conn.close()
        return n

    def add_character(self, name: str, profile: str, importance: str = "lead") -> None:
        conn = db.connect("char.db")
        try:
            conn.execute(
                "INSERT OR REPLACE INTO characters"
                "(book_id,name,importance,profile,state,as_of_chapter,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (self.book_id, name, importance, profile, "初登场", 0, _now()),
            )
            conn.commit()
        finally:
            conn.close()

    def set_trend(
        self,
        points: list[tuple[int, int, int]],
        ending_type: str = "happy",
        target_score: int = 80,
        hard_constraints: list[str] | None = None,
    ) -> None:
        conn = db.connect("trend.db")
        try:
            conn.execute(
                "INSERT OR REPLACE INTO ending_intent"
                "(book_id,ending_type,target_score,hard_constraints,soft_preferences,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (
                    self.book_id,
                    ending_type,
                    target_score,
                    json.dumps(hard_constraints or [], ensure_ascii=False),
                    "[]",
                    _now(),
                ),
            )
            for chapter, target, tol in points:
                conn.execute(
                    "INSERT OR REPLACE INTO curve(book_id,chapter,target,tolerance,created_at)"
                    " VALUES(?,?,?,?,?)",
                    (self.book_id, chapter, target, tol, _now()),
                )
            conn.commit()
        finally:
            conn.close()

    # ---------------- 取数阶段（写手的四个工具，真读库） ----------------

    def _get_curve(self, chapter: int) -> tuple[int, int]:
        conn = db.connect("trend.db")
        try:
            row = conn.execute(
                "SELECT target,tolerance FROM curve WHERE book_id=? AND chapter=?",
                (self.book_id, chapter),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise LookupError(f"没有第 {chapter} 章的曲线")
        return row["target"], row["tolerance"]

    def _get_characters(self) -> list[dict[str, Any]]:
        conn = db.connect("char.db")
        try:
            rows = conn.execute(
                "SELECT name,importance,profile,state FROM characters WHERE book_id=?",
                (self.book_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def _get_worldview(self) -> tuple[list[str], list[str]]:
        conn = db.connect("world.db")
        try:
            entries = [
                r["body"]
                for r in conn.execute(
                    "SELECT body FROM world_bible ORDER BY id"
                ).fetchall()
            ]
        finally:
            conn.close()

        conn = db.connect("trend.db")
        try:
            row = conn.execute(
                "SELECT hard_constraints FROM ending_intent WHERE book_id=?",
                (self.book_id,),
            ).fetchone()
        finally:
            conn.close()
        hard = json.loads(row["hard_constraints"]) if row and row["hard_constraints"] else []
        return entries, hard

    # ---------------- 判官（简化版，不调模型） ----------------

    @staticmethod
    def _judge(text: str, hard_constraints: list[str]) -> tuple[str, list[str]]:
        """三级裁决的简化实现：硬约束里提到"不能死"，正文出现"死"就违例。"""
        violations: list[str] = []
        for rule in hard_constraints:
            if "不能死" in rule and "死" in text:
                violations.append(f"违反硬约束「{rule}」：正文出现了『死』")
            if "不能离开" in rule and "离开" in text:
                violations.append(f"违反硬约束「{rule}」")
        if violations:
            return "violated", violations
        return "pass", []

    @staticmethod
    def _score(text: str) -> tuple[int, list[str]]:
        """走向分：高=暖，低=冷。这里用词频做极简估计。"""
        pos = sum(text.count(w) for w in POSITIVE)
        neg = sum(text.count(w) for w in NEGATIVE)
        raw = 50 + (pos - neg) * 10
        score = max(0, min(100, raw))
        ev = [f"暖词 {pos} 个 / 冷词 {neg} 个"]
        return score, ev

    # ---------------- 写一章 ----------------

    def write_chapter(
        self,
        chapter: int,
        brief: str,
        mock_text: str | None = None,
    ) -> WriteResult:
        style = self.style
        target, tol = self._get_curve(chapter)
        chars = self._get_characters()
        entries, hard = self._get_worldview()

        char_block = "\n".join(
            f"- {c['name']}（{c['importance']}）：{c['profile']}；当前 {c['state']}"
            for c in chars
        ) or "- （无人物档）"
        world_block = "\n".join(f"- {e}" for e in entries) or "- （无世界观）"

        prompt = (
            f"【文风】{style}\n"
            f"【本章目标】第 {chapter} 章，走向分目标 {target}（容差 ±{tol}）\n"
            f"【大纲】{brief}\n"
            f"【人物】\n{char_block}\n"
            f"【世界观】\n{world_block}\n"
            f"【硬约束】{hard}\n"
            f"请写这一章的正文。"
        )

        text = mock_text if mock_text is not None else self.llm.complete(prompt)
        verdict, evidence = self._judge(text, hard)
        score, score_ev = self._score(text)
        deviation = score - target

        # 打分工具的三级判定
        if verdict == "violated":
            trend_verdict = "violated"
        elif abs(deviation) > tol:
            trend_verdict = "drifted"
        else:
            trend_verdict = "on_track"

        conn = db.connect("trend.db")
        try:
            conn.execute(
                "INSERT INTO chapter_scores"
                "(book_id,chapter,score,target,deviation,verdict,evidence,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    self.book_id,
                    chapter,
                    score,
                    target,
                    deviation,
                    trend_verdict,
                    json.dumps(evidence + score_ev, ensure_ascii=False),
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        # 小事件落库（本章发生了什么）
        conn = db.connect("world.db")
        try:
            conn.execute(
                "INSERT INTO events"
                "(book_id,kind,title,summary,chapter,constraints,caused_by,source,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    self.book_id,
                    "minor",
                    f"第{chapter}章正文成稿",
                    brief[:80],
                    chapter,
                    None,
                    None,
                    "writer",
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        # 落盘（只有过闸才写出成文）
        out_path = None
        if verdict == "pass":
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            p = OUT_DIR / f"ch{chapter:02d}.txt"
            p.write_text(text, encoding="utf-8")
            out_path = str(p)

        return WriteResult(
            chapter=chapter,
            text=text,
            verdict=verdict if trend_verdict != "violated" else "violated",
            score=score,
            target=target,
            deviation=deviation,
            tokens=self.llm.tokens_used,
            evidence=evidence + score_ev,
            out_path=out_path,
        )


# ---------------- 命令行入口 ----------------

def main() -> None:
    print("=" * 62)
    print("最小闭环：注入世界观 → 定人设 → 设曲线 → 写一章 → 过闸 → 打分")
    print("=" * 62)

    orc = Orchestrator(book_id="demo", llm=FakeProvider(style="日常"))

    n = orc.ingest_worldview(
        "月光城的雨从不停。城东有座旧钟楼，钟声一响，全城的人都会停下手里的活。"
        "李家在城南摆豆腐摊，已经三代。"
    )
    print(f"\n[1] 世界观入库：{n} 条")

    orc.add_character("李三", "豆腐摊第三代，怕生，说话前会先摸一下耳朵", "lead")
    print("[2] 人物入库：李三")

    orc.set_trend(
        points=[(1, 50, 15), (2, 60, 15), (3, 75, 15)],
        ending_type="happy",
        target_score=80,
        hard_constraints=["李三不能死"],
    )
    print("[3] 曲线入库：第1章 50 / 第2章 60 / 第3章 75")

    print("\n[4] 写第 1 章（正常）")
    r1 = orc.write_chapter(1, "李三在巷口捡到一枚铜钱，抬头看见钟楼上有个人影。")
    print(r1.brief())
    print("  正文开头：", r1.text.splitlines()[-1][:40], "...")

    print("\n[5] 写第 2 章（故意踩硬约束，看它拦不拦）")
    r2 = orc.write_chapter(
        2,
        "李三病倒了。",
        mock_text="那一夜雨没停。李三死了，血从门槛一路漫到街心，没人敢去收。",
    )
    print(r2.brief())
    print("  是否写出了成文文件：", r2.out_path or "没有（被拦下）")

    print("\n" + "=" * 62)
    print("闭环跑完。四件事都真的发生了：")
    print("  - 三个库都有新数据")
    print("  - 第 1 章过闸并落盘")
    print("  - 第 2 章踩了「李三不能死」被拦，没有落盘")
    print("  - 打分和历史都记在 chapter_scores 里")
    print("=" * 62)


if __name__ == "__main__":
    main()
