"""验收 agent —— 只读，判断这一章能不能交。

它跟三个裁判的区别：

    三个裁判**各管一摊**：世界管规则、人物管人设、走向管方向。
    各自合规，不代表**合起来能交**。

    acceptor 一个人把世界观、人物、走向**一起**看完，
    回答一个问题：**这章能不能交出去？**

两个工具：
    review_article  整体评审 —— 挑毛病出报告，不参与拦不拦
    arbitrate       全局仲裁 —— 三裁判打架、或一致不过时，由它裁 + 给改写指令

为什么需要它（E-004 的实证）：
    第 1 章世界裁判 pass（"未提及雨水停止"），走向 on_track，
    但那句话"雨又下起来了"跟"雨从不停"是矛盾的 —— 三个裁判各看一摊，
    没人负责"合起来读一遍顺不顺"。

为什么重写指令也归它：
    三个裁判各说各的，写手拿到会懵（世界说 pass，人物说不行，听谁的？）。
    收敛成一条可执行的改写指令，是验收方的活 —— 它知道要改成什么样才算过。

权限：**全部只读**，跨三个库读。它不写任何东西。
"""

from __future__ import annotations

from ..db import base as db
from ..llm import Provider, parse_json_block
from . import BaseAgent


class Acceptor(BaseAgent):
    role = "acceptor"
    # 它不属于任何一本库 —— 它是跨库只读的评审。
    db_name = None

    def __init__(self, book_id: str = "demo", llm: Provider | None = None) -> None:
        self.book_id = book_id
        self.llm = llm

    # ---------- 工具：整体评审 ----------
    def review(self, chapter: int, text: str, brief: str = "") -> dict:
        """把一章正文跟全套设定放在一起读，出整体报告。

        只出报告，不改任何东西，也不参与 blocked 判断 ——
        先让它跑一段时间，看它说的话值不值得当真。
        """
        if self.llm is None:
            raise RuntimeError("整体评审要模型：先给 Acceptor(llm=...)")

        prompt = (
            "你是这部小说的读者，也是它的审稿人。\n"
            "下面三样是作者手上的**全部设定**，然后是一章正文。\n\n"
            f"【世界观】\n{self._world()}\n\n"
            f"【人物】\n{self._characters()}\n\n"
            f"【结局意图】\n{self._intent()}\n\n"
            f"【这一章本来该写什么】\n{brief or '（没给大纲）'}\n\n"
            f"【正文】\n{text}\n\n"
            "读完之后回答三件事：\n"
            "1. 这一章**单独看**有没有硬伤（矛盾、断裂、人物不像）\n"
            "2. 这一章**跟设定一起看**有没有对不上的地方\n"
            "3. 你是读者的话，读到这里会不会想放下\n\n"
            "只输出 JSON：\n"
            '{"overall": "pass 或 shaky 或 broken", '
            '"problems": [{"kind": "设定矛盾|人物走形|前后断裂|走向跑偏|文笔", '
            '"where": "第几段或哪一句", "what": "什么毛病"}], '
            '"reader_feel": "一句话说读者感受"}'
        )

        raw = self.llm.complete(prompt, system="你只输出 JSON，不聊天。")
        data = parse_json_block(raw)

        overall = str(data.get("overall", "")).strip().lower()
        if overall not in {"pass", "shaky", "broken"}:
            # 认不出就当中等：不硬说好，也不硬说坏
            overall = "shaky"

        problems: list[dict] = []
        for p in data.get("problems") or []:
            if not isinstance(p, dict):
                continue
            problems.append(
                {
                    "kind": str(p.get("kind", "")).strip(),
                    "where": str(p.get("where", "")).strip(),
                    "what": str(p.get("what", "")).strip(),
                }
            )

        return {
            "chapter": chapter,
            "overall": overall,
            "problems": problems,
            "reader_feel": str(data.get("reader_feel") or ""),
            "book_id": self.book_id,
        }

    # ---------- 工具：全局仲裁 ----------

    def arbitrate(
        self,
        chapter: int,
        text: str,
        brief: str,
        judgments: list[dict],
    ) -> dict:
        """三个裁判判完、这一章要改时，由它裁决并给出改写指令。

        judgments 每条形如：
            {"judge": "world_will", "verdict": "pass",
             "reason": "...", "violations": [...]}

        为什么不让编排层自己拼反馈：三个裁判各说各的，写手拿到会懵 ——
        「世界说 pass，人物说不行，我该听谁的？」
        把几份意见收敛成一条可执行的指令，是仲裁者的活。
        """
        if self.llm is None:
            raise RuntimeError("仲裁要模型：先给 Acceptor(llm=...)")

        lines: list[str] = []
        for j in judgments:
            lines.append(
                f"【{j.get('judge', '?')}】{j.get('verdict', '?')} —— {j.get('reason', '')}"
            )
            for v in j.get("violations") or []:
                lines.append(f"    失分项：{v}")
        block = "\n".join(lines) or "（没有裁决）"

        prompt = (
            "三个审稿人读了同一章，意见在下面。\n\n"
            f"{block}\n\n"
            f"【这一章本来该写什么】\n{brief or '（没给大纲）'}\n\n"
            f"【正文】\n{text}\n\n"
            "你是主编。现在决定这一章怎么办。\n\n"
            "要求：\n"
            "1. 审稿人之间打架时，判断谁更有道理，别和稀泥。\n"
            "2. **rewrite_advice 必须是可执行的改写指令** —— 说清哪一段动、往哪改。"
            "只罗列问题不叫建议，写手拿到还是不会改。\n"
            "3. 如果三个人都对，就 decision=pass，rewrite_advice 留空。\n\n"
            "只输出 JSON：\n"
            '{"decision": "pass 或 rewrite 或 block", '
            '"agrees_with": "world_will 或 trend 或 character 或 none", '
            '"why": "为什么这么判", '
            '"rewrite_advice": "给写手的改写指令，具体到哪一段怎么改"}'
        )

        raw = self.llm.complete(prompt, system="你只输出 JSON，不聊天。")
        data = parse_json_block(raw)

        decision = str(data.get("decision", "")).strip().lower()
        if decision not in {"pass", "rewrite", "block"}:
            # 认不出就让它改 —— 不硬说通过，也不硬毙掉
            decision = "rewrite"

        return {
            "chapter": chapter,
            "decision": decision,
            "agrees_with": str(data.get("agrees_with") or "none").strip(),
            "why": str(data.get("why") or ""),
            "rewrite_advice": str(data.get("rewrite_advice") or ""),
        }

    # ---------- 只读取数（三个库各读一段） ----------

    def _world(self) -> str:
        conn = db.connect("world.db")
        try:
            rules = conn.execute(
                "SELECT title, body FROM world_bible WHERE book_id = ? ORDER BY id",
                (self.book_id,),
            ).fetchall()
            events = conn.execute(
                "SELECT kind, title, summary, constraints FROM events"
                " WHERE book_id = ? ORDER BY id",
                (self.book_id,),
            ).fetchall()
        finally:
            conn.close()

        lines = [f"- {r['title']}：{r['body']}" for r in rules] or ["- （无）"]
        if events:
            lines.append("")
            lines.append("已经发生的事：")
            for e in events:
                lines.append(f"- [{e['kind']}] {e['title']}：{e['summary']}")
                if e["constraints"]:
                    lines.append(f"  锁死了：{e['constraints']}")
        return "\n".join(lines)

    def _characters(self) -> str:
        conn = db.connect("char.db")
        try:
            rows = conn.execute(
                "SELECT name, importance, profile, state FROM characters"
                " WHERE book_id = ? ORDER BY id",
                (self.book_id,),
            ).fetchall()
        finally:
            conn.close()
        return (
            "\n".join(
                f"- {r['name']}（{r['importance']}）：{r['profile']}；"
                f"当前 {r['state'] or '未记'}"
                for r in rows
            )
            or "- （无）"
        )

    def _intent(self) -> str:
        conn = db.connect("trend.db")
        try:
            row = conn.execute(
                "SELECT ending_type, target_score, hard_constraints FROM ending_intent"
                " WHERE book_id = ?",
                (self.book_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return "- （没设结局意图）"
        return (
            f"- 结局类型：{row['ending_type']}，目标分 {row['target_score']}\n"
            f"- 硬约束：{row['hard_constraints'] or '无'}"
        )


def bind(registry, llm=None, book_id: str = "demo") -> None:
    agent = Acceptor(book_id=book_id, llm=llm)
    registry.register("review_article", agent.review)
    registry.register("arbitrate", agent.arbitrate)
