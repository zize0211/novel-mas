-- ============ world.db（世界意志独占） ============

CREATE TABLE IF NOT EXISTS world_bible (
  id          INTEGER PRIMARY KEY,
  category    TEXT NOT NULL,              -- 地理/势力/人物/规则/历史/禁忌
  title       TEXT NOT NULL,
  body        TEXT NOT NULL,
  version     INTEGER NOT NULL DEFAULT 1,
  source      TEXT NOT NULL,              -- user_inject | world_will
  created_at  TEXT NOT NULL
);

-- 大事件：必须带 constraints（它锁死了什么）
CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY,
  book_id      TEXT NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'major',   -- major | minor
  title        TEXT NOT NULL,
  summary      TEXT NOT NULL,
  chapter      INTEGER,
  constraints  TEXT,                      -- JSON 数组，minor 可为空
  caused_by    TEXT,                      -- JSON 数组
  source       TEXT NOT NULL,             -- user | writer | random
  created_at   TEXT NOT NULL
);

-- 事件之间的有向边
CREATE TABLE IF NOT EXISTS links (
  id         INTEGER PRIMARY KEY,
  book_id    TEXT NOT NULL,
  from_event INTEGER NOT NULL REFERENCES events(id),
  to_event   INTEGER NOT NULL REFERENCES events(id),
  kind       TEXT NOT NULL,               -- causes|foreshadows|pays_off|echoes
  source     TEXT NOT NULL,               -- world_will | writer_candidate
  adopted    INTEGER NOT NULL DEFAULT 0,  -- 写手提的候选：0=待审 1=已入库
  created_at TEXT NOT NULL,
  UNIQUE(from_event, to_event, kind)
);

CREATE TABLE IF NOT EXISTS random_pool (
  id             INTEGER PRIMARY KEY,
  book_id        TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('disaster','fortune')),
  target         TEXT NOT NULL,
  title          TEXT NOT NULL,
  body           TEXT NOT NULL,
  basis          TEXT NOT NULL,           -- JSON 数组：world_bible.id
  state          TEXT NOT NULL DEFAULT 'candidate',
  cooldown_until INTEGER,
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS judgments (
  id         INTEGER PRIMARY KEY,
  book_id    TEXT NOT NULL,
  verdict    TEXT NOT NULL,               -- pass|soft_warn|hard_conflict
  claim      TEXT NOT NULL,
  rule_ids   TEXT,
  event_ids  TEXT,
  reason     TEXT NOT NULL,
  fix        TEXT,
  chapter    INTEGER,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bible_cat    ON world_bible(category);
CREATE INDEX IF NOT EXISTS idx_events_book  ON events(book_id, chapter);
CREATE INDEX IF NOT EXISTS idx_links_book   ON links(book_id, adopted);
CREATE INDEX IF NOT EXISTS idx_pool_state   ON random_pool(book_id, state, kind);


-- ============ char.db（人物控制独占，写手只读） ============

CREATE TABLE IF NOT EXISTS characters (
  id            INTEGER PRIMARY KEY,
  book_id       TEXT NOT NULL,
  name          TEXT NOT NULL,
  importance    TEXT NOT NULL,            -- lead | support | minor
  profile       TEXT NOT NULL,            -- 人设正文
  state         TEXT,                     -- 当前状态（在哪/伤/心境）
  as_of_chapter INTEGER,
  created_at    TEXT NOT NULL,
  UNIQUE(book_id, name)
);

CREATE TABLE IF NOT EXISTS relationships (
  id         INTEGER PRIMARY KEY,
  book_id    TEXT NOT NULL,
  a          TEXT NOT NULL,
  b          TEXT NOT NULL,
  kind       TEXT NOT NULL,               -- 敌/友/亏欠/亲缘
  intensity  INTEGER NOT NULL DEFAULT 5,  -- 1..10
  as_of_chapter INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS arc_events (
  id         INTEGER PRIMARY KEY,
  book_id    TEXT NOT NULL,
  name       TEXT NOT NULL,
  chapter    INTEGER NOT NULL,
  beat       TEXT NOT NULL,               -- 弧线节点：动念/受挫/转折/兑现
  note       TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS score_rules (
  book_id    TEXT PRIMARY KEY,
  threshold  INTEGER NOT NULL DEFAULT 60, -- 低于 x 就打回
  updated_at TEXT NOT NULL
);


-- ============ trend.db（世界走向独占，写手只读） ============

CREATE TABLE IF NOT EXISTS ending_intent (
  book_id          TEXT PRIMARY KEY,
  ending_type      TEXT NOT NULL,         -- happy | bittersweet | bad
  target_score     INTEGER NOT NULL,
  hard_constraints TEXT,                  -- JSON 数组
  soft_preferences TEXT,
  created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curve (
  id         INTEGER PRIMARY KEY,
  book_id    TEXT NOT NULL,
  chapter    INTEGER NOT NULL,
  target     INTEGER NOT NULL,            -- 0..100
  tolerance  INTEGER NOT NULL DEFAULT 15,
  created_at TEXT NOT NULL,
  UNIQUE(book_id, chapter)
);

CREATE TABLE IF NOT EXISTS chapter_scores (
  id         INTEGER PRIMARY KEY,
  book_id    TEXT NOT NULL,
  chapter    INTEGER NOT NULL,
  score      INTEGER NOT NULL,
  target     INTEGER,
  deviation  INTEGER,
  verdict    TEXT NOT NULL,               -- on_track|drifted|violated
  evidence   TEXT,                        -- JSON
  created_at TEXT NOT NULL
);
