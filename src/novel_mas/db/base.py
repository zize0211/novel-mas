"""三个库的连接与建表。

world.db  ── 世界意志独占
char.db   ── 人物控制独占（写手只读）
trend.db  ── 世界走向独占（写手只读）
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
SCHEMA_FILE = Path(__file__).resolve().parents[3] / "schemas" / "all.sql"

DB_NAMES = ("world.db", "char.db", "trend.db")

# all.sql 里用这一段分隔符切库
_SECTION_RE = re.compile(r"--\s*=+\s*(world|char|trend)\.db.*?=+")


def db_path(name: str) -> Path:
    if name not in DB_NAMES:
        raise ValueError(f"未知库: {name}；只有 {DB_NAMES}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / name


def connect(name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(name))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _split_schema() -> dict[str, str]:
    """把 all.sql 按 '__SECTION__' 分段，返回 {world.db: sql, ...}。"""
    text = SCHEMA_FILE.read_text(encoding="utf-8")
    parts = _SECTION_RE.split(text)
    # split 结果形如 ['', 'world', sql1, 'char', sql2, 'trend', sql3]
    out: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        key = parts[i]
        sql = parts[i + 1]
        # 去掉下一段开头的重复分隔符残留
        sql = _SECTION_RE.sub("", sql)
        out[f"{key}.db"] = sql
    return out


def migrate(verbose: bool = False) -> dict[str, int]:
    """建/更新三个库，返回每个库执行了多少条语句。"""
    sections = _split_schema()
    result: dict[str, int] = {}
    for name in DB_NAMES:
        sql = sections.get(name)
        if sql is None:
            raise RuntimeError(f"schemas/all.sql 里找不到 {name} 的分段")
        conn = connect(name)
        try:
            cur = conn.executescript(sql)
            conn.commit()
            result[name] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        finally:
            conn.close()
    if verbose:
        for k, v in result.items():
            print(f"{k}: ok")
    return result


def main() -> None:
    migrate(verbose=True)
    for name in DB_NAMES:
        conn = connect(name)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        conn.close()
        print(f"{name}: {[r['name'] for r in rows]}")


if __name__ == "__main__":
    main()
