# 实验：MAS vs SAS —— 基础把控

## 目的

证明 **MAS 对小说的基础把控更严格**。

**注意这个目的的范围**：

- ✅ 主张的是「**少犯基础错误**」——设定矛盾、人物走形、前后断裂、走向跑偏
- ❌ **不主张**「写得更好」。文笔好不好要双盲人读，本项目没做

「严格」在这里是**结果**，不是态度：

| 说法 | 意思 | 本实验支持吗 |
|---|---|---|
| 查得严 | 标准更细，抓得更多 | 部分（见「A 部分」） |
| **写得干净** | 产出错更少 | ✅ 主结论 |

## 设计

```
输入：两组完全一样（同一个库、同一个大纲、同一段文风、同一个模型）
唯一变量：怎么生成
```

| | MAS | SAS |
|---|---|---|
| 生成方式 | 走完整链路 | 一次成型 |
| 写手看到的 prompt | `orchestrator.base_prompt` | `sas_baseline.build_prompt` |
| 写完过闸 | 世界 / 走向 / 人物 | ❌ 没有 |
| 能重写 | 有（打回 → 仲裁 → 改写） | ❌ 一次 |

**两段 prompt 是照着抄的**。改一边记得改另一边，不然比的不是一回事。

## 指标

### 一、token 消耗（完全不依赖人）

```
WriteResult.cost_by_role = {
    "writer":     ?,   # 写正文
    "world_will": ?,   # 过闸
    "trend":      ?,   # 打分
    "character":  ?,   # 评人物
    "acceptor":   ?,   # 仲裁（没触发就是 0）
}
```

**分账比总数有用**，因为它回答的是：

> 多花的钱，是花在**写得更好**上，还是花在**发现写坏了**上？

### 二、成文质量（同一把尺子）

`tests/audit_chapter.py` 审任意正文，报「硬问题 N 条 / 软问题 M 条」。

```
MAS 正文 → 四个裁判 → N
SAS 正文 → 同一套裁判 → M
```

**必须用同一套裁判**。分开审、各自审对方，比不了（审查力度不同，见下）。

## 已知局限（必须写进报告）

1. **检查器同源**。尺子也是 MAS 的一部分。用我的裁判审我的正文，可能有偏袒。
   - 缓解：检查器的标准是固定的（写死的 prompt + 22 条人工标注校准集，准确率 81.8%）
   - **未缓解**：没验证它对「我自己写的」有没有偏袒

2. **样本量小**。一章说明不了什么。**至少 3 章**才勉强能看。

3. **检查器本身没过线**。三档 81.8%（线是 85%），二档 90.9%。
   所以「问题数」这个数字本身有噪声。

4. **没有人工盲评**。所以「文笔」一栏是空的，不是「打平」。

## 怎么跑

```powershell
cd E:\novel-mas
$env:PYTHONPATH = 'E:\novel-mas\src'
$env:PYTHONIOENCODING = 'utf-8'

# 1. MAS 跑一章（会花钱）
python -c "from novel_mas.llm import OpenAIProvider; from novel_mas.orchestrator import Orchestrator; orc = Orchestrator(book_id='demo', llm=OpenAIProvider.from_env()); r = orc.write_chapter(1, '李三在巷口捡到一枚铜钱'); print(r.brief()); print('分账', r.cost_by_role)"

# 2. SAS 跑同一章（同样的 brief 和 style）
python tests\sas_baseline.py --book demo --chapter 1 --brief "李三在巷口捡到一枚铜钱" --style 日常

# 3. 同一把尺子审两遍
python tests\audit_chapter.py --book demo --chapter 1 --file data\out\ch01.txt
python tests\audit_chapter.py --book demo --chapter 1 --file data\out\ch01.sas.txt
```

**第 2 步之前可以先 dry-run**，核对两段 prompt 是不是一样：

```powershell
python tests\sas_baseline.py --book demo --chapter 1 --brief "..." --dry-run
```

## 结果

**还没跑。** 跑完把这张表填上：

| 章 | 组 | 正文长度 | token | 其中写作 | 其中审查 | 硬问题 | 软问题 |
|---|---|---|---|---|---|---|---|
| 1 | MAS | — | — | — | — | — | — |
| 1 | SAS | — | — | — | — | — | — |

## 报告里能写什么 / 不能写什么

| 能写 | 依据 |
|---|---|
| 「同一套检查器下，MAS 的正文被指出的硬问题更少 / 一样多」 | 数出来的 |
| 「MAS 多花了 X 倍 token，其中写作占 Y%、审查占 Z%」 | 分账 |
| 「检查器对两组的严格程度假设相同，这一条未验证」 | 诚实的局限 |

| **不能写** | 为什么 |
|---|---|
| 「MAS 写得更好」 | 没做盲评 |
| 「判官是准的」 | 校准集没过 85% 线 |
| 「多花的钱值得」 | 没算成本-收益 |
