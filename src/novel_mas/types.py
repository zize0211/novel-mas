"""共享类型与枚举。所有 agent 和工具都用这里的定义，避免各写一套。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------- 裁决 ----------

class Verdict(str, Enum):
    PASS = "pass"
    SOFT_WARN = "soft_warn"
    HARD_CONFLICT = "hard_conflict"


class TrendVerdict(str, Enum):
    ON_TRACK = "on_track"
    DRIFTED = "drifted"
    VIOLATED = "violated"


class EndingType(str, Enum):
    HAPPY = "happy"
    BITTERSWEET = "bittersweet"
    BAD = "bad"


# ---------- 事件 ----------

class EventKind(str, Enum):
    MAJOR = "major"   # 改变世界状态，必须带 constraints
    MINOR = "minor"   # 只留痕，不加约束


class LinkKind(str, Enum):
    CAUSES = "causes"
    FORESHADOWS = "foreshadows"
    PAYS_OFF = "pays_off"
    ECHOES = "echoes"


class Source(str, Enum):
    USER = "user"
    USER_INJECT = "user_inject"
    WORLD_WILL = "world_will"
    WRITER = "writer"
    RANDOM = "random"
    WRITER_CANDIDATE = "writer_candidate"


# ---------- 数据结构 ----------

@dataclass
class Judgment:
    verdict: Verdict
    claim: str
    reason: str
    rule_ids: list[int] = field(default_factory=list)
    event_ids: list[int] = field(default_factory=list)
    fix: str = ""
    chapter: int | None = None


@dataclass
class Event:
    book_id: str
    kind: EventKind
    title: str
    summary: str
    constraints: list[str] = field(default_factory=list)
    caused_by: list[int] = field(default_factory=list)
    chapter: int | None = None


@dataclass
class CurvePoint:
    chapter: int
    target: int
    tolerance: int = 15


@dataclass
class DirectionScore:
    score: int
    verdict: TrendVerdict
    target: int | None = None
    deviation: int | None = None
    evidence: list[dict] = field(default_factory=list)
    violated_constraints: list[str] = field(default_factory=list)
    fix_hint: str = ""


@dataclass
class CharacterSnapshot:
    name: str
    importance: str
    profile: str
    state: str | None
    as_of_chapter: int | None
    relationships: list[dict] = field(default_factory=list)


# ---------- 常量 ----------

MAX_SLOPE_DEFAULT = 25      # 相邻章允许的最大走向分变化
TOLERANCE_DEFAULT = 15      # 默认容忍带
SCORE_MIN, SCORE_MAX = 0, 100
