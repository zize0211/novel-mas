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
import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable


@contextmanager
def judge_mode(llm, temperature: float = 0.1) -> Iterator[None]:
    """把 llm 切到「判官温度」，出块时恢复。

    为什么要有它：写作和判官要的不是同一个温度 ——
    写正文要 0.8（有起伏，不然每章一个调子）；
    当尺子要 0.1（量具要稳，抖一下结论就翻了）。

    实测（2026-11-18，同一份 1266 字正文，各打三次）：
        T=0.1   64 / 64 / 64   极差 0    ← 连三个视角的分都一样
        T=0.8   60 / 61 / 70   极差 10
        T=0.95  55 / 60 / 65   极差 10
    也就是说，之前那次「跑章时 72 分、审计时 55 分」不是判得不一样，
    是同一把尺子在抖 —— 而阈值 60 正好落在抖动区间里。

    没有 temperature 属性的 provider（假模型、自定义）静默跳过，不报错。
    """
    if not hasattr(llm, "temperature"):
        yield
        return
    old = llm.temperature
    llm.temperature = temperature
    try:
        yield
    finally:
        llm.temperature = old


@contextmanager
def metered(cost: dict[str, int], label: str, llm) -> Iterator[None]:
    """记一段调用的 token 开销，按角色分开算。

    用法：
        with metered(cost, "world_will", llm):
            verdict = reg.call("world_will", "check_consistency", ...)

    为什么需要它：tokens_used 只有一个总数，说不清是哪个角色花的。
    而「多花的钱是花在写作上，还是花在审查上」正是要回答的问题 ——
    只报一个 3495，等于什么都没说。

    finally 里记：即使中途抛异常，花掉的钱也算数。
    """
    before = getattr(llm, "tokens_used", 0)
    try:
        yield
    finally:
        after = getattr(llm, "tokens_used", 0)
        cost[label] = cost.get(label, 0) + (after - before)


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

        # 假模型也要认得「要 JSON」的请求，否则判官工具在假模型下直接炸，
        # 「用假模型也能跑通」这条设计原则就断了。
        # 返回的每个值都标「假模型」，一眼看得出这不是判断结果。
        if "只输出 JSON" in system or "只输出 JSON" in prompt:
            # 它不会推理，只做字符串匹配 —— 够让闭环演示「拦 / 不拦」两条路。
            # 真判断在 OpenAIProvider 那边；跑真模型时这段完全不参与。
            lead = prompt.split("【待判剧情】")[-1] if "【待判剧情】" in prompt else ""
            dies = ("死" in lead) and ("没有死" not in lead)
            return json.dumps(
                {
                    "verdict": "hard_conflict" if dies else "pass",
                    "reason": (
                        "[假模型] 待判段落里匹配到「死」，且不是否定句"
                        if dies
                        else "[假模型] 没匹配到违禁词，放行"
                    ),
                    "score": 60,
                    "events": [],
                    "findings": [],
                    "self": {"score": 60, "reason": "[假模型]"},
                    "others": {"score": 60, "reason": "[假模型]"},
                    "reader": {"score": 60, "reason": "[假模型]"},
                    "violations": [],
                    "state": "[假模型] 本章末状态占位",
                    "constraints": [],
                    "ending_type": "happy",
                    "target_score": 60,
                    "hard_constraints": [],
                    "soft_preferences": [],
                    "points": [],
                    # acceptor.arbitrate 要的字段
                    "decision": "rewrite",
                    "agrees_with": "none",
                    "why": "[假模型] 不做仲裁，一律让它改",
                    "rewrite_advice": "[假模型] 这是占位改写指令，真模型才会给具体的内容",
                    "overall": "shaky",
                },
                ensure_ascii=False,
            )

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


def parse_json_block(text: str) -> dict:
    """从模型回复里抠 JSON。容错：带 ```json 围栏、前后夹废话、连着吐两个对象。

    放在 llm 模块里，因为「模型输出不干净」是通用问题，不只世界意志会遇到。

    ⚠ 别用「找第一个 { 到最后一个 }」那种办法 —— 模型偶尔会把两个对象
    连着吐出来（比如先给一个 problems 数组再给一个对象），那样切出来的
    是两段 JSON 拼在一起，json.loads 直接报 Extra data。
    raw_decode 只吃第一个完整 JSON，后面的不管。
    """
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[:-3]

    i = s.find("{")
    if i < 0:
        raise ValueError(f"模型没给 JSON：{text[:200]}")

    try:
        obj, _ = json.JSONDecoder().raw_decode(s[i:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型给的 JSON 坏了（{exc}）：{text[:300]}") from exc

    if not isinstance(obj, dict):
        raise ValueError(f"模型给的不是对象：{text[:200]}")
    return obj


def _load_dotenv(path: str = ".env") -> None:
    """把 .env 读进 os.environ。没有这个文件就静默跳过。

    为什么自己写：装 python-dotenv 就破坏零依赖。
    只支持最简单的 KEY=VALUE——多行、转义那些复杂格式不该塞进 key 文件。
    """
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # setdefault：已经在环境变量里的优先，.env 不覆盖它
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


@dataclass
class OpenAIProvider:
    """真模型：按 OpenAI 的 /v1/chat/completions 规范接。

    为什么不用 openai sdk：项目现在是 dependencies = []，零依赖，装起来不用等。
    标准库 urllib 够用——一次 POST，读 JSON，取 content。

    兼容面：任何讲这套协议的服务都能接（OpenAI / DeepSeek / 本地 vLLM / Ollama 的
    OpenAI 兼容端点），换的只是 base_url 和 model。
    """

    api_key: str = field(default="", repr=False)   # repr 里不显示，免得日志泄露
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    temperature: float = 0.8
    timeout: int = 120
    _tokens: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("api_key 不能为空；用 OpenAIProvider.from_env() 从环境变量取")

    @classmethod
    def from_env(cls) -> "OpenAIProvider":
        """从环境变量建。key 不进代码、不进 git。"""
        _load_dotenv()          # 先把 .env 捞进来，再读环境变量
        key = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or ""
        ).strip()
        if not key:
            raise RuntimeError(
                "没找到 API key：先设 OPENAI_API_KEY 或 DEEPSEEK_API_KEY"
            )
        return cls(
            api_key=key,
            model=os.environ.get("NOVEL_MODEL", "deepseek-chat"),
            base_url=os.environ.get("NOVEL_BASE_URL", "https://api.deepseek.com/v1"),
        )

    def complete(self, prompt: str, system: str = "") -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        # 瞬时网络抖动（笔记本休眠唤醒后 DNS 失效、Wi-Fi 刚切换）很常见，
        # 重试一次基本就过 —— 别让一次 getaddrinfo 失败废掉整章。
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:400]
                raise RuntimeError(f"模型返回 HTTP {exc.code}：{body}") from exc
            except urllib.error.URLError as exc:
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                reason = exc.reason
                detail = str(reason)
                if isinstance(reason, OSError) and "11001" in detail:
                    detail += "（域名解析失败：多半是网络刚切换或刚休眠唤醒，等两秒再点一次）"
                raise RuntimeError(f"连不上模型 {self.base_url}：{detail}") from exc

        usage = data.get("usage") or {}
        self._tokens += int(usage.get("total_tokens") or 0)
        return data["choices"][0]["message"]["content"]

    @property
    def tokens_used(self) -> int:
        return self._tokens
