-- ============ world.db（世界意志独占） ============

CREATE TABLE IF NOT EXISTS world_bible (
  id          INTEGER PRIMARY KEY,
  book_id     TEXT NOT NULL,              -- 哪本书的世界观：两本书不共用设定
  category    TEXT NOT NULL,              -- 地理/势力/人物/规则/历史/禁忌
  title       TEXT NOT NULL,
  body        TEXT NOT NULL,
  version     INTEGER NOT NULL DEFAULT 1,
  source      TEXT NOT NULL,              -- user_inject | world_will
  created_at  TEXT NOT NULL,
  -- 同一本书里标题唯一：重灌世界观应该是覆盖，不是一路追加。
  UNIQUE(book_id, title)
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

-- 每个人「到第几章是什么状态」。characters 只留最新一版，
-- 写手按「本章」取它之前最近的一条 —— 不然每章看到的都是「初登场」。
CREATE TABLE IF NOT EXISTS character_states (
  id         INTEGER PRIMARY KEY,
  book_id    TEXT NOT NULL,
  name       TEXT NOT NULL,
  chapter    INTEGER NOT NULL,
  state      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(book_id, name, chapter)
);

CREATE TABLE IF NOT EXISTS relationships (
  id         INTEGER PRIMARY KEY,
  book_id    TEXT NOT NULL,
  a          TEXT NOT NULL,
  b          TEXT NOT NULL,
  kind       TEXT NOT NULL,               -- 敌/友/亏欠/亲缘
  intensity  INTEGER NOT NULL DEFAULT 5,  -- 1..10
  as_of_chapter INTEGER,
  created_at TEXT NOT NULL,
  -- 两个人之间同一种关系只该有一条。方向按 a→b 定，反向存成另一行是有意为之。
  UNIQUE(book_id, a, b, kind)
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
  tokens     INTEGER,                     -- 本章这一次运行的 token 合计（旧数据为空）
  created_at TEXT NOT NULL,
  -- 一章只留一条成绩。重跑同一章要覆盖，不是并排堆两条。
  UNIQUE(book_id, chapter)
);

-- 卷纲：**人**给的总纲（六卷就是六行，原文照收不改写）。
-- 走向拿它切「本章指令」，写手看不到这一层 —— 写手吃的是加工后的指令。
CREATE TABLE IF NOT EXISTS arc (
  id           INTEGER PRIMARY KEY,
  book_id      TEXT NOT NULL,
  volume       INTEGER NOT NULL,
  title        TEXT,
  plan         TEXT NOT NULL,             -- 原文，不改写
  from_chapter INTEGER,                   -- 这一卷从第几章开始（None = 不按章段切）
  to_chapter   INTEGER,
  created_at   TEXT NOT NULL,
  UNIQUE(book_id, volume)
);

-- 本章指令：走向加工出来的「你该写什么 + 谁上场 / 谁不许出现」。
-- 为什么要落库：将来要能回答「当时为什么这么写」。指令只在内存里，就没法复现。
CREATE TABLE IF NOT EXISTS chapter_plan (
  id         INTEGER PRIMARY KEY,
  book_id    TEXT NOT NULL,
  chapter    INTEGER NOT NULL,
  volume     INTEGER,
  directive  TEXT NOT NULL,               -- 人看得懂的一段话
  cast_names TEXT,                        -- JSON 数组：本章出场的人（列名不能叫 cast，那是 SQL 关键字）
  forbidden  TEXT,                        -- JSON 数组：本章不许出现的人
  target     INTEGER,                     -- 本章情绪目标分（来自曲线）
  by_human   INTEGER NOT NULL DEFAULT 0,  -- 1 = 人改过，不再算走向原话
  created_at TEXT NOT NULL,
  UNIQUE(book_id, chapter)
);
