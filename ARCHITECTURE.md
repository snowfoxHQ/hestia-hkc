# HKC 架构原则 (Architecture Principles)

HKC(Hestia Knowledge Core)是一个 AI 原生的知识层。本文件记录其不可妥协的
架构原则——它们约束所有模块的职责边界,优先级高于一时的实现便利。

## 三引擎职责

```
KDE = Producer    知识生产者:抽取(Extract)→ 写入 → 发事件。仅此而已。
KEE = Authority   知识权威:身份、合并、冲突、演化的唯一裁决者。
ACE = Consumer    知识消费者:把稳定知识编译成能力(Compile)。
```

---

## Principle 07 — Single Knowledge Authority(知识主权唯一)

> 任何涉及 **Identity(身份)/ Merge(合并)/ Conflict(冲突)/
> Canonicalization(规范化)/ Confidence Evolution(置信度演化)/
> Evidence Aggregation(证据聚合)** 的逻辑,**只能存在于 KEE**。

### 禁止清单

- **KDE 禁止**:知识身份判断、知识合并、冲突处理、置信度调整。
  KDE 只产出 KU 候选并写入,是否"已存在/重复/该合并/有冲突"一概不判断。
- **ACE 禁止**:修改知识身份。ACE 只读取稳定知识,不改写。

### 为什么(而非按时间窗口划分)

一个常见的错误折中是按"批次内 / 批次外"分权:KDE 管同一次 ingest 内的去重,
KEE 管跨批次。这是**按时间窗口划分,不是按职责划分**,必然导致主权分裂:

> 同一次 ingest 里有 A.pdf 和 B.pdf,KDE 就地判定二者知识相同并合并 ——
> KEE 根本没看到这次合并。于是"知识身份"的一部分永久地在 KDE 发生,无人知晓。
> 最终没人说得清谁是真正的权威。

知识身份必须有**唯一源头**——正如任何可信系统都要求真相有唯一权威来源。

### 漂移风险

若放任 KDE 做知识判断,它会从 `_find_existing()` 一步步长出
`_find_existing_concept()`、`_find_existing_entity()` …… 最终变成"半个 KEE"。
这是典型的架构漂移。趁早立界,成本最低。

### 迁移状态(2 步法 —— 已全部完成)

KDE 历史上对 Entity/Concept/Fact/Claim 四类都做了 `_find_existing` 合并/去重,
违反本原则。迁移分两步完成,现已收尾:

- **第一步(已完成)**:曾给 KDE 的旧合并逻辑打 `DEPRECATED` 标记 + 警告日志 +
  触发计数(`KUPackager.legacy_merge_count`),并验证(人为禁用 KDE 合并后)
  KEE 的 `KnowledgeDeduplicator` 对四类 KU 都能接管(去重 + Evidence 汇聚)。

- **第二步(已完成)**:已**删除** `_find_existing` 及 Entity/Concept/Fact/Claim
  四处合并逻辑与 `legacy_merge_count`,KDE 收缩为**纯 Producer**。现在 KEE 的
  `KnowledgeDeduplicator`(订阅 `knowledge.created`)是知识身份/去重/合并的唯一权威。
  删除前观测:同份重复内容 legacy ON vs OFF 最终 KU 数完全一致(都 7 KU、无重复),
  证明 KEE 等价接管——**判据不是"count 归零",而是"删除后去重行为不变"**,已回归验证
  (`test_kee_dedup.py` / `test_kde.py` 的 `test_packager_does_not_dedup` 等)。

---

## 其他既定原则(摘要)

- **双层去重分工**:event 级去重在 Crystallizer(light fingerprint,仅标签);
  knowledge 级去重在 KEE(canonical fingerprint,权威)。
  详见 `hkc-crystallizer/README.md` 与 `hkc-kee/DEDUP.md`。
- **接口隔离**:GraphStore / EmbeddingBackend / SDK 后端均通过抽象接口可替换。
- **Integrations vs Core**:Crystallizer 属集成层(边界转换器),非 Core。
  Core 的演化不应被集成层牵动,反之亦然。
- **离线优先**:CDN、外部模型下载、网络假设都要有本地降级路径
  (UI 的 Three.js 本地 vendor 打包、embedding 缺失时降级 stub、search 的 stub 后端)。
