"""工具注册表 —— 隔离在这里落地。

规矩：
1. 每个角色只能拿到白名单里的工具，多一个都拿不到。
2. 拿不到的调用会抛 PermissionError，不是静默失败。
3. 工具就是唯一的跨角色通道；agent 之间不直接对话。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# key: 角色名，value: 允许持有的工具名集合
ROLE_TOOLS: dict[str, set[str]] = {
    # 世界意志：8 个
    "world_will": {
        "check_consistency",
        "record_event",
        "record_minor_event",
        "link_events",
        "manage_random",
        "inject_event",
        "set_worldview",
        "remove_worldview",
    },
    # 人物控制：5 个
    "character": {
        "random_character",
        "import_character",
        "score_in_character",
        "advance_state",
        "remove_character",
    },
    # 世界走向：3 个
    "trend": {
        "set_ending_intent",
        "set_curve",
        "score_direction",
    },
    # 测试：2 个。跨三个库**只读**。
    # review_article = 整体评审（三裁判各看一摊，它合起来读一遍）
    # arbitrate      = 全局仲裁（三裁判打架、或一致不过时，由它裁 + 给改写指令）
    "acceptor": {
        "review_article",
        "arbitrate",
    },
    # 写作：5 个（全是取，没有一个是写）
    "writer": {
        "set_style",
        "get_curve",
        "get_characters",
        "get_worldview",
        "link_minor_events",
    },
}

# 这些工具会改库，只有对应角色能拿。写手一个都没有。
WRITE_TOOLS = {
    "record_event",
    "record_minor_event",
    "link_events",
    "manage_random",
    "inject_event",
    "random_character",
    "import_character",
    "set_ending_intent",
    "set_curve",
}


@dataclass
class Tool:
    name: str
    func: Callable[..., Any]
    doc: str = ""


class ToolRegistry:
    """注册所有工具，再按角色分发。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # ---- 注册侧（只有编排层能注册） ----

    def register(self, name: str, func: Callable[..., Any], doc: str = "") -> None:
        if name in self._tools:
            raise ValueError(f"工具重复注册: {name}")
        self._tools[name] = Tool(name=name, func=func, doc=doc)

    # ---- 分发侧 ----

    def names_for(self, role: str) -> set[str]:
        if role not in ROLE_TOOLS:
            raise KeyError(f"未知角色: {role}")
        return set(ROLE_TOOLS[role])

    def get(self, role: str, tool_name: str) -> Tool:
        """取工具。不在白名单里就拒绝——这是隔离的闸门。"""
        allowed = self.names_for(role)
        if tool_name not in allowed:
            raise PermissionError(
                f"角色 {role!r} 无权使用工具 {tool_name!r}；"
                f"它只能用: {sorted(allowed)}"
            )
        if tool_name not in self._tools:
            raise KeyError(f"工具未注册: {tool_name}")
        return self._tools[tool_name]

    def call(self, role: str, tool_name: str, **kwargs: Any) -> Any:
        return self.get(role, tool_name).func(**kwargs)

    def can_write_db(self, role: str) -> bool:
        """写手永远 False。"""
        return bool(self.names_for(role) & WRITE_TOOLS)

    def check_coverage(self) -> list[str]:
        """检查白名单里有没有注册漏掉的工具名。返回缺失列表。"""
        missing: list[str] = []
        for role, names in ROLE_TOOLS.items():
            for n in names:
                if n not in self._tools:
                    missing.append(f"{role}:{n}")
        return missing


registry = ToolRegistry()
