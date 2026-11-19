"""每个角色的动作日志：谁、干了什么、花了多少、成不成。

为什么是文件不是表：

- 这不是任何 agent 的领地。三个裁判各占一个库、acceptor 只读、写手不写库 ——
  为了写一行流水去动某张表的 schema，等于自己拆自己立的隔离规矩。
- 也不该塞进 `chapter_scores`：那张表一章一条，日志是一章几十上百条。

落盘：`<数据目录>/logs/agent-YYYYMMDD.jsonl`，一行一条，UTF-8。
选 JSONL 的理由：追加不重写、坏的只坏一行、`findstr` / `Select-String` 直接能查。

记什么（两种）：

| kind | 谁记的 | 内容 |
|---|---|---|
| `llm` | `llm.metered()` | 角色、动作、**token 增量**、耗时 —— 每个裁判和写手的每一次模型调用 |
| `tool` | `ToolRegistry.call()` | 角色、工具名、成不成、耗时 —— 跨角色通道上的每一次动作 |

`book_id` / `chapter` 不靠层层传参：编排层用 `bind()` 挂到上下文里，
`log_event()` 自己取。这样 registry 和 llm 都不用知道「现在在写哪一章」。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .db import base as db

_ctx: ContextVar[dict[str, Any]] = ContextVar("novel_mas_agentlog", default={})

ROLES = ("writer", "world_will", "character", "trend", "acceptor")


@contextmanager
def bind(book_id: str | None = None, chapter: int | None = None) -> Iterator[None]:
    """这一段里有谁的活。嵌套会叠加，退出时还原。"""
    merged = dict(_ctx.get())
    if book_id is not None:
        merged["book_id"] = book_id
    if chapter is not None:
        merged["chapter"] = chapter
    token = _ctx.set(merged)
    try:
        yield
    finally:
        _ctx.reset(token)


def log_dir() -> Path:
    return db.DATA_DIR / "logs"


def current(key: str) -> Any:
    """取上下文里的 book_id / chapter（没有就 None）。"""
    return _ctx.get().get(key)


def log_path(when: datetime | None = None) -> Path:
    stamp = (when or datetime.now()).strftime("%Y%m%d")
    return log_dir() / f"agent-{stamp}.jsonl"


def log_event(
    role: str,
    action: str,
    *,
    kind: str = "llm",
    tokens: int | None = None,
    ok: bool = True,
    ms: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """追加一条。**日志写不下去不能把正事弄死** —— 吞掉 OSError，返回那条记录。"""
    record: dict[str, Any] = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "role": role,
        "kind": kind,
        "action": action,
        "ok": ok,
    }
    for key, value in _ctx.get().items():
        if value is not None:
            record[key] = value
    if tokens is not None:
        record["tokens"] = tokens
    if ms is not None:
        record["ms"] = ms
    for key, value in extra.items():
        if value is not None:
            record[key] = value

    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:  # noqa: BLE001
        # 磁盘满了、目录只读 —— 记一笔到 stderr 就够，别抛
        print(f"[agentlog] 写不进去（{exc}）：{record}", file=sys.stderr)
    return record


class Timer:
    """`with Timer() as t: ...` → t.ms 是毫秒数。给 metered / registry 共用。"""

    def __init__(self) -> None:
        self.ms = 0

    def __enter__(self) -> "Timer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.ms = int((time.perf_counter() - self._t0) * 1000)


# ---- 读回来：按角色 / 按章汇总 ----

def read_records(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or log_path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # 坏一行就跳一行，不做全书校验
    return rows


def read_all() -> list[dict[str, Any]]:
    """目录里**所有**天的日志，按时间排。

    20 章长跑经常跨零点：只看今天的文件，昨天夜里那半截就凭空没了。
    """
    files = sorted(log_dir().glob("agent-*.jsonl"))
    rows: list[dict[str, Any]] = []
    for path in files:
        rows.extend(read_records(path))
    rows.sort(key=lambda r: r.get("ts") or "")
    return rows


def summarize(
    rows: list[dict[str, Any]], book_id: str | None = None, chapter: int | None = None
) -> dict[str, dict[str, Any]]:
    """按角色聚合：调用次数、token、耗时、动作清单。"""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if book_id is not None and row.get("book_id") != book_id:
            continue
        if chapter is not None and row.get("chapter") != chapter:
            continue
        role = row.get("role") or "?"
        slot = out.setdefault(
            role, {"calls": 0, "tokens": 0, "ms": 0, "actions": {}, "fails": 0}
        )
        slot["calls"] += 1
        slot["tokens"] += int(row.get("tokens") or 0)
        slot["ms"] += int(row.get("ms") or 0)
        if not row.get("ok", True):
            slot["fails"] += 1
        action = row.get("action") or "?"
        slot["actions"][action] = slot["actions"].get(action, 0) + 1
    return out


def format_report(rows: list[dict[str, Any]]) -> str:
    """一行一个角色，给人看的。"""
    lines: list[str] = []
    for role in list(ROLES) + sorted(set(summarize(rows)) - set(ROLES)):
        slot = summarize(rows).get(role)
        if not slot:
            continue
        acts = "、".join(f"{k}×{v}" for k, v in sorted(slot["actions"].items()))
        fail = f"，失败 {slot['fails']}" if slot["fails"] else ""
        lines.append(
            f"{role:11s} 调用 {slot['calls']:3d} 次  token {slot['tokens']:6d}"
            f"  用时 {slot['ms'] / 1000:6.1f}s{fail}\n            {acts}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="看角色日志（默认今天的）")
    ap.add_argument("--book", default=None, help="只看这本书")
    ap.add_argument("--chapter", type=int, default=None, help="只看这一章")
    ap.add_argument("--file", default=None, help="指定某个 jsonl（默认今天）")
    ap.add_argument(
        "--all-days", action="store_true", help="把目录里所有天的日志都读进来（跨零点的长跑）"
    )
    args = ap.parse_args(argv)

    if args.file:
        path = Path(args.file)
        rows = read_records(path)
    elif args.all_days:
        path = log_dir()
        rows = read_all()
    else:
        path = log_path()
        rows = read_records(path)
    if args.book or args.chapter is not None:
        rows = [
            r
            for r in rows
            if (args.book is None or r.get("book_id") == args.book)
            and (args.chapter is None or r.get("chapter") == args.chapter)
        ]
    print(f"日志：{path}    记录 {len(rows)} 条")
    print(format_report(rows) or "（没有记录）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
