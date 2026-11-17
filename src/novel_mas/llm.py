"""模型接口 —— 把 LLM 抽成一个可替换的 Provider。

为什么要抽：
    最小闭环要能【不花钱、可重复】地跑。所以先给一个假模型，
    等真模型接进来，只换 Provider，编排那七步一行不用改。

这个文件里有两个东西：
    Provider       协议（谁想当模型，就实现 complete 和 tokens_used）
    FakeProvider   确定性的假模型：同样的输入永远给同样的输出
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    """一个模型只需要能干两件事。"""

    def complete(self, prompt: str, system: str = "") -> str: ...

    @property
    def tokens_used(self) -> int: ...


@dataclass
class FakeProvider:
    """假模型：不联网、不花钱、结果稳定。

    它不"理解"任何东西，只按 prompt 里的关键词从模板里挑一段。
    作用是让整条链路能走通，好让接口和数据结构先被真实调用一遍。
    """

    style: str = "日常"
    _tokens: int = field(default=0, repr=False)

    POSITIVE = ("重逢", "温暖", "一起", "笑", "归来", "灯火")
    NEGATIVE = ("死", "别离", "毁", "坑", "血", "断")

    def complete(self, prompt: str, system: str = "") -> str:
        self._tokens += max(1, (len(prompt) + len(system)) // 2)

        seed = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:6]
        tone = "暖" if sum(prompt.count(w) for w in self.POSITIVE) >= sum(
            prompt.count(w) for w in self.NEGATIVE
        ) else "冷"

        body = (
            f"【{self.style}｜{tone}｜seed {seed}】\n"
            f"夜里风从巷口过来，把晾着的布吹得翻了个面。"
            f"李三把那枚铜钱捏在手里，边角硌着掌心，像有人轻轻按了一下。"
            f"他没有立刻收起来，先抬头看了看天——云走得比平时慢。\n"
            f"（这是一段占位正文：链路跑通用的，不是真作品。）"
        )
        return body

    @property
    def tokens_used(self) -> int:
        return self._tokens


@dataclass
class EchoProvider:
    """更笨的假模型：只回显 prompt 的前 200 字，用来排查拼串问题。"""

    _tokens: int = field(default=0, repr=False)

    def complete(self, prompt: str, system: str = "") -> str:
        self._tokens += len(prompt) // 4
        return f"[ECHO] {prompt[:200]}"

    @property
    def tokens_used(self) -> int:
        return self._tokens
