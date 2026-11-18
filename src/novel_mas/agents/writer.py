"""写作 agent —— 唯一的产出方。

它的工具**全是取**，没有一个是写：
    文风 / 曲线 / 人物 / 世界观 / 小事链接（只提交候选）

写手能改的只有正文。想动设定、人设、走向，都得走单向工具去求。

实现状态：接口定稿，全部 stub（等 demo）。
"""

from __future__ import annotations
from ..db import base as db
from ..types import CharacterSnapshot, CurvePoint
from . import BaseAgent
import json

class Writer(BaseAgent):
    role = "writer"
    db_name = None  # 它没有自己的库

    # ---------- 工具 1：文风设定 ----------

    def set_style(
        self,
        book_id: str,
        chapter: int | None = None,
        preset: str | None = None,
        custom: str | None = None,
    ) -> dict:
        """设文风。可按章换（回忆章可以换个笔调）。"""
        if not preset and not custom:
            raise ValueError("preset 和 custom 至少给一个")
        resolved = custom or preset
        return {
            "style_id": f"{book_id}:{'ch' + str(chapter) if chapter else 'book'}",
            "resolved": resolved,
            "scope": "chapter" if chapter else "book",
        }

    # ---------- 工具 2：拿大纲曲线 ----------

    def get_curve(self, book_id: str, chapter: int) -> CurvePoint:
        """只取本章的目标分与容忍带，不是全书。
        
        写手没有自己的库 (db_name=None)，所以这里连的是trend.db的只读口:
        只 SELECT ，没有任何写语句。无法修改曲线。
        """
        conn = db.connect("trend.db")
        try:
            row = conn.execute(
                "SELECT chapter, target, tolerance FROM curve "
                "WHERE book_id=? AND chapter=?",
                (book_id, chapter),
            ).fetchone()
            if row is None:
                raise LookupError(f"{book_id} 第 {chapter} 章的曲线不存在")
        finally:
            conn.close()

        return CurvePoint(
            chapter=row["chapter"], 
            target=row["target"], 
            tolerance=row["tolerance"],
        )

    # ---------- 工具 3：人物信息获取 ----------

    def get_characters(
        self, book_id: str, names: list[str] | None = None
    ) -> list[CharacterSnapshot]:
        """拿人物档的只读快照。

        必须带 as_of_chapter —— 否则会拿第 3 章的旧状态写第 12 章。

        关系表一起取：一条关系在库里只存一行 (a, b)，但两个人身上都得挂，
        所以下面按 a / b 各展开一次，再按人名归组。
        """
        conn = db.connect("char.db")
        try:
            sql = (
                "SELECT name, importance, profile, state, as_of_chapter "
                "FROM characters WHERE book_id = ?"
            )
            params: list = [book_id]
            if names:
                placeholders = ",".join("?" for _ in names)
                sql += f" AND name IN ({placeholders})"
                params.extend(names)
            rows = conn.execute(sql, params).fetchall()

            # 关系表也认 names，别全查（一个人物多起来就浪费）
            rel_sql = "SELECT a, b, kind, intensity FROM relationships WHERE book_id = ?"
            rel_params: list = [book_id]
            if names:
                ph = ",".join("?" for _ in names)
                rel_sql += f" AND (a IN ({ph}) OR b IN ({ph}))"
                rel_params.extend(names)   # a 那边的问号
                rel_params.extend(names)   # b 那边的问号，必须再来一遍
            rel_rows = conn.execute(rel_sql, rel_params).fetchall()
        finally:
            conn.close()

        # {人名: [{"with":..., "kind":..., "intensity":...}, ...]}
        rel_map: dict[str, list[dict]] = {}
        for r in rel_rows:
            for side, other in ((r["a"], r["b"]), (r["b"], r["a"])):
                rel_map.setdefault(side, []).append(
                    {"with": other, "kind": r["kind"], "intensity": r["intensity"]}
                )

        return [
            CharacterSnapshot(
                name=row["name"],
                importance=row["importance"],
                profile=row["profile"],
                state=row["state"],
                as_of_chapter=row["as_of_chapter"],
                relationships=rel_map.get(row["name"], []),
            )
            for row in rows
        ]

    # ---------- 工具 4：世界观获取 ----------

    def get_worldview(
        self, book_id: str, topics: list[str] | None = None
    ) -> dict:
        """只读世界观条目 + 本章硬约束。

        规则同世界意志：只读条目，不读正文全文。

        约束有两个来源，都要收：
          1. world.db 的 events.constraints —— 大事件锁死的可能性
          2. trend.db 的 ending_intent.hard_constraints —— 结局意图里的红线
        """
        conn = db.connect("world.db")
        try:
            # 按书取：两本书的世界观不能串
            sql = "SELECT id, category, title, body FROM world_bible WHERE book_id = ?"
            params: list = [book_id]
            if topics:
                ph = ",".join("?" for _ in topics)
                sql += f" AND category IN ({ph})"
                params.extend(topics)

            rows = conn.execute(sql, params).fetchall()
            ev_rows = conn.execute(
                "SELECT constraints FROM events "
                "WHERE book_id = ? AND kind = 'major'",
                (book_id,),
            ).fetchall()
            # 前面几章发生过什么（小事）。写手要接得上，
            # 不然每一章都像第一章 —— 人还在原地，事还没发生。
            recap_rows = conn.execute(
                "SELECT chapter, title, summary FROM events "
                "WHERE book_id = ? AND kind = 'minor' AND chapter IS NOT NULL "
                "ORDER BY chapter DESC, id DESC LIMIT 8",
                (book_id,),
            ).fetchall()
        finally:
            conn.close()

        # 条目去重：库里重复插过（跟 chapter_scores 同一类问题）
        seen: set[tuple] = set()
        entries: list[dict] = []
        for r in rows:
            key = (r["category"], r["title"], r["body"])
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {"id": r["id"], "category": r["category"], "body": r["body"]}
            )

        # 大事件的约束
        constraints: list[str] = []
        for r in ev_rows:
            if r["constraints"]:
                constraints.extend(json.loads(r["constraints"]))

        # 结局意图的硬约束（在另一个库）
        conn = db.connect("trend.db")
        try:
            row = conn.execute(
                "SELECT hard_constraints FROM ending_intent WHERE book_id = ?",
                (book_id,),
            ).fetchone()
        finally:
            conn.close()
        if row and row["hard_constraints"]:
            constraints.extend(json.loads(row["hard_constraints"]))

        # 前面几章发生过什么（按章倒序取，给 orchestrator 塞进 prompt）
        recap = [
            {"chapter": r["chapter"], "title": r["title"], "summary": r["summary"]}
            for r in recap_rows
        ]

        return {"entries": entries, "constraints": constraints, "recap": recap}

    # ---------- 工具 5：小事链接（只提交候选） ----------

    def link_minor_events(
        self, from_id: int, to_id: int, kind: str, note: str = ""
    ) -> dict:
        """提「这两件小事像有呼应」。**只产出候选，不写库。**

        真正入库要经世界意志查环 + 查依据。
        否则写手会给自己发明的伏笔补一条"回收"，把漏洞焊上。
        """
        return {
            "candidate": {"from": from_id, "to": to_id, "kind": kind},
            "status": "pending_review",
            "note": note,
        }


def bind(registry) -> None:
    agent = Writer()
    registry.register("set_style", agent.set_style)
    registry.register("get_curve", agent.get_curve)
    registry.register("get_characters", agent.get_characters)
    registry.register("get_worldview", agent.get_worldview)
    registry.register("link_minor_events", agent.link_minor_events)
