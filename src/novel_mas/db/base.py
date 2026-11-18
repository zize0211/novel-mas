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


def _upgrade(conn: sqlite3.Connection, name: str) -> None:
    """给老库补新列。

    CREATE TABLE IF NOT EXISTS 只对**不存在**的表生效 —— 已经建过的表
    不会因为改了 all.sql 就长出新的列。所以升级得单独写。
    """
    if name != "world.db":
        return

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(world_bible)")}
    if "book_id" not in cols:
        # 补列时已有数据全归到 demo：加这一列之前只有一本书。
        conn.execute(
            "ALTER TABLE world_bible"
            " ADD COLUMN book_id TEXT NOT NULL DEFAULT 'demo'"
        )
        conn.commit()

    # 同一本书里标题唯一。建索引前必须先清重复，否则索引建不上。
    conn.execute(
        "DELETE FROM world_bible WHERE id NOT IN ("
        "  SELECT MAX(id) FROM world_bible GROUP BY book_id, title)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_world_bible_title"
        " ON world_bible(book_id, title)"
    )
    conn.commit()


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
            _upgrade(conn, name)
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
