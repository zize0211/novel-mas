"""一致性判官的校准：拿人工标的样本考它。

跑法：
    cd E:\novel-mas
    $env:PYTHONPATH='E:\novel-mas\src'
    python tests\calibrate_consistency.py

会调用真模型（花钱）。所以不放进 selfcheck —— selfcheck 必须零成本、
不联网、每次都能跑。这个脚本是手动跑的，跑一次算一次账。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novel_mas.agents.world_will import WorldWill
from novel_mas.llm import OpenAIProvider

CASES = Path(__file__).resolve().parent / "calibration" / "consistency_cases.json"

# 判定线：三档严格吻合率低于这个，判官不许上线。
# 为什么是 85%：剩 15% 意味着每七章有一章被判错，而判错的方向可能是不拦。
THRESHOLD = 0.85


def main() -> int:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    judge = WorldWill(llm=OpenAIProvider.from_env())

    strict_hits = 0
    gate_hits = 0          # 二档：只问「该拦的拦住了吗」和「不该拦的放过了吗」
    wrong: list[tuple[dict, str, str]] = []

    for i, c in enumerate(cases, 1):
        judgment = judge.check_consistency(c["claim"])
        got = judgment.verdict.value
        expect = c["expect"]

        ok = got == expect
        strict_hits += ok

        # 二档口径：hard_conflict 才算「拦」，pass / soft_warn 都算「放」
        gate_ok = (got == "hard_conflict") == (expect == "hard_conflict")
        gate_hits += gate_ok

        mark = "ok  " if ok else "XX  "
        print(f"{mark}{i:2d}. 期望 {expect:<14s} 实得 {got:<14s} {c['claim'][:26]}")
        if not ok:
            wrong.append((c, got, judgment.reason))

    n = len(cases)
    strict_rate = strict_hits / n
    gate_rate = gate_hits / n

    print()
    print(f"三档准确率      : {strict_hits}/{n} = {strict_rate:.1%}")
    print(f"二档（拦/不拦） : {gate_hits}/{n} = {gate_rate:.1%}")
    print(f"共用 token      : {judge.llm.tokens_used}")

    if wrong:
        print()
        print("=== 判错的 ===")
        for c, got, reason in wrong:
            print(f"  期望 {c['expect']} / 实得 {got}")
            print(f"    句子: {c['claim']}")
            print(f"    模型理由: {reason}")
            print(f"    人标的理由: {c['why']}")

    print()
    if strict_rate < THRESHOLD:
        print(f"没到线：{strict_rate:.1%} < {THRESHOLD:.0%}。这个判官现在不能上线。")
        return 1
    print(f"过线了：{strict_rate:.1%} >= {THRESHOLD:.0%}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
