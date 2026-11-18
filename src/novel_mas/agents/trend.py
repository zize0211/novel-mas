"""世界走向 agent —— 管「这故事该不该走到这儿」。

分值方向：高 = 感人/温馨/日常/阳光（happy end）；低 = 分离/死亡/堕落（bad end）。
注意这**不是质量分**，是情绪方向分。

隔离：独占 trend.db。
工具：3 个。

实现状态：接口定稿；曲线校验（max_slope）已真实现，其余 stub。
"""

from __future__ import annotations

import json
from datetime import datetime

from ..db import base as db
from ..llm import Provider, parse_json_block
from ..types import (
    MAX_SLOPE_DEFAULT,
    SCORE_MAX,
    SCORE_MIN,
    TOLERANCE_DEFAULT,
    CurvePoint,
    DirectionScore,
    EndingType,
    TrendVerdict,
)
from . import BaseAgent


class WorldTrend(BaseAgent):
    role = "trend"
    db_name = "trend.db"

    def __init__(self, book_id: str = "demo", llm: Provider | None = None) -> None:
        self.book_id = book_id
        self.llm = llm

    # ---------- 工具 1：剧情设定（结局意图） ----------

    def set_ending_intent(self, book_id: str, prompt: str) -> dict:
        """用户注入 prompt，抽出结局类型 + 目标分 + 硬约束。

        抽不出硬约束时要 needs_review=True，不许硬编。
        """
        if self.llm is None:
            raise RuntimeError("抽结局意图要模型：先给 WorldTrend(llm=...)")

        raw = self.llm.complete(
            "从下面这段话里抽出这个故事的结局意图。\n\n"
            f"【作者的话】\n{prompt}\n\n"
            "只输出一个 JSON：\n"
            '{"ending_type": "happy 或 bittersweet 或 bad", '
            '"target_score": 0 到 100 的整数（高=温暖光明，低=黑暗决绝）, '
            '"hard_constraints": ["绝对不能发生的事，每条一句"], '
            '"soft_preferences": ["希望倾向但不强求的"]}\n'
            "抽不出某一项就给空数组。不要编——没有硬约束就是没有。",
            system="你只输出 JSON，不聊天，不解释。",
        )
        data = parse_json_block(raw)

        # ending_type 必须落在合法值里。认不出就报错，不猜一个塞进去
        try:
            ending = EndingType(str(data.get("ending_type", "")).strip().lower())
        except ValueError:
            raise ValueError(
                f"结局类型认不出：{data.get('ending_type')!r}，"
                "只能是 happy / bittersweet / bad"
            )

        target = int(data.get("target_score", 50))
        if not 0 <= target <= 100:
            raise ValueError(f"target_score 必须在 0..100，收到 {target}")

        hard = [
            str(x).strip()
            for x in (data.get("hard_constraints") or [])
            if str(x).strip()
        ]
        soft = [
            str(x).strip()
            for x in (data.get("soft_preferences") or [])
            if str(x).strip()
        ]

        conn = db.connect("trend.db")
        try:
            # book_id 是主键，一书记一行：重设就覆盖，不堆历史
            conn.execute(
                "INSERT INTO ending_intent"
                "(book_id, ending_type, target_score, hard_constraints,"
                " soft_preferences, created_at)"
                " VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(book_id) DO UPDATE SET"
                "   ending_type      = excluded.ending_type,"
                "   target_score     = excluded.target_score,"
                "   hard_constraints = excluded.hard_constraints,"
                "   soft_preferences = excluded.soft_preferences,"
                "   created_at       = excluded.created_at",
                (
                    book_id,
                    ending.value,
                    target,
                    json.dumps(hard, ensure_ascii=False),
                    json.dumps(soft, ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "ending_type": ending.value,
            "target_score": target,
            "hard_constraints": hard,
            "soft_preferences": soft,
            # 一条硬约束都没抽出来，就得人来补 —— 没有约束的守门人等于没牙
            "needs_review": not hard,
        }

    # ---------- 工具 2：剧情走向设定（曲线） ----------

    def set_curve(
        self,
        book_id: str,
        points: list[CurvePoint] | None = None,
        prompt: str | None = None,
    ) -> dict:
        """设定逐章目标分 + 容忍带，并做坡度检查。"""
        if not points:
            if not prompt:
                raise ValueError("points 和 prompt 至少要给一个")
            if self.llm is None:
                raise RuntimeError("从 prompt 生成曲线要模型：先给 WorldTrend(llm=...)")
            points = self._curve_from_prompt(prompt)

        warnings = self.validate_curve(points)

        conn = db.connect("trend.db")
        try:
            # UNIQUE(book_id, chapter)：先清旧的，再整体写新的，不留半截
            conn.execute("DELETE FROM curve WHERE book_id = ?", (book_id,))
            now = datetime.now().isoformat(timespec="seconds")
            conn.executemany(
                "INSERT INTO curve(book_id, chapter, target, tolerance, created_at)"
                " VALUES(?,?,?,?,?)",
                [(book_id, p.chapter, p.target, p.tolerance, now) for p in points],
            )
            conn.commit()
        finally:
            conn.close()

        return {"curve": points, "warnings": warnings}

    def _curve_from_prompt(self, prompt: str, chapters: int = 20) -> list[CurvePoint]:
        """让模型把一段人话排成逐章目标分。

        包一层 {"points": [...]}：parse_json_block 只认对象，
        直接让模型输出裸数组会抠不出来。
        """
        raw = self.llm.complete(
            f"按下面这段话，给这个故事排一条 {chapters} 章的「情绪方向曲线」。\n\n"
            f"【作者的话】\n{prompt}\n\n"
            "分值：高 = 温暖光明（happy end），低 = 黑暗决绝（bad end），范围 0..100。\n"
            "这是情绪方向，不是质量分。\n"
            f"只输出一个 JSON，数组长度正好 {chapters} 项：\n"
            '{"points": [{"chapter": 1, "target": 50, "tolerance": 15}, ...]}',
            system="你只输出 JSON，不聊天。",
        )
        data = parse_json_block(raw)

        raw_points = data.get("points") or []
        if not isinstance(raw_points, list) or not raw_points:
            raise ValueError(f"模型没给 points 数组：{raw[:200]}")

        out: list[CurvePoint] = []
        for i, p in enumerate(raw_points, 1):
            if not isinstance(p, dict):
                continue
            target = int(p.get("target", 50))
            out.append(
                CurvePoint(
                    chapter=int(p.get("chapter", i)),
                    target=max(0, min(100, target)),      # 夹紧，别让模型给 120
                    tolerance=int(p.get("tolerance", 15)),
                )
            )
        return out

    @staticmethod
    def validate_curve(
        points: list[CurvePoint], max_slope: int = MAX_SLOPE_DEFAULT
    ) -> list[str]:
        """纯逻辑：检查曲线有没有断崖。已真实现，可直接测。

        一章从 90 掉到 15 不叫转折，叫失忆。
        """
        warnings: list[str] = []
        ordered = sorted(points, key=lambda p: p.chapter)
        for prev, cur in zip(ordered, ordered[1:]):
            delta = abs(cur.target - prev.target)
            if delta > max_slope:
                warnings.append(
                    f"第 {prev.chapter}→{cur.chapter} 章单章落差 {delta}，"
                    f"超过上限 {max_slope}，建议拆成两章"
                )
        for p in ordered:
            if not 0 <= p.target <= 100:
                warnings.append(f"第 {p.chapter} 章目标分 {p.target} 越界（0–100）")
        return warnings

    # ---------- 工具 3：剧情走向打分 ----------

    # 事件类型 → 方向权重。写在这里，因为这是「口径」不是「内容」：
    # 判的是「发生了哪一类事」，不是「写得好不好」。基础分 50。
    EVENT_WEIGHTS: dict[str, int] = {
        "death": -30,
        "departure": -15,
        "betrayal": -12,
        "loss": -10,
        "injury": -5,
        "conflict": -3,
        "daily": 0,
        "kindness": 5,
        "rescue": 10,
        "reunion": 12,
        "reconciliation": 5,
    }

    # 每个类型的一句解释。**不给解释模型就会自己猜** ——
    # 实测过：它把「推开木门走进去」判成了 departure（永别），
    # 因为 departure 字面上也像「离开当前位置」。类型名有歧义，得钉死。
    EVENT_DESC: dict[str, str] = {
        "death": "有人死了，或明确暗示死亡（如『再没醒来』）",
        "departure": "永别 —— 人走了不再回来。不是出门、不是离开房间、不是走进某处",
        "betrayal": "背叛、出卖",
        "loss": "失去重要之物（人还在，东西没了）",
        "injury": "受伤、病倒",
        "conflict": "冲突、争吵、翻脸",
        "daily": "日常，不带情绪方向",
        "kindness": "善意、照顾",
        "rescue": "被救 / 救下 / 主动去救",
        "reunion": "重聚、久别重逢",
        "reconciliation": "和解、冰释",
    }
    BASE_SCORE = 50

    def score_direction(
        self,
        book_id: str,
        text: str,
        chapter: int | None = None,
        mode: str = "chapter",
    ) -> DirectionScore:
        """给本章 / 终局打走向分。

        判定规则（已定）：
        - on_track ：|deviation| <= tolerance 且没撞硬约束
        - drifted  ：超出容忍带，但方向不离谱
        - violated ：撞了结局硬约束（例如设定"主角不能死"，本章主角死了）

        **打分走两步**：模型只负责「抽出了哪些事」，分数由 EVENT_WEIGHTS
        在 Python 里算。这样每个分都能指向具体事件，而不是模型随口给个数。
        """
        if mode not in {"chapter", "final"}:
            raise ValueError(f"未知 mode: {mode}")
        if self.llm is None:
            raise RuntimeError("抽事件要模型：先给 WorldTrend(llm=...)")

        target, tolerance = self._load_target(book_id, chapter, mode)
        events = self._extract_events(text)
        score = self._score_from_events(events)
        violated = self._check_violated(book_id, events)

        verdict = self.judge(score, target, tolerance, violated)

        return DirectionScore(
            score=score,
            verdict=TrendVerdict(verdict),
            target=target,
            deviation=score - target,
            evidence=events,
            violated_constraints=violated,
            fix_hint=self._fix_hint(score, target, events),
        )

    def _load_target(
        self, book_id: str, chapter: int | None, mode: str
    ) -> tuple[int, int]:
        """目标分从库里取，不写死。取不到就退回中位 50。"""
        if mode == "final":
            conn = db.connect("trend.db")
            try:
                row = conn.execute(
                    "SELECT target_score FROM ending_intent WHERE book_id = ?",
                    (book_id,),
                ).fetchone()
            finally:
                conn.close()
            target = int(row["target_score"]) if row else self.BASE_SCORE
            return target, TOLERANCE_DEFAULT

        if chapter is None:
            raise ValueError("chapter 模式下必须给 chapter")
        conn = db.connect("trend.db")
        try:
            row = conn.execute(
                "SELECT target, tolerance FROM curve WHERE book_id = ? AND chapter = ?",
                (book_id, chapter),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return self.BASE_SCORE, TOLERANCE_DEFAULT
        return int(row["target"]), int(row["tolerance"])

    def _extract_events(self, text: str) -> list[dict]:
        """让模型只抽事件类型，不给分。"""
        kinds = "\n".join(
            f"- {k}（{w:+d}）：{self.EVENT_DESC.get(k, '')}"
            for k, w in self.EVENT_WEIGHTS.items()
        )
        raw = self.llm.complete(
            "读完这段小说正文，把里面**改变情绪方向**的事抽出来。\n\n"
            f"{text}\n\n"
            "只认下面这些类型（照抄英文原词，不要自己造新类型）：\n"
            f"{kinds}\n\n"
            "拿不准归 daily，不要硬套。\n"
            "只输出 JSON：\n"
            '{"events": [{"who": "谁", "kind": "上面某个类型", "what": "一句话说清"}]}\n'
            "日常闲聊、景物描写、纯心理活动不算事件。没有就输出空数组。",
            system="你只输出 JSON，不聊天，不评价写得好坏。",
        )
        data = parse_json_block(raw)

        out: list[dict] = []
        for e in data.get("events") or []:
            if not isinstance(e, dict):
                continue
            kind = str(e.get("kind", "")).strip().lower()
            if kind not in self.EVENT_WEIGHTS:
                continue          # 认不出的类型直接丢，不瞎猜权重
            out.append(
                {
                    "who": str(e.get("who", "")),
                    "kind": kind,
                    "what": str(e.get("what", "")),
                    "weight": self.EVENT_WEIGHTS[kind],
                }
            )
        return out

    @classmethod
    def _score_from_events(cls, events: list[dict]) -> int:
        """纯算术：基础分 + 每个事件的权重，夹在 0..100。"""
        total = cls.BASE_SCORE + sum(int(e["weight"]) for e in events)
        return max(SCORE_MIN, min(SCORE_MAX, total))

    def _check_violated(self, book_id: str, events: list[dict]) -> list[str]:
        """便宜的近似：death 事件 + 硬约束里点名了这个 who，就算撞了。

        真正细的语义判定在世界意志的 check_consistency；这里是第二道，宁可粗。
        """
        deaths = [e for e in events if e["kind"] == "death"]
        if not deaths:
            return []

        conn = db.connect("trend.db")
        try:
            row = conn.execute(
                "SELECT hard_constraints FROM ending_intent WHERE book_id = ?",
                (book_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row or not row["hard_constraints"]:
            return []

        hard = json.loads(row["hard_constraints"])
        out: list[str] = []
        for d in deaths:
            who = d["who"]
            for h in hard:
                if who and who in h:
                    out.append(f"{h}（{who}：{d['what']}）")
        return out

    @staticmethod
    def _fix_hint(score: int, target: int, events: list[dict]) -> str:
        """偏了就给出往回走的方向，别只丢一个数字。"""
        if abs(score - target) <= TOLERANCE_DEFAULT:
            return ""
        diff = score - target
        if diff < 0:
            return f"比目标沉了 {abs(diff)} 分，加一点缓和的日常或善意"
        return f"比目标亮了 {diff} 分，加一点代价或阴影"

    @staticmethod
    def judge(score: int, target: int, tolerance: int, violated: list[str]) -> str:
        """纯逻辑：三分级判定。已真实现，可直接测。"""
        if violated:
            return "violated"
        if abs(score - target) <= tolerance:
            return "on_track"
        return "drifted"


def bind(registry, llm=None, book_id: str = "demo") -> None:
    # book_id 必须传进来，否则永远读的是 demo 那本书的曲线和结局意图。
    agent = WorldTrend(book_id=book_id, llm=llm)
    registry.register("set_ending_intent", agent.set_ending_intent)
    registry.register("set_curve", agent.set_curve)
    registry.register("score_direction", agent.score_direction)
