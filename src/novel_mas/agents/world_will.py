"""世界意志 agent —— 管「这世界能不能这样」。

隔离：独占 world.db，只读世界圣经 + 事件摘要，不读正文全文。
工具：5 个。写手拿不到其中任何一个。

实现状态：接口定稿，除查环外均为 stub（等 demo）。
"""

from __future__ import annotations

from typing import Any

from ..types import Event, EventKind, Judgment, LinkKind, Verdict
from . import BaseAgent


class WorldWill(BaseAgent):
    role = "world_will"
    db_name = "world.db"

    # ---------- 工具 1：合理性判断 ----------

    def check_consistency(self, claim: str, chapter: int | None = None) -> Judgment:
        """判断一段剧情事件是否依据世界观。

        做法（待实现）：把 claim 拆成"实体 + 行为 + 后果"，
        逐条比对 world_bible 与 events.constraints，产出三级裁决。
        """
        raise NotImplementedError("等 demo：比对逻辑未实现")

    # ---------- 工具 2：大事件记录 ----------

    def record_event(self, ev: Event) -> int:
        """记大事件。必须带 constraints —— 它锁死了什么。"""
        if ev.kind is EventKind.MAJOR and not ev.constraints:
            raise ValueError("大事件必须带 constraints；没有约束的大事件等于没记")
        raise NotImplementedError("等 demo：写库逻辑未实现")

    # ---------- 工具 3：小事件记录 ----------

    def record_minor_event(self, ev: Event) -> int:
        """记小事件。只留痕，不加约束。"""
        if ev.kind is not EventKind.MINOR:
            raise ValueError("这是 record_minor_event，kind 必须是 minor")
        raise NotImplementedError("等 demo：写库逻辑未实现")

    # ---------- 工具 4：事件链接 ----------

    def link_events(self, from_id: int, to_id: int, kind: LinkKind) -> int:
        """把两个事件连起来。写入前必须查环。"""
        if self.would_create_cycle(from_id, to_id):
            raise ValueError(
                f"拒绝写入：{from_id} → {to_id} 会形成环，判断时会无限递归"
            )
        raise NotImplementedError("等 demo：写库逻辑未实现")

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

    def manage_random(self, action: str, **kwargs: Any) -> Any:
        """灾/缘的池子管理。写手无权调用。"""
        if action not in {"offer", "adopt", "cool", "reject"}:
            raise ValueError(f"未知动作: {action}")
        raise NotImplementedError("等 demo：池子逻辑未实现")


# 供 registry 注册用
def bind(registry) -> None:
    agent = WorldWill()
    registry.register("check_consistency", agent.check_consistency)
    registry.register("record_event", agent.record_event)
    registry.register("record_minor_event", agent.record_minor_event)
    registry.register("link_events", agent.link_events)
    registry.register("manage_random", agent.manage_random)
