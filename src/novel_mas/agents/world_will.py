"""世界意志 agent —— 管「这世界能不能这样」。

隔离：独占 world.db，只读世界圣经 + 事件摘要，不读正文全文。
工具：5 个。写手拿不到其中任何一个。

实现状态：接口定稿，除查环外均为 stub（等 demo）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..db import base as db
from ..llm import Provider, parse_json_block
from ..types import Event, EventKind, Judgment, LinkKind, Verdict
from . import BaseAgent


class WorldWill(BaseAgent):
    role = "world_will"
    db_name = "world.db"

    def __init__(self, book_id: str = "demo", llm: Provider | None = None) -> None:
        # 一个世界意志实例管一本书。约束要按书取，所以书号挂在实例上。
        self.book_id = book_id
        # 判一致性要语义理解，必须有模型。没接就是 None，调工具时才报错。
        self.llm = llm

    # ---------- 工具 1：合理性判断 ----------

    def check_consistency(self, claim: str, chapter: int | None = None) -> Judgment:
        """判断一段剧情事件是否依据世界观 —— 交给模型做语义推理。

        「他合上了眼，再没醒来」这种句子，关键词匹配看不出来，模型看得出来。
        所以这里不留规则版兜底：没模型就明说判不了，不假装判过了。
        """
        if self.llm is None:
            raise RuntimeError(
                "世界意志没接模型，判不了语义。要先 WorldWill(llm=OpenAIProvider.from_env())"
            )

        rules = self._world_rules()
        hard = self._load_hard_constraints()

        prompt = (
            "你是这个小说世界的守门人。只判断一件事：下面这段剧情，"
            "有没有违背这个世界已经定下的规则。\n\n"
            "【世界规则】\n"
            + ("\n".join(f"- {text}" for _rid, text in rules) if rules else "- （暂无）")
            + "\n\n【硬约束 · 绝对不能违背】\n"
            + ("\n".join(f"- {h}" for h in hard) if hard else "- （暂无）")
            + f"\n\n【待判剧情】\n{claim}\n\n"
            "只输出一个 JSON 对象，不要任何解释文字：\n"
            '{"verdict": "pass 或 soft_warn 或 hard_conflict", '
            '"reason": "一句话说清为什么", '
            '"violated": "被违背的那条原文，没有就空字符串"}'
        )

        raw = self.llm.complete(prompt, system="你只输出 JSON，不聊天，不解释。")
        data = parse_json_block(raw)

        raw_verdict = str(data.get("verdict", "")).strip().lower()
        verdict = {
            "pass": Verdict.PASS,
            "soft_warn": Verdict.SOFT_WARN,
            "hard_conflict": Verdict.HARD_CONFLICT,
        }.get(raw_verdict, Verdict.SOFT_WARN)   # 认不出就降级成软警告，别当没事

        return Judgment(
            verdict=verdict,
            claim=claim,
            reason=str(data.get("reason") or raw)[:200],
            chapter=chapter,
        )

    def _world_rules(self) -> list[tuple[int, str]]:
        """(id, "标题：正文") 列表，去重（库里有重复插的历史）。

        带 id 是为了让随机事件的 basis 能追到「凭的是第几条」。
        """
        conn = db.connect("world.db")
        try:
            rows = conn.execute(
                "SELECT id, title, body FROM world_bible WHERE book_id = ? ORDER BY id",
                (self.book_id,),
            ).fetchall()
        finally:
            conn.close()

        seen: set[tuple] = set()
        out: list[tuple[int, str]] = []
        for r in rows:
            key = (r["title"], r["body"])
            if key in seen:
                continue
            seen.add(key)
            out.append((r["id"], f"{r['title']}：{r['body']}"))
        return out

    def _find_same_claim(self, claim: str) -> int | None:
        """库里有没有一字不差的同一条事件。只看 summary 完全相同。"""
        conn = db.connect("world.db")
        try:
            row = conn.execute(
                "SELECT id FROM events WHERE book_id = ? AND summary = ? LIMIT 1",
                (self.book_id, claim),
            ).fetchone()
        finally:
            conn.close()
        return int(row["id"]) if row else None

    def _load_hard_constraints(self) -> list[str]:
        """收集所有硬约束，两个来源都要。

        1. trend.db 的 ending_intent.hard_constraints —— 结局意图里的红线
        2. world.db 的 events.constraints —— 大事件锁死的可能性

        第 2 条是 2026-11-18 补的：之前只读第 1 条，导致 adopt 生成的事件约束
        「记了但判的时候不看」。记了不用，等于没记。
        """
        out: list[str] = []

        # 1) 结局意图
        conn = db.connect("trend.db")
        try:
            row = conn.execute(
                "SELECT hard_constraints FROM ending_intent WHERE book_id = ?",
                (self.book_id,),
            ).fetchone()
        finally:
            conn.close()
        if row and row["hard_constraints"]:
            out.extend(json.loads(row["hard_constraints"]))

        # 2) 已记大事件锁死的东西
        conn = db.connect("world.db")
        try:
            rows = conn.execute(
                "SELECT constraints FROM events "
                "WHERE book_id = ? AND kind = 'major'",
                (self.book_id,),
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            if r["constraints"]:
                out.extend(json.loads(r["constraints"]))

        return out

    # ---------- 工具 2：大事件记录 ----------

    def record_event(self, ev: Event) -> int:
        """记大事件。必须带 constraints —— 它锁死了什么。"""
        if ev.kind is not EventKind.MAJOR:
            raise ValueError("这是 record_event，kind 必须是 major")
        if not ev.constraints:
            raise ValueError("大事件必须带 constraints；没有约束的大事件等于没记")

        conn = db.connect("world.db")
        try:
            cur = conn.execute(
                "INSERT INTO events"
                "(book_id, kind, title, summary, chapter, constraints,"
                " caused_by, source, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    ev.book_id,
                    ev.kind.value,
                    ev.title,
                    ev.summary,
                    ev.chapter,
                    json.dumps(ev.constraints, ensure_ascii=False),   # 大事件必带
                    json.dumps(ev.caused_by, ensure_ascii=False),
                    "world_will",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    # ---------- 工具 3：小事件记录 ----------

    def record_minor_event(self, ev: Event, dedupe: bool = True) -> int:
        """记小事件。只留痕，不加约束。

        dedupe=True 时，同一个 title + chapter 已经记过就先删旧的再写新的。
        重写同一章不该在历史里堆两条 —— 实测跑三次就堆了三份同样的摘要。
        """
        if ev.kind is not EventKind.MINOR:
            raise ValueError("这是 record_minor_event，kind 必须是 minor")

        conn = db.connect("world.db")
        try:
            if dedupe and ev.chapter is not None:
                # chapter IS ? 而不是 = ?：要能匹配 NULL
                conn.execute(
                    "DELETE FROM events"
                    " WHERE book_id = ? AND kind = 'minor'"
                    "   AND title = ? AND chapter IS ?",
                    (ev.book_id, ev.title, ev.chapter),
                )

            cur = conn.execute(
                "INSERT INTO events"
                "(book_id, kind, title, summary, chapter, constraints,"
                " caused_by, source, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    ev.book_id,
                    ev.kind.value,          # 枚举 → 字符串，不能直接存
                    ev.title,
                    ev.summary,
                    ev.chapter,
                    None,                   # 小事件不加约束
                    json.dumps(ev.caused_by, ensure_ascii=False),
                    "world_will",           # 谁写的库，不是谁要求的
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()                   # 不 commit，关连接就丢了
            return int(cur.lastrowid)
        finally:
            conn.close()

    # ---------- 工具 4：事件链接 ----------

    def link_events(self, from_id: int, to_id: int, kind: LinkKind) -> int:
        """把两个事件连起来。写入前必须查环。"""
        # 关键：要把已有的边喂进去，不然 would_create_cycle 只能查 from == to
        if self.would_create_cycle(from_id, to_id, self._load_edges()):
            raise ValueError(
                f"拒绝写入：{from_id} → {to_id} 会形成环，判断时会无限递归"
            )

        conn = db.connect("world.db")
        try:
            cur = conn.execute(
                "INSERT INTO links"
                "(book_id, from_event, to_event, kind, source, adopted, created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (
                    self.book_id,
                    from_id,
                    to_id,
                    kind.value,
                    "world_will",
                    1,          # 世界意志自己写的，直接算入库
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def _load_edges(self) -> dict[int, set[int]]:
        """把 links 表读成邻接表 {from: {to}}，给查环用。"""
        conn = db.connect("world.db")
        try:
            rows = conn.execute(
                "SELECT from_event, to_event FROM links WHERE book_id = ?",
                (self.book_id,),
            ).fetchall()
        finally:
            conn.close()

        edges: dict[int, set[int]] = {}
        for r in rows:
            edges.setdefault(r["from_event"], set()).add(r["to_event"])
        return edges

    @staticmethod
    def would_create_cycle(
        from_id: int, to_id: int, edges: dict[int, set[int]] | None = None
    ) -> bool:
        """纯逻辑：这条边会不会造成环。

        已真实现（不依赖 db），可直接测。
        """
        if from_id == to_id:
            return True
        if not edges:
            return False
        # 从 to_id 出发能不能走回 from_id
        seen: set[int] = set()
        stack = [to_id]
        while stack:
            cur = stack.pop()
            if cur == from_id:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(edges.get(cur, ()))
        return False

    # ---------- 工具 5：随机事件 ----------

    # ---------- 工具 6：手动注入事件 ----------

    def inject_event(
        self,
        claim: str,
        title: str = "",
        chapter: int | None = None,
        force: bool = False,
    ) -> dict:
        """手动注入一条事件：**先过模型判定，通过了才写进 events**。

        这是「人想让它发生」的入口。判定沿用 check_consistency，
        跟写手写正文走的是同一把尺——注入不享受豁免。

        force=False 时会拦「一字不差地记过」的重复：
        同一条话注入两次，多半是手滑，不是真的要它发生两遍。
        真要记两次，传 force=True —— 不静默吞，也不硬写。

        返回 dict 而不是 Event：这里要同时带回裁决，让调用方知道有没有过。
        """
        if not force:
            dup_id = self._find_same_claim(claim)
            if dup_id is not None:
                return {
                    "accepted": False,
                    "verdict": "duplicate",
                    "reason": (
                        f"这条已经记过了（event_id={dup_id}）。"
                        "真要再记一次，传 force=True"
                    ),
                    "event_id": dup_id,
                }

        judgment = self.check_consistency(claim, chapter=chapter)

        if judgment.verdict is not Verdict.PASS:
            return {
                "accepted": False,
                "verdict": judgment.verdict.value,
                "reason": judgment.reason,
            }

        # 通过：按大事件记，约束让模型给（跟 adopt 随机事件同一个道理）
        constraints = self._infer_constraints(title or claim[:20], claim, "")
        ev_id = self.record_event(
            Event(
                book_id=self.book_id,
                kind=EventKind.MAJOR,
                title=title or claim[:20],
                summary=claim,
                constraints=constraints,
                chapter=chapter,
            )
        )
        return {
            "accepted": True,
            "event_id": ev_id,
            "verdict": "pass",
            "constraints": constraints,
        }

    def manage_random(self, action: str, **kwargs: Any) -> Any:
        """灾/缘的池子管理。写手无权调用。

        **全是候选制**：系统只提供选项，人挑用，不替人选。
        offer  —— 人造一个灾/缘候选（写进 random_pool，state=candidate）
        adopt  —— 人挑中某条，转成正式大事件
        cool   —— 暂时别用（state=cooling），人不解冻就一直挂着
        reject —— 作废（state=rejected），记录留着
        """
        if action not in {"offer", "adopt", "cool", "reject"}:
            raise ValueError(f"未知动作: {action}")

        if action == "offer":
            return self._random_offer(
                kind=str(kwargs["kind"]),
                target=str(kwargs.get("target", "")),
                hint=str(kwargs.get("hint", "")),
            )
        if action == "adopt":
            return self._random_adopt(int(kwargs["pool_id"]), kwargs.get("constraints"))
        return self._random_set_state(
            int(kwargs["pool_id"]), "cooling" if action == "cool" else "rejected"
        )

    def _random_offer(self, kind: str, target: str = "", hint: str = "") -> int:
        """让模型按世界观造一个候选。人要才跑，不会自己冒出来。"""
        if self.llm is None:
            raise RuntimeError("造随机事件要模型：先给 WorldWill(llm=...)")
        if kind not in {"disaster", "fortune"}:
            raise ValueError(f"kind 只能是 disaster / fortune，收到 {kind!r}")

        rules = self._world_rules()
        prompt = (
            "你是这个小说世界的守门人。按下面的世界规则，造一个"
            + ("灾" if kind == "disaster" else "缘")
            + "的候选。它只是备选，还没发生。\n\n【世界规则】\n"
            + ("\n".join(f"{rid}. {text}" for rid, text in rules) if rules else "（暂无）")
            + (f"\n\n【主题倾向】{hint}" if hint else "")
            + (f"\n【针对对象】{target}" if target else "")
            + "\n\n只输出一个 JSON：\n"
            '{"title": "一句话标题", "body": "两三句写清会发生什么", '
            '"target": "落到谁 / 哪里", "basis": [依据的世界规则编号，整数数组]}'
        )
        raw = self.llm.complete(prompt, system="你只输出 JSON，不聊天，不解释。")
        data = parse_json_block(raw)

        basis: list[int] = []
        for b in data.get("basis") or []:
            try:
                basis.append(int(b))
            except (TypeError, ValueError):
                continue

        conn = db.connect("world.db")
        try:
            cur = conn.execute(
                "INSERT INTO random_pool"
                "(book_id, kind, target, title, body, basis, state,"
                " cooldown_until, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    self.book_id,
                    kind,
                    str(data.get("target") or target),
                    str(data.get("title") or "（无题）"),
                    str(data.get("body") or ""),
                    json.dumps(basis, ensure_ascii=False),
                    "candidate",
                    None,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def _random_adopt(self, pool_id: int, constraints: list[str] | None = None) -> int:
        """把候选转成正式大事件。

        「锁死什么」是采用那一刻才生效的，所以不在候选阶段定：
        调用方给了就用给的；没给就让模型按这条候选生成。
        """
        conn = db.connect("world.db")
        try:
            row = conn.execute(
                "SELECT state, target, title, body FROM random_pool "
                "WHERE id = ? AND book_id = ?",
                (pool_id, self.book_id),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise LookupError(f"池子里没有 id={pool_id} 的候选")
        if row["state"] != "candidate":
            raise ValueError(
                f"候选 {pool_id} 的状态是 {row['state']!r}，只有 candidate 能采用"
            )

        if constraints is None:
            if self.llm is None:
                raise RuntimeError("没给 constraints 又没模型，没法决定它锁死什么")
            constraints = self._infer_constraints(
                row["title"], row["body"], row["target"] or ""
            )

        ev_id = self.record_event(
            Event(
                book_id=self.book_id,
                kind=EventKind.MAJOR,
                title=row["title"],
                summary=row["body"],
                constraints=list(constraints),
            )
        )
        self._random_set_state(pool_id, "adopted")
        return ev_id

    def _random_set_state(self, pool_id: int, state: str) -> int:
        conn = db.connect("world.db")
        try:
            cur = conn.execute(
                "UPDATE random_pool SET state = ? WHERE id = ? AND book_id = ?",
                (state, pool_id, self.book_id),
            )
            conn.commit()
        finally:
            conn.close()
        if cur.rowcount == 0:
            raise LookupError(f"池子里没有 id={pool_id} 的候选")
        return pool_id

    def _infer_constraints(self, title: str, body: str, target: str) -> list[str]:
        """这条事件写进小说，会锁死哪些后续可能性。"""
        prompt = (
            "这个事件如果写进小说，会锁死哪些后续可能性？\n"
            f"标题：{title}\n正文：{body}\n对象：{target or '（不限）'}\n\n"
            '只输出 JSON：{"constraints": ["...", "..."]}，'
            "一到三条，每条一句短话，要具体（谁不能再做什么）。"
        )
        raw = self.llm.complete(prompt, system="你只输出 JSON，不聊天。")
        data = parse_json_block(raw)
        return [
            str(c).strip()
            for c in (data.get("constraints") or [])
            if str(c).strip()
        ]


    def set_worldview(
        self, book_id: str, title: str, body: str, category: str = "规则"
    ) -> dict:
        """单条世界观：有则改，无则加。

        为什么要有这个：整段重灌只能按「条目1/条目2」覆盖，想改其中一条
        的措辞就得整段重写，风险大还容易改错行。
        """
        title, body = (title or "").strip(), (body or "").strip()
        if not title or not body:
            raise ValueError("标题和正文都不能空")
        conn = db.connect("world.db")
        try:
            conn.execute(
                "INSERT INTO world_bible"
                "(book_id, category, title, body, version, source, created_at)"
                " VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(book_id, title) DO UPDATE SET"
                "   body = excluded.body,"
                "   category = excluded.category,"
                "   version = world_bible.version + 1",
                (
                    book_id,
                    category or "规则",
                    title,
                    body,
                    1,
                    "user_inject",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "title": title, "body": body}

    def remove_worldview(self, book_id: str, title: str) -> dict:
        """删掉一条世界观。标题对不上就报错，不猜。"""
        title = (title or "").strip()
        if not title:
            raise ValueError("要删哪一条？标题不能空")
        conn = db.connect("world.db")
        try:
            cur = conn.execute(
                "DELETE FROM world_bible WHERE book_id = ? AND title = ?",
                (book_id, title),
            )
            if cur.rowcount == 0:
                raise LookupError(f"{book_id} 里没有标题「{title}」的世界观条目")
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "title": title}


# 供 registry 注册用
def bind(registry, llm=None, book_id: str = "demo") -> None:
    # book_id 必须传进来：不然这个 agent 永远读 demo 那本书的约束，
    # 换一本书就跑成别人的世界观了。
    agent = WorldWill(book_id=book_id, llm=llm)
    registry.register("check_consistency", agent.check_consistency)
    registry.register("record_event", agent.record_event)
    registry.register("record_minor_event", agent.record_minor_event)
    registry.register("link_events", agent.link_events)
    registry.register("manage_random", agent.manage_random)
    registry.register("inject_event", agent.inject_event)
    registry.register("set_worldview", agent.set_worldview)
    registry.register("remove_worldview", agent.remove_worldview)
