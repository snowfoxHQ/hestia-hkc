# KEE 知识级去重(Knowledge-Level Dedup)

KEE 作为知识身份的唯一权威,实现"知识级去重 + Evidence 合并"。

## 双层去重的分工

| 层 | 在哪 | 指纹 | 视角 | 职责 |
|----|------|------|------|------|
| 事件级 | Crystallizer | light(结构哈希) | 事件视角 | 避免重复消费同一事件,**不做知识决策** |
| 知识级 | **KEE** | canonical(语义规范) | 知识视角 | 知识身份判定,去重权威 |

> Crystallizer is a knowledge candidate generator, not a knowledge identity resolver.
> KEE is the sole authority for knowledge deduplication and evolution.

## canonical fingerprint

`fingerprint.py`:基于 KU 的**知识身份要素**(类型 + 规范化名称 + 领域)。

规范化用 Unicode 类别过滤(只留字母/数字),天然覆盖中英文标点、全角半角、
各种连字符。"价值投资"/"价值投资 "/"价值-投资"/"Value Investing"→ 同指纹。

## 去重流程(KEE `_on_knowledge_created`)

新 KU 创建事件到达 → 对**所有 KU 类型**先做知识级查重:

```
canonical fingerprint 查索引
  命中已有 KU →
      1. 合并 Evidence(sources / supports 并入 canonical KU)
      2. 重定向关系(重复 KU 的关系转移到 canonical,避免悬空)
      3. 软删除重复 KU
      → 不再作为独立 KU 处理
  未命中 → 登记指纹,按类型走后续演化(Claim 走冲突检测等)
```

## GraphStore 新增能力(本步补齐的图存储基础能力)

- `delete_relation(rel_id)`:删除关系(SQLite 表 + NetworkX 图同步)
- `redirect_relations(from, to)`:把一个 KU 的关系重定向到另一个(自环丢弃、去重)

这是图存储本就该有的基础能力,不是为去重打的补丁。

## 索引

指纹索引(canonical_fingerprint → ku_id)由 KEE 的 `KnowledgeDeduplicator` 维护,
**不污染 GraphStore schema**。冷启动可用 `rebuild_index()` 从全量 KU 重建。

## 已知边界

- **Evidence 合并依赖 KDE 充分建立来源关系**:当前去重机制完备(命中时会合并
  sources、重定向关系),但实际合并效果取决于 KDE 在创建 KU 时是否充分建立了
  KU↔Evidence 关系。KDE 的来源-知识关系完善是独立话题,不在本步范围。
- **canonical 用规则规范化,非向量语义**:能覆盖"同一知识不同写法/不同来源"的
  主要去重场景。向量级同义归并(近义表达)是更重的能力,可作后续增强。

## 迁移状态(已完成)

第二步已落地:**KDE 的 `_find_existing` 及四类合并逻辑已全部删除**,KDE 收缩为纯
Producer,KEE 的 `KnowledgeDeduplicator` 在真实流程中实际承担去重。历史迁移要点(留档):

- KEE 对 Claim 的去重在第一步(legacy 合并还在时)从未真实触发——重复 Claim 被
  KDE 在 packager 阶段跳过、不进 `KNOWLEDGE_CREATED` 事件流。删除 legacy 后已在真实
  流程回归确认 KEE 接管正常(`test_kee_dedup.py::test_dedup_merges_provenance_across_sources`)。
- 旧 KDE 的 Entity 合并曾有一处逻辑瑕疵(`item["name"] not in aliases` 成立时 extend
  的是 `item["aliases"]` 而非把 `name` 并入别名)——该逻辑已随 legacy 删除,未搬入 KEE。

## 并发(全系统审核结论)

`KnowledgeDeduplicator` 的指纹索引 `_index` 会被摄入线程与请求线程同时访问,**查重 +
登记必须原子**(否则并发漏去重),已加锁（与 `SQLiteGraphStore` 一致的锁模式：任何跨线程
共享的 SQLite 连接/内存索引都必须加锁）。回归见 `tests/test_concurrency.py`。
