"""走一遍：types → 工具 → registry，看清楚每一层在干什么。

    cd E:\\novel-mas
    set PYTHONPATH=src
    python examples\\walkthrough.py

这个文件不调用任何 LLM，所以你随时能跑、不花钱。
每一段都先打印"现在在说什么"，再打印结果。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from novel_mas.agents import bootstrap
from novel_mas.agents.trend import WorldTrend
from novel_mas.agents.world_will import WorldWill
from novel_mas.db import base as db
from novel_mas.tools.registry import ROLE_TOOLS
from novel_mas.types import CurvePoint, Event, EventKind, LinkKind


def sec(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


# ---------------------------------------------------------------- 1
sec("1. types.py 里是什么？—— 只有「形状」，没有「动作」")

print("它定义的是【东西长什么样】，不是一个能调用的工具。")
print()
print("比如 Event（一个事件）：")
import dataclasses

for name, field in Event.__dataclass_fields__.items():
    if field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
        shown = "(必填)"
    elif field.default is not dataclasses.MISSING:
        shown = repr(field.default)
    else:
        shown = "(工厂函数)"
    print(f"    {name:<12} 默认值 = {shown}")
print()
print("一个具体的 Event 是这样造出来的：")
ev = Event(
    book_id="demo",
    kind=EventKind.MINOR,
    title="李三在巷口捡到一枚铜钱",
    summary="小事件，只留痕",
    chapter=1,
)
print(f"    {ev}")
print()
print("它只是数据，你把 ev.title 拿去干什么都行——但它自己不会做任何事。")

# ---------------------------------------------------------------- 2
sec("2. 工具是什么？—— 是【函数】，写在四个 agent 里")

print("工具 = agent 类上的一个方法。比如 Writer 里的 link_minor_events。")
print("工具名就是方法名，这是约定好的对应关系：")
print()
for role, names in ROLE_TOOLS.items():
    print(f"  {role:<12} → {sorted(names)}")

# ---------------------------------------------------------------- 3
sec("3. registry 是什么？—— 是【谁能用哪个工具】的名单")

reg = bootstrap()
print(f"bootstrap() 把 {len(reg._tools)} 个工具都注册进来了。")
print(f"  注册总数: {len(reg._tools)}  （= 5 + 3 + 3 + 5）")
print()
print("但它不是一张平铺的表，是按角色分好的：")
for role in ROLE_TOOLS:
    print(f"  {role:<12} 能拿 {len(reg.names_for(role))} 个")

# ---------------------------------------------------------------- 4
sec("4. 一次真实调用：写手提交候选链接")

print("写手调 link_minor_events —— 注意它是【有实现】的，能跑：")
out = reg.call("writer", "link_minor_events", from_id=1, to_id=2, kind="echoes")
print(f"    返回: {out}")
print()
print("看 status：pending_review —— 它只产出【候选】，不写库。")
print("真正入库要世界意志查环 + 查依据。")

# ---------------------------------------------------------------- 5
sec("5. 一次被拒绝的调用：写手想记大事件")

print("写手调 record_event —— 走 registry 的权限闸门：")
try:
    reg.get("writer", "record_event")
except PermissionError as exc:
    print(f"    PermissionError: {exc}")
print()
print("这就是「隔离」。不是写手忘了、也不是约定，是【代码里堵死的】。")

# ---------------------------------------------------------------- 6
sec("6. 工具自己也会校验：大事件必须带 constraints")

ww = WorldWill()
bad = Event(book_id="demo", kind=EventKind.MAJOR, title="某城被毁", summary="没写约束")
print("造一个大事件，constraints 留空，直接交给世界意志：")
try:
    ww.record_event(bad)
except ValueError as exc:
    print(f"    ValueError: {exc}")
print()
print("注意它拦在 NotImplementedError 【之前】——先校验，再谈实现。")

# ---------------------------------------------------------------- 7
sec("7. 纯逻辑已经能算：查环 + 曲线坡度")

edges = {1: {2}, 2: {3}}
print(f"事件图已有边: {edges}")
print(f"  连 3 → 1 会成环吗？ {WorldWill.would_create_cycle(3, 1, edges)}")
print(f"  连 1 → 3 会成环吗？ {WorldWill.would_create_cycle(1, 3, edges)}")

pts = [CurvePoint(1, 50), CurvePoint(2, 55), CurvePoint(3, 15)]
print(f"\n曲线目标分: {[(p.chapter, p.target) for p in pts]}")
for w in WorldTrend.validate_curve(pts):
    print(f"    [!] {w}")

# ---------------------------------------------------------------- 8
sec("8. 为什么「没有人调用」？")

print("因为缺一个【编排者】（orchestrator）。它要做的是：")
print()
print("    1. 调 writer.set_style()          → 定文风")
print("    2. 调 writer.get_curve(chapter)   → 拿本章目标分")
print("    3. 调 writer.get_characters()     → 拿人物快照")
print("    4. 调 writer.get_worldview()      → 拿硬约束")
print("    5. 让模型写正文（这一步还没有）")
print("    6. 调 world_will.check_consistency() → 判")
print("    7. 调 trend.score_direction()     → 打分")
print()
print("这七步现在一步都走不完 —— 因为第 5 步要 LLM，而 2/3/4/6/7 的实现是 stub。")
print()
print("✅ 但结构已经齐了：工具在、权限在、库在、形状在。剩下的都是填空。")

sec("跑完了")
