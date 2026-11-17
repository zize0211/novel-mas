"""世界走向 agent —— 管「这故事该不该走到这儿」。

分值方向：高 = 感人/温馨/日常/阳光（happy end）；低 = 分离/死亡/堕落（bad end）。
注意这**不是质量分**，是情绪方向分。

隔离：独占 trend.db。
工具：3 个。

实现状态：接口定稿；曲线校验（max_slope）已真实现，其余 stub。
"""

from __future__ import annotations

from ..types import (
    MAX_SLOPE_DEFAULT,
    CurvePoint,
    DirectionScore,
    EndingType,
)
from . import BaseAgent


class WorldTrend(BaseAgent):
    role = "trend"
    db_name = "trend.db"

    # ---------- 工具 1：剧情设定（结局意图） ----------

    def set_ending_intent(self, book_id: str, prompt: str) -> dict:
        """用户注入 prompt，抽出结局类型 + 目标分 + 硬约束。

        抽不出硬约束时要 needs_review=True，不许硬编。
        """
        raise NotImplementedError("等 demo：抽取逻辑未实现")

    # ---------- 工具 2：剧情走向设定（曲线） ----------

    def set_curve(
        self,
        book_id: str,
        points: list[CurvePoint] | None = None,
        prompt: str | None = None,
    ) -> dict:
        """设定逐章目标分 + 容忍带，并做坡度检查。"""
        if not points:
            raise NotImplementedError("等 demo：从自然语言 prompt 生成曲线的逻辑未实现")
        warnings = self.validate_curve(points)
        return {"curve": points, "warnings": warnings}

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

    def score_direction(
        self,
        book_id: str,
        text: str,
        chapter: int | None = None,
        mode: str = "chapter",
    ) -> DirectionScore:
        """给本章 / 终局打走向分。

        判定规则（已定，待实现）：
        - on_track ：|deviation| <= tolerance 且没撞硬约束
        - drifted  ：超出容忍带，但方向不离谱
        - violated ：撞了结局硬约束（例如设定"主角不能死"，本章主角死了）
        """
        if mode not in {"chapter", "final"}:
            raise ValueError(f"未知 mode: {mode}")
        raise NotImplementedError("等 demo：打分未实现")

    @staticmethod
    def judge(score: int, target: int, tolerance: int, violated: list[str]) -> str:
        """纯逻辑：三分级判定。已真实现，可直接测。"""
        if violated:
            return "violated"
        if abs(score - target) <= tolerance:
            return "on_track"
        return "drifted"


def bind(registry) -> None:
    agent = WorldTrend()
    registry.register("set_ending_intent", agent.set_ending_intent)
    registry.register("set_curve", agent.set_curve)
    registry.register("score_direction", agent.score_direction)
