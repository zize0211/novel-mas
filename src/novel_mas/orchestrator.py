r"""最小闭环 —— 编排者（orchestrator）。

它做的事，就是把这七步串起来。**每一步都走 registry.call，没有一处直连库**：

    1. 定文风          writer.set_style
    2. 取本章曲线       writer.get_curve
    3. 取人物快照       writer.get_characters
    4. 取世界观条目      writer.get_worldview
    5. 让模型写正文      llm.complete（这一步不是工具，是写手的笔）
    6. 世界意志过闸      world_will.check_consistency
    7. 走向打分 → 落库   trend.score_direction

跑法：
    $env:PYTHONPATH = 'E:\novel-mas\src'
    $env:PYTHONIOENCODING = 'utf-8'
    python -m novel_mas.orchestrator

设计原则：
    - 用假模型也能跑通（不花钱、可重复）
    - 每一步都真的走工具、真的读写库，不是演示
    - 打回时把「失分项」写清楚，别只说不行

2026-11-18 改：这里原来有一整套私有副本（_get_curve / _get_characters /
_get_worldview / _judge / _score），跟 agent 里的工具重了一遍，
结果 16 个工具都验过了却没人用。现在全部换成 registry.call。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .agents import bootstrap
from .db import base as db
from .llm import FakeProvider, Provider, judge_mode, metered
from .tools.registry import ToolRegistry
from .types import Event, EventKind, TrendVerdict, Verdict

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "out"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# 篇幅软约束（2026-11-19 主人定：一章 2500–3000 字）。
# 只进 prompt，不拦章、不触发重写 —— 模型对数字的服从度一般，
# 先让它有个目标，实际字数照实报出来，超了自己看得见。
WORDS_MIN = 2500
WORDS_MAX = 3000


@dataclass
class WriteResult:
    chapter: int
    text: str
    verdict: str            # 过闸结果：pass | violated（拦没拦）
    trend_verdict: str      # 走向判定：on_track | drifted | violated（偏没偏）
    score: int
    target: int
    deviation: int
    tokens: int
    words: int = 0             # 正文字数（含标点，去掉空白后计数）
    evidence: list[str] = field(default_factory=list)
    out_path: str | None = None
    # token 按角色分账。只报一个总数，说不清钱花在哪 ——
    # 而「多花的钱是花在写作上，还是花在审查上」正是要回答的问题。
    cost_by_role: dict[str, int] = field(default_factory=dict)

    def brief(self) -> str:
        lines = [
            f"第 {self.chapter} 章 · 过闸 {self.verdict.upper()} / 走向 {self.trend_verdict}",
            f"  走向分 {self.score} / 目标 {self.target}（偏差 {self.deviation:+d}）",
            f"  用掉 token {self.tokens}",
            f"  篇幅 {self.words} 字（目标 {WORDS_MIN}–{WORDS_MAX}）",
        ]
        # 分账只列真花了钱的（取数类工具不调模型，永远是 0，不占行）
        spent = [
            f"{k} {v}"
            for k, v in sorted(self.cost_by_role.items(), key=lambda kv: -kv[1])
            if v
        ]
        if spent:
            lines.append("  分账：" + " / ".join(spent))
        if self.evidence:
            lines.append("  证据：")
            for e in self.evidence:
                lines.append(f"    - {e}")
        if self.out_path:
            lines.append(f"  落盘：{self.out_path}")
        return "\n".join(lines)


class Orchestrator:
    """最小闭环的调度器。没有 LLM 也能跑。"""

    def __init__(
        self, book_id: str = "demo", llm: Provider | None = None, style: str = "日常"
    ) -> None:
        self.book_id = book_id
        self.llm: Provider = llm or FakeProvider()
        self.style = style
        db.migrate()
        # 每个编排者用**自己的** registry：bootstrap 默认的那个是全局单例，
        # 先用假模型建过一轮之后，再换真模型也会拿回旧的（闭包里绑着假模型）。
        # book_id 必须一起传下去 —— 不然三个 agent 全去读 demo 那本书。
        self.registry = bootstrap(
            ToolRegistry(), llm=self.llm, book_id=self.book_id
        )

    # ---------------- 配置阶段（这三步是人在配，不是每章都跑） ----------------

    def ingest_worldview(self, prompt: str) -> int:
        """把一段世界观拆成条目入库。简化版：按句拆，不调模型。"""
        parts = [p.strip() for p in prompt.replace("；", "。").split("。") if p.strip()]
        conn = db.connect("world.db")
        n = 0
        try:
            for i, body in enumerate(parts, 1):
                conn.execute(
                    # OR REPLACE：重灌世界观是覆盖，不是一路追加。
                    # 之前是纯 INSERT，跑六次就堆出六份同样的三条。
                    "INSERT OR REPLACE INTO world_bible"
                    "(book_id,category,title,body,version,source,created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (
                        self.book_id,
                        "规则",
                        f"条目{i}",
                        body,
                        1,
                        "user_inject",
                        _now(),
                    ),
                )
                n += 1
            conn.commit()
        finally:
            conn.close()
        return n

    def add_character(self, name: str, profile: str, importance: str = "lead") -> None:
        conn = db.connect("char.db")
        try:
            conn.execute(
                "INSERT OR REPLACE INTO characters"
                "(book_id,name,importance,profile,state,as_of_chapter,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (self.book_id, name, importance, profile, "初登场", 0, _now()),
            )
            conn.commit()
        finally:
            conn.close()

    def set_trend(
        self,
        points: list[tuple[int, int, int]],
        ending_type: str = "happy",
        target_score: int = 80,
        hard_constraints: list[str] | None = None,
    ) -> None:
        conn = db.connect("trend.db")
        try:
            conn.execute(
                "INSERT OR REPLACE INTO ending_intent"
                "(book_id,ending_type,target_score,hard_constraints,soft_preferences,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (
                    self.book_id,
                    ending_type,
                    target_score,
                    json.dumps(hard_constraints or [], ensure_ascii=False),
                    "[]",
                    _now(),
                ),
            )
            for chapter, target, tol in points:
                conn.execute(
                    "INSERT OR REPLACE INTO curve(book_id,chapter,target,tolerance,created_at)"
                    " VALUES(?,?,?,?,?)",
                    (self.book_id, chapter, target, tol, _now()),
                )
            conn.commit()
        finally:
            conn.close()

    # ---------------- 写一章（七步全走工具） ----------------

    def write_chapter(
        self,
        chapter: int,
        brief: str,
        mock_text: str | None = None,
        max_retries: int = 1,
    ) -> WriteResult:
        reg, book = self.registry, self.book_id

        # 1. 定文风
        style = reg.call("writer", "set_style", book_id=book, preset=self.style)["resolved"]

        # 2. 取本章曲线
        curve = reg.call("writer", "get_curve", book_id=book, chapter=chapter)

        # 3. 取人物快照
        chars = reg.call("writer", "get_characters", book_id=book, chapter=chapter)

        # 4. 取世界观 + 硬约束 + 前情
        wv = reg.call("writer", "get_worldview", book_id=book)
        entries, constraints = wv["entries"], wv["constraints"]
        recap = wv.get("recap") or []

        # 5. 写正文（这一步是写手的笔，不是工具）
        char_block = "\n".join(
            f"- {c.name}（{c.importance}）：{c.profile}；当前 {c.state}"
            for c in chars
        ) or "- （无人物档）"
        world_block = "\n".join(f"- {e['body']}" for e in entries) or "- （无世界观）"

        # 前情：库里是倒序取的（最近在前），这里翻回时间正序给模型看
        recap_block = "\n".join(
            f"- 第 {r['chapter']} 章：{r['summary']}" for r in reversed(recap)
        ) or "- （这是第一章，前面没有故事）"

        base_prompt = (
            f"【文风】{style}\n"
            f"【本章目标】第 {chapter} 章，走向分目标 {curve.target}"
            f"（容差 ±{curve.tolerance}）\n"
            f"【前情】\n{recap_block}\n"
            f"【大纲】{brief}\n"
            f"【人物】\n{char_block}\n"
            f"【世界观】\n{world_block}\n"
            f"【硬约束】{constraints or '无'}\n"
            f"【篇幅】正文 {WORDS_MIN}–{WORDS_MAX} 字（含标点），目标中段 2800 字左右。\n"
            "  靠细节、动作、环境和心理把字数写够；不要用概括或重复注水，\n"
            "  也不要写成提纲。这一条是软要求，写不到也别硬凑。\n"
            f"请写这一章的正文。"
        )

        # 打回后重写。mock_text 是测试用的固定稿，重写没意义，所以只跑一稿。
        retry_budget = 0 if mock_text is not None else max(0, max_retries)
        feedback = ""
        attempts = 0
        blocked = False                  # 循环里会重算，先给个底
        arbitrations: list[dict] = []    # 仲裁记录（可能有多次重写）
        cost: dict[str, int] = {}        # token 按角色分账：多花的钱花在写作还是审查？

        for attempt in range(retry_budget + 1):
            attempts = attempt + 1
            if mock_text is not None:
                text = mock_text
            else:
                with metered(cost, "writer", self.llm):
                    text = self.llm.complete(base_prompt + feedback)

            # 6. 世界意志过闸（真推理，不是关键词）
            #
            # 判官一律走 judge_mode：尺子用 0.1，写正文用 0.8。
            # 实测 0.8 下同一份正文在 55~72 之间跳，0.1 下三次都是 64。
            with metered(cost, "world_will", self.llm), judge_mode(self.llm):
                judgment = reg.call(
                    "world_will", "check_consistency", claim=text, chapter=chapter
                )

            # 7. 走向打分（模型抽事件 + Python 按口径算分）
            with metered(cost, "trend", self.llm), judge_mode(self.llm):
                scored = reg.call(
                    "trend", "score_direction", book_id=book, text=text, chapter=chapter
                )

            # 8. 人物裁判：只评 lead（主角级）。support/minor 不评，省调用。
            #
            #    口径：人物分低**不硬拦**。它是软判断——「这章人物有点飘」跟
            #    「主角死了」不是一回事，不能同样一票否决。
            #    但它照样进证据链：不过阈值时把失分项带出来，那是改稿线索。
            char_reviews: list[dict] = []
            for c in chars:
                if c.importance != "lead":
                    continue
                with metered(cost, "character", self.llm), judge_mode(self.llm):
                    char_reviews.append(
                        reg.call(
                            "character",
                            "score_in_character",
                            book_id=book,
                            name=c.name,
                            text=text,
                            chapter=chapter,
                        )
                    )

            # 拦不拦：硬裁判（世界 / 走向）**任一说撞了就拦**。
            # 光看世界裁判会漏 —— 走向裁判自己也有一套撞约束检查（_check_violated），
            # 它判 violated 而世界裁判说 pass 时，以前会：返回 pass、成文落盘、库里存 violated。
            # 两个裁判独立判、各自可能漏，所以取并集，不取交集。
            # 人物裁判只记不拦，见上面第 8 步的口径。
            blocked = (
                judgment.verdict is Verdict.HARD_CONFLICT
                or scored.verdict is TrendVerdict.VIOLATED
            )

            char_ok = all(r["passed"] for r in char_reviews)
            last_chance = attempt >= retry_budget

            # 硬裁判（世界 / 走向）：它们管"该不该一票否决"
            hard_blocked = (
                judgment.verdict is Verdict.HARD_CONFLICT
                or scored.verdict is TrendVerdict.VIOLATED
            )
            # 全过 = 硬裁判都没事，而且人物也过
            passed_all = (not hard_blocked) and char_ok

            if passed_all:
                blocked = False
                break

            if last_chance:
                # 改的机会用完了还不过 → 拦。
                # 硬裁判说撞了当然拦；人物跑偏改不过也拦 ——
                # 不因为一次软判断的误判就毙掉整章，但给过机会还不改就是真跑偏。
                blocked = True
                break

            # ---- 还有改写机会：请仲裁者给指令，别自己拼 ----
            #
            # 为什么不自己拼：三个裁判各说各的，写手拿到会懵
            # （世界说 pass，人物说不行，听谁的？）。
            # 收敛成一条可执行的改写指令，是仲裁者的活。
            # 先把三份意见整理好，再请它裁 —— 这样计数只包住一次调用
            judge_list = [
                {
                    "judge": "world_will",
                    "verdict": judgment.verdict.value,
                    "reason": judgment.reason,
                },
                {
                    "judge": "trend",
                    "verdict": scored.verdict.value,
                    "reason": (
                        f"{scored.score} 分 / 目标 {scored.target}"
                        f"（偏差 {scored.deviation:+d}）"
                    ),
                    "violations": scored.violated_constraints,
                },
            ] + [
                {
                    "judge": f"character:{r['name']}",
                    "verdict": "过" if r["passed"] else "不过",
                    "reason": r["reason"],
                    "violations": r["violations"],
                }
                for r in char_reviews
            ]

            with metered(cost, "acceptor", self.llm), judge_mode(self.llm):
                arbitration = reg.call(
                    "acceptor",
                    "arbitrate",
                    chapter=chapter,
                    text=text,
                    brief=brief,
                    judgments=judge_list,
                )
            arbitrations.append(arbitration)

            if arbitration["decision"] == "pass":
                # 它看完全部材料说不用改 —— 放行
                blocked = False
                break

            blocked = arbitration["decision"] == "block"
            if blocked:
                break

            feedback = (
                "\n\n【主编的改写指令】\n"
                f"{arbitration['rewrite_advice']}\n"
                "按这个改，其它地方不要动。"
            )

        evidence: list[str] = []
        # 两个硬裁判都说没事、结果却被拦了 —— 那只能是人物拦的。
        # 不写这一行，证据链看起来会自相矛盾（「过闸 pass」却说 violated）。
        if blocked and (
            judgment.verdict is not Verdict.HARD_CONFLICT
            and scored.verdict is not TrendVerdict.VIOLATED
        ):
            evidence.append("拦下原因：人物跑偏，重写次数用尽仍不过阈值")
        if attempts > 1:
            evidence.append(f"打回后重写了 {attempts - 1} 次")
        for i, arb in enumerate(arbitrations, 1):
            evidence.append(
                f"仲裁 #{i}：{arb['decision']}"
                f"（听 {arb['agrees_with']}）{arb['why'][:60]}"
            )
        evidence.append(f"过闸 {judgment.verdict.value}：{judgment.reason}")
        evidence += [
            f"事件 {e['kind']}（{e['weight']:+d}）：{e['what']}" for e in scored.evidence
        ]
        if scored.violated_constraints:
            evidence += [f"撞约束：{v}" for v in scored.violated_constraints]

        # 人物裁判的结论也进证据链，但不参与拦不拦
        for r in char_reviews:
            mark = "过" if r["passed"] else "不过"
            evidence.append(
                f"人物 {r['name']}：{r['score']} 分"
                f"（阈值 {r['threshold']}，{mark}）"
            )
            for v in r["violations"]:
                evidence.append(f"  人设偏离：{v}")
            if not r["passed"] and r.get("fix_hint"):
                evidence.append(f"  建议：{r['fix_hint']}")

        # 落库：一章一条，重跑就覆盖。
        # 判定以**过闸**为准：被拦下的章，走向分再正常也是 violated。
        # 否则库里会写着 on_track，而正文其实被拦了——查历史的人会被骗。
        #
        # 这一处是编排层唯一直连库的地方，**不是漏改**：
        # chapter_scores 记的是「编排历史」（第几章跑过、得了几分），
        # 不是任何 agent 的领地。三个 agent 谁都不该写这张表。
        #
        # 用 ON CONFLICT：库里已经有 UNIQUE(book_id, chapter) 兜底，
        # 不再靠「先 DELETE 再 INSERT」这种自觉写法（那正是 8 条重复的来源）。
        final_verdict = "violated" if blocked else scored.verdict.value
        conn = db.connect("trend.db")
        try:
            conn.execute(
                "INSERT INTO chapter_scores"
                "(book_id,chapter,score,target,deviation,verdict,evidence,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)"
                " ON CONFLICT(book_id, chapter) DO UPDATE SET"
                "   score     = excluded.score,"
                "   target    = excluded.target,"
                "   deviation = excluded.deviation,"
                "   verdict   = excluded.verdict,"
                "   evidence  = excluded.evidence,"
                "   created_at= excluded.created_at",
                (
                    book,
                    chapter,
                    scored.score,
                    scored.target,
                    scored.deviation,
                    final_verdict,
                    json.dumps(evidence, ensure_ascii=False),
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        # 落盘：只有过闸才写成文
        out_path = None
        if not blocked:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            # demo 沿用老文件名（实验记录引用过 ch01.txt），其它书加前缀，免得互相覆盖
            name = f"ch{chapter:02d}.txt" if book == "demo" else f"{book}-ch{chapter:02d}.txt"
            p = OUT_DIR / name
            p.write_text(text, encoding="utf-8")
            out_path = str(p)

            # 章过了，把「这一章发生了什么」记成小事 —— 下一章取世界观时能看到。
            # 只记过的章：被拦下的那几稿是废稿，不该进历史。
            summary = brief
            if scored.evidence:
                summary += "｜" + "；".join(e["what"] for e in scored.evidence)
            reg.call(
                "world_will",
                "record_minor_event",
                ev=Event(
                    book_id=book,
                    kind=EventKind.MINOR,
                    title=f"第{chapter}章",
                    summary=summary,
                    chapter=chapter,
                ),
            )

            # 章落盘了，人物状态才推进一章 —— 被拦下的稿不算发生过。
            # 状态由人物判官看完正文后给出（state_update）；没给就不推进，不替它编。
            for rv in char_reviews:
                st = str(rv.get("state_update") or "").strip()
                if not st:
                    continue
                reg.call(
                    "character",
                    "advance_state",
                    book_id=book,
                    name=rv["name"],
                    chapter=chapter,
                    state=st,
                )

        # 字数口径：去掉所有空白后的字符数（含标点）。中文按字算，不按 token 算。
        words = len("".join(text.split()))

        return WriteResult(
            chapter=chapter,
            words=words,
            text=text,
            verdict="violated" if blocked else "pass",
            trend_verdict=scored.verdict.value,
            score=scored.score,
            target=scored.target,
            deviation=scored.deviation,
            tokens=self.llm.tokens_used,
            evidence=evidence,
            out_path=out_path,
            cost_by_role=dict(cost),
        )


# ---------------- 命令行入口 ----------------

def main() -> None:
    print("=" * 62)
    print("最小闭环：注入世界观 → 定人设 → 设曲线 → 写一章 → 过闸 → 打分")
    print("=" * 62)

    orc = Orchestrator(book_id="demo", llm=FakeProvider(style="日常"))

    n = orc.ingest_worldview(
        "月光城的雨从不停。城东有座旧钟楼，钟声一响，全城的人都会停下手里的活。"
        "李家在城南摆豆腐摊，已经三代。"
    )
    print(f"\n[1] 世界观入库：{n} 条")

    orc.add_character("李三", "豆腐摊第三代，怕生，说话前会先摸一下耳朵", "lead")
    print("[2] 人物入库：李三")

    orc.set_trend(
        points=[(1, 50, 15), (2, 60, 15), (3, 75, 15)],
        ending_type="happy",
        target_score=80,
        hard_constraints=["李三不能死"],
    )
    print("[3] 曲线入库：第1章 50 / 第2章 60 / 第3章 75")

    print("\n[4] 写第 1 章（正常）")
    r1 = orc.write_chapter(1, "李三在巷口捡到一枚铜钱，抬头看见钟楼上有个人影。")
    print(r1.brief())

    print("\n[5] 写第 2 章（故意踩硬约束，看它拦不拦）")
    r2 = orc.write_chapter(
        2,
        "李三病倒了。",
        mock_text="那一夜雨没停。李三死了，血从门槛一路漫到街心，没人敢去收。",
    )
    print(r2.brief())
    print("  是否写出了成文文件：", r2.out_path or "没有（被拦下）")

    print("\n" + "=" * 62)
    print("闭环跑完。四件事都真的发生了：")
    print("  - 每一章都走 registry.call，七步没有一处直连库")
    print("  - 第 1 章过闸并落盘")
    print("  - 第 2 章踩了「李三不能死」被拦，没有落盘")
    print("  - 打分和历史都记在 chapter_scores 里")
    print("=" * 62)


if __name__ == "__main__":
    main()
