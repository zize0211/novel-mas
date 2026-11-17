"""骨架冒烟测试 —— 只验「结构对不对」，不验「功能好不好」。

重点验三件事：
1. 三个库能建出来
2. 工具注册齐全，白名单没有漏洞
3. 隔离真的挡得住（写手拿不到写库的工具）
"""

from __future__ import annotations

import pytest

from novel_mas.agents import bootstrap
from novel_mas.agents.trend import WorldTrend
from novel_mas.agents.world_will import WorldWill
from novel_mas.db import base as db
from novel_mas.types import CurvePoint


# ---------- 1. 建库 ----------

def test_migrate_creates_three_dbs():
    db.migrate()
    for name in db.DB_NAMES:
        assert db.db_path(name).exists(), f"{name} 没建出来"


def test_tables_exist():
    db.migrate()
    expected = {
        "world.db": {"world_bible", "events", "links", "random_pool", "judgments"},
        "char.db": {"characters", "relationships", "arc_events", "score_rules"},
        "trend.db": {"ending_intent", "curve", "chapter_scores"},
    }
    for name, tables in expected.items():
        conn = db.connect(name)
        got = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert tables <= got, f"{name} 缺表: {tables - got}"


# ---------- 2. 注册完整 ----------

def test_bootstrap_registers_everything():
    reg = bootstrap()
    assert reg.check_coverage() == []


def test_role_tool_counts():
    reg = bootstrap()
    assert len(reg.names_for("world_will")) == 5
    assert len(reg.names_for("character")) == 3
    assert len(reg.names_for("trend")) == 3
    assert len(reg.names_for("writer")) == 5


# ---------- 3. 隔离真的挡得住 ----------

@pytest.mark.parametrize(
    "role,forbidden",
    [
        ("writer", "record_event"),
        ("writer", "manage_random"),
        ("writer", "set_curve"),
        ("writer", "import_character"),
        ("character", "record_event"),
        ("trend", "link_events"),
        ("world_will", "set_curve"),
    ],
)
def test_isolation_blocks_foreign_tools(role, forbidden):
    reg = bootstrap()
    with pytest.raises(PermissionError):
        reg.get(role, forbidden)


def test_writer_cannot_write_db():
    reg = bootstrap()
    assert reg.can_write_db("world_will") is True
    assert reg.can_write_db("character") is True
    assert reg.can_write_db("trend") is True
    assert reg.can_write_db("writer") is False


# ---------- 4. 两条纯逻辑（已真实现，值得测） ----------

def test_link_cycle_detection():
    # 已有 1 -> 2 -> 3，再连 3 -> 1 就成环
    edges = {1: {2}, 2: {3}}
    assert WorldWill.would_create_cycle(3, 1, edges) is True
    assert WorldWill.would_create_cycle(1, 3, edges) is False
    assert WorldWill.would_create_cycle(2, 2, edges) is True


def test_curve_slope_warning():
    points = [
        CurvePoint(chapter=1, target=50),
        CurvePoint(chapter=2, target=55),
        CurvePoint(chapter=3, target=15),  # 断崖：-40
    ]
    warnings = WorldTrend.validate_curve(points)
    assert any("落差 40" in w for w in warnings)


def test_curve_smooth_passes():
    points = [CurvePoint(chapter=i, target=50 + i * 5) for i in range(1, 6)]
    assert WorldTrend.validate_curve(points) == []


def test_trend_verdict_three_levels():
    assert WorldTrend.judge(score=60, target=60, tolerance=15, violated=[]) == "on_track"
    assert WorldTrend.judge(score=80, target=50, tolerance=15, violated=[]) == "drifted"
    assert (
        WorldTrend.judge(score=60, target=60, tolerance=15, violated=["主角不能死"])
        == "violated"
    )


# ---------- 5. 写手的小事链接只出候选 ----------

def test_writer_link_returns_candidate_only():
    reg = bootstrap()
    out = reg.call("writer", "link_minor_events", from_id=1, to_id=2, kind="echoes")
    assert out["status"] == "pending_review"
    assert out["candidate"] == {"from": 1, "to": 2, "kind": "echoes"}
