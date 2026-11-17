"""人物控制 agent —— 管「这人会不会这样」。

隔离：独占 char.db；写手只能通过工具拿只读快照，碰不到本体。
工具：3 个。

实现状态：接口定稿，全部 stub（等 demo）。
"""

from __future__ import annotations

from ..types import CharacterSnapshot
from . import BaseAgent


class CharacterControl(BaseAgent):
    role = "character"
    db_name = "char.db"

    # ---------- 工具 1：随机生成角色 ----------

    def random_character(
        self, book_id: str, need: str, importance: str = "minor"
    ) -> CharacterSnapshot:
        """按剧情需要现生成一个角色。

        必须先在 char.db 里查重名 —— 否则会出现两个"李三"。
        """
        raise NotImplementedError("等 demo：生成 + 查重未实现")

    # ---------- 工具 2：导入角色 ----------

    def import_character(
        self, book_id: str, name: str, profile: str, importance: str = "lead"
    ) -> CharacterSnapshot:
        """导入主角 / 配角档。用户可指定重要程度。"""
        raise NotImplementedError("等 demo：入库未实现")

    # ---------- 工具 3：角色剧情打分 ----------

    def score_in_character(
        self, book_id: str, name: str, text: str, chapter: int | None = None
    ) -> dict:
        """给剧情里的角色行为打「符合人设」分。

        规则（已定，待实现）：
        - 阈值 x 从 char.db 的 score_rules 读，不写死在代码里
        - 必须给出「不符合哪一条人设」，不能只给数字
        - 低于 x → 打回，并把失分项回传给写作 agent
        """
        raise NotImplementedError("等 demo：打分未实现")


def bind(registry) -> None:
    agent = CharacterControl()
    registry.register("random_character", agent.random_character)
    registry.register("import_character", agent.import_character)
    registry.register("score_in_character", agent.score_in_character)
