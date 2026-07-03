# HKC Python SDK

把 HKC 的能力封装成干净的 Python 接口。一套接口，两种后端：远程 HTTP 或进程内直连。

## 安装

```bash
# SDK 随主包一起安装（HTTP 客户端依赖的 requests 已包含在依赖里）
pip install -e ".[all]"
```

## 快速开始

```python
from hkc_sdk import connect

# 方式一：远程 HTTP（HKC 作为独立服务部署）
hkc = connect("http://localhost:8000")

# 方式二：进程内直连（Agent 与 HKC 同进程，零网络开销）
from hkc_api.container import HKCContainer
container = HKCContainer(data_dir="./hkc_data", embedding_kind="tei",
                        embedding_url="http://localhost:8080")
hkc = connect(container=container)

# —— 之后的用法完全一样 ——
hkc.ingest_text("巴菲特认为价值投资长期跑赢市场", domain="Investment")
hits = hkc.search("价值投资", mode="hybrid", top_k=5)
for h in hits:
    print(h.name, h.score)
```

## 核心方法

### 知识摄入

```python
result = hkc.ingest_text(text, source_title="书名", domain="Investment")
result = hkc.ingest_url("https://...", domain="Investment")
print(result.ku_count, result.counts)   # 写入多少 KU，各类型多少
```

### 查询与搜索

```python
ku = hkc.get_ku("ENT_00000001")          # 单个 KU
kus = hkc.list_by_domain("Investment")   # 领域内全部
hits = hkc.search("回测", mode="bm25")    # bm25/vector/graph/hybrid
nbrs = hkc.neighbors("CON_00000001")     # 图谱邻居
path = hkc.find_path("ENT_001", "CON_009")  # 最短路径
```

### 能力（ACE）

```python
# 查看还差哪些知识
report = hkc.coverage_report("quant_analyst")
print(report.can_compile, report.missing_skills)

# 编译（覆盖度不足抛 HKCInsufficientCoverage）
ability = hkc.compile_ability("quant_analyst")

# 获取已编译的（未编译抛 HKCNotFoundError）
ability = hkc.get_ability("quant_analyst")

# 便捷：有就拿，没有就编译
ability = hkc.ensure_ability("quant_analyst")

# Agent 用某个 Skill 的知识上下文
skill = ability.skill_context("backtesting")
print(skill.coverage, skill.concept_hits)
```

### 冲突（KEE）

```python
conflicts = hkc.list_conflicts(status="open")
for c in conflicts:
    print(c.claim_a_id, "vs", c.claim_b_id)

# 人工裁决（失败返回 False，不抛异常）
ok = hkc.resolve_conflict(c.conflict_id, winner_id=c.claim_b_id,
                          note="新研究支持")
```

## 异常处理

```python
from hkc_sdk import (
    HKCError, HKCNotFoundError,
    HKCInsufficientCoverage, HKCBadRequest, HKCServerError,
)

try:
    ability = hkc.compile_ability("quant_analyst")
except HKCInsufficientCoverage as e:
    print("还缺这些技能:", e.missing_skills)
    print("当前覆盖度:", e.coverage)
except HKCNotFoundError:
    print("未知能力")
```

异常体系：
- `HKCError` — 基类
- `HKCNotFoundError` — 资源不存在（KU / 未编译能力）
- `HKCInsufficientCoverage` — 覆盖度不足，带 `missing_skills` / `coverage`
- `HKCBadRequest` — 参数错误 / 请求体过大
- `HKCServerError` — 服务错误 / 连接失败

## 给 Agent 用

典型场景：Finance Agent 不读原始书籍，直接调用编译好的能力。

```python
from hkc_sdk import connect

hkc = connect(container=shared_container)   # 与 HKC 同进程

class FinanceAgent:
    def analyze(self, query):
        # 拿到量化分析能力（含知识引用、技能、工作流）
        ability = hkc.ensure_ability("quant_analyst")
        # 检索相关知识
        knowledge = hkc.search(query, domain="Investment", top_k=10)
        # ... 用 ability + knowledge 完成任务
        return ...
```

## 两种后端怎么选

| | HTTPClient | DirectClient |
|---|---|---|
| 场景 | HKC 独立部署，跨进程/跨机器 | Agent 与 HKC 同进程 |
| 开销 | 有网络序列化 | 零开销，直接调引擎 |
| 依赖 | `requests` | 需要 `HKCContainer` 实例 |
| 接口 | 完全一致 | 完全一致 |

两者实现同一个 `BaseClient` 抽象接口，所有方法签名、返回类型、异常行为都经过同一组契约测试验证，可无感切换。
