"""零依赖自检 —— 不用 pytest 也能跑。

    cd E:\\novel-mas
    set PYTHONPATH=src
    python tests\\selfcheck.py

验的是「结构对不对」，不验功能实现。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from novel_mas.agents import bootstrap
from novel_mas.agents.trend import WorldTrend
from novel_mas.agents.world_will import WorldWill
from novel_mas.db import base as db
from novel_mas.types import CurvePoint, Event, EventKind

passed = 0
failed: list[str] = []


def check(name: str, fn) -> None:
    global passed
    try:
        fn()
        passed += 1
        print(f"  [ok]   {name}")
    except Exception as exc:  # noqa: BLE001
        failed.append(f"{name}: {exc}")
        print(f"  [FAIL] {name}: {exc}")
        traceback.print_exc()


print("=== 1. 建三个库 ===")
check("migrate 跑通", lambda: db.migrate())


def _tables() -> None:
    expected = {
        "world.db": {"world_bible", "events", "links", "random_pool", "judgments"},
        "char.db": {"characters", "relationships", "arc_events", "score_rules"},
        "trend.db": {"ending_intent", "curve", "chapter_scores"},
    }
    for name, want in expected.items():
        conn = db.connect(name)
        got = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        missing = want - got
        assert not missing, f"{name} 缺表 {missing}"


check("三库表齐全", _tables)

print("=== 2. 工具注册 ===")
reg = bootstrap()
check("白名单无遗漏", lambda: (_ for _ in ()).throw(AssertionError(reg.check_coverage())) if reg.check_coverage() else None)


def _counts() -> None:
    assert len(reg.names_for("world_will")) == 8
    assert len(reg.names_for("character")) == 5
    assert len(reg.names_for("trend")) == 3
    assert len(reg.names_for("writer")) == 5
    assert len(reg.names_for("acceptor")) == 2


check("四个角色工具数对得上", _counts)

print("=== 3. 隔离挡得住 ===")


def _blocked(role: str, tool: str) -> None:
    try:
        reg.get(role, tool)
    except PermissionError:
        return
    raise AssertionError(f"{role} 竟然拿到了 {tool}")


check("写手拿不到 record_event", lambda: _blocked("writer", "record_event"))
check("写手拿不到 set_curve", lambda: _blocked("writer", "set_curve"))
check("人物控制拿不到 link_events", lambda: _blocked("character", "link_events"))
check("世界意志拿不到 set_style", lambda: _blocked("world_will", "set_style"))


def _no_write() -> None:
    assert reg.can_write_db("world_will") is True
    assert reg.can_write_db("writer") is False


check("写手完全没有写库权限", _no_write)

print("=== 4. 两条纯逻辑 ===")


def _cycle() -> None:
    edges = {1: {2}, 2: {3}}
    assert WorldWill.would_create_cycle(3, 1, edges) is True
    assert WorldWill.would_create_cycle(1, 3, edges) is False
    assert WorldWill.would_create_cycle(2, 2, edges) is True


check("事件链接查环", _cycle)


def _slope() -> None:
    pts = [
        CurvePoint(chapter=1, target=50),
        CurvePoint(chapter=2, target=55),
        CurvePoint(chapter=3, target=15),
    ]
    warns = WorldTrend.validate_curve(pts)
    assert any("落差 40" in w for w in warns), warns


check("曲线断崖告警", _slope)


def _verdict() -> None:
    assert WorldTrend.judge(60, 60, 15, []) == "on_track"
    assert WorldTrend.judge(80, 50, 15, []) == "drifted"
    assert WorldTrend.judge(60, 60, 15, ["主角不能死"]) == "violated"


check("走向三级判定", _verdict)

print("=== 5. 写手只能提交候选 ===")


def _candidate() -> None:
    out = reg.call("writer", "link_minor_events", from_id=1, to_id=2, kind="echoes")
    assert out["status"] == "pending_review"


check("小事链接只出候选", _candidate)


def _major_needs_constraints() -> None:
    from novel_mas.agents.world_will import WorldWill as WW

    ww = WW()
    ev = Event(book_id="b1", kind=EventKind.MAJOR, title="某城被毁", summary="...")
    try:
        ww.record_event(ev)
    except ValueError as exc:
        assert "constraints" in str(exc)
        return
    raise AssertionError("大事件不带 constraints 竟然没被拦")


check("大事件必须带 constraints", _major_needs_constraints)

print("=== 6. 人物状态按章推进 ===")


def _state_by_chapter() -> None:
    from novel_mas.agents.character import CharacterControl
    from novel_mas.agents.writer import Writer

    book = "selfcheck-state"
    cc = CharacterControl(book_id=book)
    cc.import_character(
        book_id=book, name="阿甲", profile="守夜人，话少", importance="lead"
    )
    cc.advance_state(book_id=book, name="阿甲", chapter=1, state="雨里淋了一夜，病倒")
    cc.advance_state(book_id=book, name="阿甲", chapter=3, state="病好，回钟楼守夜")

    w = Writer()
    by_ch = {
        ch: {c.name: c for c in w.get_characters(book, chapter=ch)}["阿甲"]
        for ch in (1, 2, 3)
    }
    assert by_ch[1].state == "雨里淋了一夜，病倒", by_ch[1]
    assert by_ch[1].as_of_chapter == 1, by_ch[1]
    # 第 2 章自己没有状态 —— 必须取第 1 章那条，绝不能偷看第 3 章
    assert by_ch[2].state == "雨里淋了一夜，病倒", by_ch[2]
    assert by_ch[2].as_of_chapter == 1, by_ch[2]
    assert by_ch[3].as_of_chapter == 3, by_ch[3]

    # 同名重导 = 改档，不是报错：档案更新，进度不丢
    cc.advance_state(book_id=book, name="阿甲", chapter=5, state="又守了四夜")
    cc.import_character(
        book_id=book, name="阿甲", profile="守夜人，话少，左手旧伤", importance="lead"
    )
    again = {c.name: c for c in w.get_characters(book, chapter=5)}["阿甲"]
    assert again.profile == "守夜人，话少，左手旧伤", again
    assert again.as_of_chapter == 5, again

    # 删干净：档案、逐章状态一起走；删不存在的人要报错，不许静默成功
    cc.remove_character(book_id=book, name="阿甲")
    assert w.get_characters(book, chapter=5) == []
    try:
        cc.remove_character(book_id=book, name="阿甲")
        raise AssertionError("删不存在的人物应当抛 LookupError")
    except LookupError:
        pass

    # selfcheck 不往库里留测试数据
    conn = db.connect("char.db")
    try:
        conn.execute("DELETE FROM character_states WHERE book_id = ?", (book,))
        conn.execute("DELETE FROM characters WHERE book_id = ?", (book,))
        conn.commit()
    finally:
        conn.close()


check("状态随章取，旧章不被未来覆盖", _state_by_chapter)

print("=== 7. 世界观单条可改可删 ===")


def _worldview_editable() -> None:
    from novel_mas.agents.world_will import WorldWill
    from novel_mas.llm import FakeProvider
    from novel_mas.orchestrator import Orchestrator

    book = "selfcheck-wv"
    # 先清场：自检不能依赖上一次跑剩下的数据，否则 version 会一路往上爬
    conn = db.connect("world.db")
    try:
        conn.execute("DELETE FROM world_bible WHERE book_id = ?", (book,))
        conn.commit()
    finally:
        conn.close()

    ww = WorldWill(book_id=book)
    ww.set_worldview(book, "雨", "雨从不停", "地理")
    ww.set_worldview(book, "雨", "雨小了些", "地理")

    conn = db.connect("world.db")
    try:
        rows = conn.execute(
            "SELECT body, version FROM world_bible WHERE book_id = ? AND title = '雨'",
            (book,),
        ).fetchall()
        assert len(rows) == 1, [dict(r) for r in rows]
        assert rows[0]["body"] == "雨小了些", dict(rows[0])
        assert rows[0]["version"] == 2, dict(rows[0])
    finally:
        conn.close()

    # 整段重灌：句数变少时旧条目不能留成孤儿
    orc = Orchestrator(book_id=book, llm=FakeProvider())
    orc.ingest_worldview("甲。乙。丙。")
    orc.ingest_worldview("丁。")
    conn = db.connect("world.db")
    try:
        left = conn.execute(
            "SELECT title, body FROM world_bible WHERE book_id = ?", (book,)
        ).fetchall()
        assert len(left) == 2, [dict(r) for r in left]  # 「雨」+「条目1」
    finally:
        conn.close()

    ww.remove_worldview(book, "雨")
    try:
        ww.remove_worldview(book, "雨")
        raise AssertionError("删不存在的条目应当抛 LookupError")
    except LookupError:
        pass

    conn = db.connect("world.db")
    try:
        conn.execute("DELETE FROM world_bible WHERE book_id = ?", (book,))
        conn.commit()
    finally:
        conn.close()


check("世界观可改可删，重灌不留孤儿", _worldview_editable)

print("=== 8. 书：列表 / 改名 / 删除 ===")


def _book_ops() -> None:
    import json as _json

    from novel_mas import web
    from novel_mas.agents.world_will import WorldWill
    from novel_mas.llm import FakeProvider
    from novel_mas.orchestrator import Orchestrator

    old, new = "q1-old", "q1-new"
    ww = WorldWill(book_id=old)
    ww.set_worldview(old, "雨", "雨从不停")
    Orchestrator(book_id=old, llm=FakeProvider()).ingest_worldview("甲。乙。")

    overview = web.books_overview()
    ids = [b["book_id"] for b in overview]
    assert old in ids, ids
    stat = next(b for b in overview if b["book_id"] == old)
    assert stat["world"] == 3, stat      # 雨 + 条目1 + 条目2

    moved = web.rename_book(old, new)
    assert moved >= 3, moved
    ids = [b["book_id"] for b in web.books_overview()]
    assert new in ids and old not in ids, ids

    removed, backup = web.delete_book(new)
    assert removed >= 3, removed
    assert backup.exists(), backup
    dumped = _json.loads(backup.read_text(encoding="utf-8"))
    assert any("world_bible" in k for k in dumped), list(dumped)
    assert new not in [b["book_id"] for b in web.books_overview()]
    backup.unlink(missing_ok=True)

    # 备份可选：关掉就不该留文件
    ww.set_worldview(new, "雨", "雨从不停")
    removed2, no_backup = web.delete_book(new, backup=False)
    assert removed2 >= 1, removed2
    assert no_backup is None, no_backup


check("书能列出来、改名、删除（删除先备份）", _book_ops)

print()
print(f"通过 {passed} 项，失败 {len(failed)} 项")
if failed:
    for f in failed:
        print("  -", f)
    sys.exit(1)
