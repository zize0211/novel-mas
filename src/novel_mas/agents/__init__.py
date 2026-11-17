"""四个 agent 的公共底座 + 注册入口。

注意：agent 之间**没有**互相调用的口子。想跨角色做事，只能通过
`tools.registry` 里的工具，而工具是设了白名单的。
"""

from __future__ import annotations

import sqlite3

from ..db import base as db


class BaseAgent:
    """所有 agent 的基类。只管两件事：自己的库、自己的角色名。"""

    role: str = ""
    db_name: str | None = None

    def connect(self) -> sqlite3.Connection:
        if self.db_name is None:
            raise RuntimeError(f"{type(self).__name__} 没有自己的库")
        return db.connect(self.db_name)


def bootstrap(registry=None):
    """把四个 agent 的工具都注册到 registry，返回 registry。

    编排层（orchestrator）启动时调一次。
    """
    from ..tools.registry import ToolRegistry, registry as default_registry

    reg = registry if registry is not None else default_registry
    if reg._tools:  # 已经注册过就不重复
        return reg

    from . import character, trend, world_will, writer

    world_will.bind(reg)
    character.bind(reg)
    trend.bind(reg)
    writer.bind(reg)

    missing = reg.check_coverage()
    if missing:
        raise RuntimeError(f"白名单里有工具没注册: {missing}")
    return reg
