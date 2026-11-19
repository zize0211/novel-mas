"""人物控制 agent —— 管「这人会不会这样」。

隔离：独占 char.db；写手只能通过工具拿只读快照，碰不到本体。
工具：3 个。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ..db import base as db
from ..llm import Provider, parse_json_block
from ..types import SCORE_MAX, SCORE_MIN, CharacterSnapshot
from . import BaseAgent


class CharacterControl(BaseAgent):
    role = "character"
    db_name = "char.db"

    def __init__(self, book_id: str = "demo", llm: Provider | None = None) -> None:
        self.book_id = book_id
        self.llm = llm

    # ---------- 工具 1：随机生成角色 ----------

    def random_character(
        self, book_id: str, need: str, importance: str = "minor"
    ) -> CharacterSnapshot:
        """按剧情需要现生成一个角色。

        必须先在 char.db 里查重名 —— 否则会出现两个"李三"。
        撞名了不自动改名：名字是读者认人的锚，机器随手加个后缀比报错更糟。
        """
        if importance not in {"lead", "support", "minor"}:
            raise ValueError(
                f"importance 只能是 lead / support / minor，收到 {importance!r}"
            )
        if self.llm is None:
            raise RuntimeError("生成角色要模型：先给 CharacterControl(llm=...)")

        existing = self._names(book_id)
        raw = self.llm.complete(
            "为这个故事现编一个角色，要能接住下面这个需要。\n\n"
            f"【需要】{need}\n"
            f"【分量】{importance}（lead=主角级，support=配角，minor=过场）\n\n"
            + (
                f"【已经有这些名字，绝对不许撞】{'、'.join(existing)}\n\n"
                if existing
                else ""
            )
            + "只输出 JSON：\n"
            '{"name": "名字", '
            '"profile": "三句话人设：出身 / 想要什么 / 藏着什么", '
            '"state": "出场时的状态（在哪、什么处境）"}',
            system="你只输出 JSON，不聊天，不解释。",
        )
        data = parse_json_block(raw)

        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("模型没给名字")
        if name in existing:
            raise ValueError(
                f"撞名了：{name!r} 已经在 char.db 里。换个 need 重生成，"
                "或者用 import_character 改那个人"
            )

        profile = str(data.get("profile", "")).strip()
        if not profile:
            raise ValueError("模型没给人设正文")

        state = str(data.get("state") or "").strip() or None
        self._insert(book_id, name, importance, profile, state, None)

        return CharacterSnapshot(
            name=name,
            importance=importance,
            profile=profile,
            state=state,
            as_of_chapter=None,
        )

    # ---------- 工具 2：导入角色 ----------

    def import_character(
        self, book_id: str, name: str, profile: str, importance: str = "lead"
    ) -> CharacterSnapshot:
        """导入主角 / 配角档。用户可指定重要程度。"""
        if importance not in {"lead", "support", "minor"}:
            raise ValueError(
                f"importance 只能是 lead / support / minor，收到 {importance!r}"
            )
        name, profile = name.strip(), profile.strip()
        if not name or not profile:
            raise ValueError("名字和人设都不能空")

        self._insert(book_id, name, importance, profile, None, None)
        return CharacterSnapshot(
            name=name,
            importance=importance,
            profile=profile,
            state=None,
            as_of_chapter=None,
        )

    # ---------- 工具 5：删人物 ----------

    def remove_character(self, book_id: str, name: str) -> dict:
        """删掉一个人物：档案、逐章状态、关系里提到他的行。

        删是真删（没有回收站），所以名字对不上就报错，不做「模糊匹配」——
        一个字打错把别人删了，比报错难处理得多。
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("名字不能空")
        conn = self.connect()
        try:
            cur = conn.execute(
                "DELETE FROM characters WHERE book_id = ? AND name = ?", (book_id, name)
            )
            if cur.rowcount == 0:
                raise LookupError(f"{book_id} 里没有叫「{name}」的人物")
            states = conn.execute(
                "DELETE FROM character_states WHERE book_id = ? AND name = ?",
                (book_id, name),
            ).rowcount
            rels = conn.execute(
                "DELETE FROM relationships WHERE book_id = ? AND (a = ? OR b = ?)",
                (book_id, name, name),
            ).rowcount
            conn.commit()
        finally:
            conn.close()
        return {
            "ok": True,
            "name": name,
            "states_removed": states,
            "relationships_removed": rels,
        }

    # ---------- 工具 3：角色剧情打分 ----------

    # 三视角的权重。合起来才是「像不像他」。
    # self 最重：人物一致性首先是内在逻辑成立，不是旁人看着顺眼。
    VIEW_WEIGHTS: dict[str, float] = {
        "self": 0.5,
        "others": 0.25,
        "reader": 0.25,
    }

    def score_in_character(
        self, book_id: str, name: str, text: str, chapter: int | None = None
    ) -> dict:
        """给剧情里的角色行为打「符合人设」分 —— 两趟走。

        第一趟：只挑刺（三个自问），不评分。没有打分压力，挑得更狠。
        第二趟：带着挑出来的毛病 + 三视角 + 人设，给分。

        为什么要两趟：一趟里既要挑刺又要打分，模型会把劲头分给打分，
        挑出来的毛病就变少变虚。拆开之后，两件事各自专心。

        规则（已定）：
        - 阈值 x 从 char.db 的 score_rules 读，不写死在代码里
        - 必须给出「不符合哪一条人设」，不能只给数字
        - 低于 x → 打回，并把失分项回传给写作 agent
        """
        if self.llm is None:
            raise RuntimeError("判人设要模型：先给 CharacterControl(llm=...)")

        profile = self._profile(book_id, name)
        threshold = self._threshold(book_id)

        # 第一趟：挑刺。不看人设，只看这段文字本身有没有硬伤。
        findings = self._critique(text)
        findings_block = (
            "\n".join(f"- {f}" for f in findings) if findings else "- （没挑出毛病）"
        )

        # 第二趟：三视角打分，把第一趟的毛病算进去。
        raw = self.llm.complete(
            f"【第一趟挑出的毛病】\n{findings_block}\n\n"
            f"【角色人设】\n{profile}\n\n"
            f"【这段行为】\n{text}\n\n"
            "从三个视角分别判断这段行为像不像这个人。\n\n"
            "1. self —— 第一视角，他自己：符合他自己的动机、创伤、欲望吗？\n"
            "   注意：一个受过伤的人做出「重复创伤」的事，是符合内在逻辑的，不算 OOC。\n"
            "2. others —— 第二视角，他身边的人：认识他的人看到，会觉得「这不像他」吗？\n"
            "3. reader —— 第三视角，读者：读到这段，会觉得突兀、像作者硬掰吗？\n"
            "   这一视角必须回答三件事，缺一件就不算评完：\n"
            "   a) 他为什么要这么做 —— 动机在文本里立得住吗？\n"
            "   b) 这件事跟别的已发生的事有因果关系吗，还是孤零零掉下来的一块？\n"
            "   c) 读者读到这里是觉得顺，还是觉得作者按着头解释？\n\n"
            "打分标尺（照着给，别都往高处报）：\n"
            "- 90-100：完全立得住，挑不出毛病\n"
            "- 70-89 ：成立，但有可以更好的地方\n"
            "- 50-69 ：有明确问题，读的人会皱眉\n"
            "- 30-49 ：明显不对劲\n"
            "- 0-29  ：完全立不住\n"
            "三个视角要拉开差距——如果三个都给 80 上下，说明没分开评。\n"
            "**第一趟挑出的毛病是真的，对应视角的分要压下来。**\n\n"
            "只输出 JSON：\n"
            '{"self": {"score": 0 到 100, "reason": "一句话"}, '
            '"others": {"score": 0 到 100, "reason": "一句话"}, '
            '"reader": {"score": 0 到 100, "reason": "一句话"}, '
            '"violations": ["不符合人设的具体点，要引用人设原文里的说法"], '
            '"state": "这一段结束后，这个人处在什么状态（一句话，写变化：伤/在哪/心境）"}',
            system="你只输出 JSON，不聊天。三个视角分开评，不要互相带偏。",
        )
        data = parse_json_block(raw)

        views: dict[str, dict] = {}
        for key in self.VIEW_WEIGHTS:
            v = data.get(key) or {}
            views[key] = {
                "score": max(SCORE_MIN, min(SCORE_MAX, int(v.get("score", 0)))),
                "reason": str(v.get("reason") or ""),
            }

        score = round(
            sum(views[k]["score"] * w for k, w in self.VIEW_WEIGHTS.items())
        )
        violations = [
            str(v).strip()
            for v in (data.get("violations") or [])
            if str(v).strip()
        ]

        passed = score >= threshold
        worst = min(views, key=lambda k: views[k]["score"])

        return {
            "name": name,
            "score": score,
            "views": views,
            "findings": findings,
            "threshold": threshold,
            "passed": passed,
            "violations": violations,
            "reason": f"最低是 {worst}（{views[worst]['score']}）：{views[worst]['reason']}",
            # 「过没过」和「有没有可改的地方」是两件事：
            # 分数过了阈值也可能有具体失分项，那是白送的改进线索，不能吞掉。
            "fix_hint": (
                "；".join(violations)
                if violations
                else (
                    "；".join(findings)
                    if findings
                    else ("" if passed else f"最低是 {worst} 视角：{views[worst]['reason']}")
                )
            ),
            "chapter": chapter,
            # 下一章要用的状态。模型没给就留空，编排层不会拿空串去推进。
            "state_update": str(data.get("state") or "").strip(),
        }

    def advance_state(
        self, book_id: str, name: str, chapter: int, state: str
    ) -> dict:
        """记下「这个人到第 chapter 章末是什么状态」。

        两个地方一起写：
        - character_states：逐章历史，写手按章取它之前最近的一条
        - characters：同步成最新，老读法（直接读 state）不会坏

        只写自己的库（char.db），符合「三个裁判各管各库」。
        """
        text = (state or "").strip()
        if not text:
            raise ValueError("state 不能空：没发生什么就别推进，别拿空串糊过去")
        ch = int(chapter)
        conn = self.connect()
        try:
            conn.execute(
                "INSERT INTO character_states(book_id, name, chapter, state, created_at)"
                " VALUES(?,?,?,?,?)"
                " ON CONFLICT(book_id, name, chapter) DO UPDATE SET state = excluded.state",
                (
                    book_id,
                    name,
                    ch,
                    text,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.execute(
                "UPDATE characters SET state = ?, as_of_chapter = ?"
                " WHERE book_id = ? AND name = ?",
                (text, ch, book_id, name),
            )
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "name": name, "chapter": ch, "state": text}

    def _critique(self, text: str) -> list[str]:
        """第一趟：只挑毛病，不评分。三个自问固定，缺一件不算挑完。"""
        raw = self.llm.complete(
            "只挑毛病，不要评分，不要夸。这段描写有什么问题？\n\n"
            f"{text}\n\n"
            "逐个问自己，答不上来就是问题：\n"
            "① 为什么要写这一笔？删掉会怎样？\n"
            "② 它跟别的内容有因果关系吗，还是孤零零掉下来的一块？\n"
            "③ 读起来顺不顺，有没有卡住、绕、假的地方？\n\n"
            '只输出 JSON：{"findings": ["具体的一条毛病", "..."]}',
            system="你只输出 JSON，只挑刺，不评分，不安慰。",
        )
        data = parse_json_block(raw)
        return [
            str(f).strip()
            for f in (data.get("findings") or [])
            if str(f).strip()
        ]

    # ---------- 内部 ----------

    def _insert(
        self,
        book_id: str,
        name: str,
        importance: str,
        profile: str,
        state: str | None,
        as_of_chapter: int | None,
    ) -> None:
        conn = db.connect("char.db")
        try:
            conn.execute(
                "INSERT INTO characters"
                "(book_id, name, importance, profile, state, as_of_chapter, created_at)"
                " VALUES(?,?,?,?,?,?,?)"
                # 同名重导 = 改档，不是报错。只改档案两列，
                # state / as_of_chapter 留着 —— 改人设不该把人写到的进度抹掉。
                " ON CONFLICT(book_id, name) DO UPDATE SET"
                "   importance = excluded.importance,"
                "   profile    = excluded.profile",
                (
                    book_id,
                    name,
                    importance,
                    profile,
                    state,
                    as_of_chapter,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"char.db 里已经有 {name!r} 了（UNIQUE(book_id, name)）"
            ) from exc
        finally:
            conn.close()

    def _names(self, book_id: str) -> list[str]:
        conn = db.connect("char.db")
        try:
            rows = conn.execute(
                "SELECT name FROM characters WHERE book_id = ? ORDER BY id",
                (book_id,),
            ).fetchall()
        finally:
            conn.close()
        return [r["name"] for r in rows]

    def _profile(self, book_id: str, name: str) -> str:
        conn = db.connect("char.db")
        try:
            row = conn.execute(
                "SELECT profile FROM characters WHERE book_id = ? AND name = ?",
                (book_id, name),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise LookupError(f"char.db 里没有角色 {name!r}")
        return str(row["profile"])

    def _threshold(self, book_id: str) -> int:
        """阈值从库里读。没设过就用 60，但那是兜底不是规定。"""
        conn = db.connect("char.db")
        try:
            row = conn.execute(
                "SELECT threshold FROM score_rules WHERE book_id = ?",
                (book_id,),
            ).fetchone()
        finally:
            conn.close()
        return int(row["threshold"]) if row else 60


def bind(registry, llm=None, book_id: str = "demo") -> None:
    # book_id 必须传进来，否则永远管的是 demo 那本书的人物。
    agent = CharacterControl(book_id=book_id, llm=llm)
    registry.register("random_character", agent.random_character)
    registry.register("import_character", agent.import_character)
    registry.register("score_in_character", agent.score_in_character)
    registry.register("advance_state", agent.advance_state)
    registry.register("remove_character", agent.remove_character)
