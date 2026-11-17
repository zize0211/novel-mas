"""写作 agent —— 唯一的产出方。

它的工具**全是取**，没有一个是写：
    文风 / 曲线 / 人物 / 世界观 / 小事链接（只提交候选）

写手能改的只有正文。想动设定、人设、走向，都得走单向工具去求。

实现状态：接口定稿，全部 stub（等 demo）。
"""

from __future__ import annotations

from ..types import CharacterSnapshot, CurvePoint
from . import BaseAgent


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
        raise NotImplementedError("等 demo：文风解析未实现")

    # ---------- 工具 2：拿大纲曲线 ----------

    def get_curve(self, book_id: str, chapter: int) -> CurvePoint:
        """只取本章的目标分与容忍带，不是全书。"""
        raise NotImplementedError("等 demo：读 trend.db 未实现")

    # ---------- 工具 3：人物信息获取 ----------

    def get_characters(
        self, book_id: str, names: list[str] | None = None
    ) -> list[CharacterSnapshot]:
        """拿人物档的只读快照。

        必须带 as_of_chapter —— 否则会拿第 3 章的旧状态写第 12 章。
        """
        raise NotImplementedError("等 demo：读 char.db 未实现")

    # ---------- 工具 4：世界观获取 ----------

    def get_worldview(
        self, book_id: str, topics: list[str] | None = None
    ) -> dict:
        """只读世界观条目 + 本章硬约束。

        规则同世界意志：只读条目，不读正文全文。
        """
        raise NotImplementedError("等 demo：读 world.db 未实现")

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
