# HKC Crystallizer — 知识结晶器(Integrations Layer)

Memory World → Knowledge World 的**边界转换器**(Boundary Translator)。

把任何"可沉淀认知"事件,转化为 HKC 可接收的知识候选体。

## 定位:它不属于 HKC Core

```
Integrations Layer(边界层)        HKC Core
  ├── Crystallizer          →       KDE → KEE → ACE
  ├── Event Adapters
  └── Protocol Translators
```

Crystallizer 是"进入 HKC 的数据净化入口",不是知识系统本体。它处理的不是
"记忆",而是把**任何可沉淀认知**转化为知识候选体——所以 HKC 永不被任何单一上游系统绑定死。

## 架构铁律

> **Crystallizer is a knowledge candidate generator, not a knowledge identity resolver.**
> **KEE is the sole authority for knowledge deduplication and evolution.**

Crystallizer 永不查询 KU 是否存在,永不做合并/更新决策。
知识身份(是否同一知识、是否合并)的最终解释权,只属于 KEE。

## 数据流

```
事件(memory.matured / meeting.finished / research.finished / document.ingested …)
  → KnowledgeEventSource(Protocol，按 event_name 订阅,不绑"记忆")
  → KnowledgeCrystallizer(只做 4 件事)
        1. 接收事件   2. 筛选   3. 事件级去重   4. 构造 KnowledgeCandidate
  → KnowledgeCandidate { content, evidence[], event_refs[], light_fingerprint }
  → KnowledgeIngress(Protocol，HKC 对外入口契约)
  → HKCIngress(适配器,内部调 KDE)
  → KDE → KEE(知识级去重在这) → ACE
```

## 用法

```python
from hkc_sdk import connect
from hkc_crystallizer import KnowledgeCrystallizer, HKCIngress, SystemBusEventSource

hkc = connect(container=hkc_container)

crystallizer = KnowledgeCrystallizer(
    ingress = HKCIngress(hkc),                  # 只认 KnowledgeIngress 协议
    source  = SystemBusEventSource(system_bus), # 只认 KnowledgeEventSource 协议
    events  = ["memory.matured"],
)

print(crystallizer.get_stats())
```

## 两层去重的职责划分

| 层 | 在哪 | 做什么 |
|----|------|--------|
| 事件级去重 | **Crystallizer** | 用 event_refs / source_id 避免重复消费同一事件 |
| 知识级去重 | **KEE**(已实现) | canonical fingerprint + KU 查重,命中则合并 Evidence 而非新建 |

Crystallizer 只计算 **light fingerprint**(结构哈希,事件视角的粗去重标签),
**不**参与"是否已存在、是否合并"的知识决策。语义层面的归并(canonical
fingerprint)由 KEE 负责——那是知识身份问题,只有 KEE 有解释权。

## 结构化 Evidence(可追溯)

候选体携带结构化 `CandidateEvidence`(而非字符串拼接的 source):

```python
CandidateEvidence(
    evidence_type = "memory",      # memory | meeting | research | document ...
    source_id     = "MEM_001",
    agent         = "research_agent",
    confidence    = 0.92,
    timestamp     = "...",
)
```

未来进图谱后形成 `KU ← Evidence ← Memory` 链,可回答"为什么系统认为用户长期
关注 Agent 架构"——顺着链路回溯到具体记忆。(Evidence 入图谱由后续 KEE 步骤完成。)

## 扩展新事件类型

只需注册一个翻译函数,不改 Crystallizer:

```python
def translate_meeting_finished(payload):
    return KnowledgeCandidate(
        content=payload["summary"], title=payload["topic"],
        evidence=[CandidateEvidence(evidence_type="meeting",
                                    source_id=payload["meeting_id"])],
        event_refs=[payload["meeting_id"]])

crystallizer = KnowledgeCrystallizer(
    ingress=HKCIngress(hkc), source=SystemBusEventSource(bus),
    events=["memory.matured", "meeting.finished"],
    translators={"meeting.finished": translate_meeting_finished})
```

## 可注入扩展点

| 参数 | 作用 |
|------|------|
| `ingress` | KnowledgeIngress 实现(HKCIngress / 自定义) |
| `source` | KnowledgeEventSource 实现(SystemBusEventSource / 自定义) |
| `events` | 订阅哪些事件 |
| `translators` | 事件名 → 翻译函数(扩展新事件类型) |
| `candidate_filter` | `(KnowledgeCandidate)->bool` 二次筛选 |
