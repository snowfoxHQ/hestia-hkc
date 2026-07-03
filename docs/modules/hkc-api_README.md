# HKC API 使用指南

REST API 把 HKC 的四大引擎暴露成 HTTP 接口：KDE 摄入、KEE 演化、ACE 编译、Search 检索。

## 启动

```bash
# 装依赖（建议在虚拟环境里；需 Python ≥ 3.11）
pip install -e ".[all]"

# 启动（激活 venv 后统一用 python -m uvicorn，不依赖 PATH）
python -m uvicorn hkc_api.main:app --host 127.0.0.1 --port 8000
```

访问 `http://localhost:8000/docs` 查看自动生成的交互式 API 文档（Swagger UI）。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HKC_DATA_DIR` | `./hkc_data` | 数据目录（SQLite、向量索引、能力包） |
| `HKC_EMBEDDING` | `stub` | 向量后端：`stub` / `local` / `tei` |
| `HKC_EMBEDDING_URL` | `http://localhost:8080` | TEI 服务地址（embedding=tei 时） |
| `HKC_LLM_PROVIDER` | `anthropic` | LLM 提供商：`anthropic` / `deepseek` / `openai` / `openai-compatible`（本地/自定义） |
| `HKC_LLM_API_KEY` | — | 通用 key；未设则按 provider 回退读 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` |
| `HKC_LLM_MODEL` / `HKC_LLM_BASE_URL` | 按 provider | 模型名 / 自定义 API 地址（本地模型、代理时） |
| `HKC_API_KEY` | — | 设了则所有请求需带 `X-API-Key` 头（可选鉴权）；未设=开放 |
| `HKC_HOST` | `127.0.0.1` | 监听地址 |
| `HKC_PORT` | `8000` | 监听端口 |

## 端点总览

### 系统

| Method | Path | 说明 |
|--------|------|------|
| GET | `/` | 服务信息 |
| GET | `/health` | 健康检查 |
| GET | `/stats` | 系统统计（KU 数量、索引大小、能力列表、**`data_dir` 实际数据目录**） |
| GET | `/events?limit=20` | 最近的 KEP 事件 |

### 知识摄入与查询

| Method | Path | 说明 |
|--------|------|------|
| POST | `/knowledge/ingest/text` | 摄入文本，触发完整 pipeline |
| POST | `/knowledge/ingest/url` | 摄入网页 |
| POST | `/knowledge/ingest/file` | 上传文件同步摄入（.pdf/.md/.txt/.epub/.mobi/.azw3） |
| POST | `/knowledge/ingest/file/async` | 上传文件**异步**摄入，秒返回 `job_id`，后台线程池跑 |
| GET | `/knowledge/ingest/jobs/{job_id}` | 轮询异步摄入进度（processed/total/status/ku_count） |
| GET | `/knowledge/graph?limit=` | 一次拉全量图（所有 KU + 关系 + stats），前端 3D 星球用；**剥离 source_text** 瘦身 |
| GET | `/knowledge/ku/{ku_id}` | 获取单个 KU（含 source_text 原文段落） |
| GET | `/knowledge/ku/{ku_id}/neighbors` | KU 的图谱邻居 |
| GET | `/knowledge/ku/{ku_id}/synthesis` | 读该节点的**已缓存** AI 综述（不触发生成/不调 LLM） |
| POST | `/knowledge/ku/{ku_id}/synthesis?force=` | 生成（或返回缓存）AI 综述；派生只读视图，缓存独立 `synthesis.db`，不写知识图 |
| GET | `/knowledge/domain/{domain}` | 列出领域内所有 KU |
| POST | `/knowledge/reset` | 清空整个知识库（不可逆），重建索引。演示/重来用 |

### 搜索

| Method | Path | 说明 |
|--------|------|------|
| POST | `/search` | 统一搜索（bm25/vector/graph/hybrid） |
| POST | `/search/neighbors` | 图谱邻居展开 |
| GET | `/search/path?from_id=&to_id=` | 两 KU 间最短路径 |

### 能力（ACE）

| Method | Path | 说明 |
|--------|------|------|
| GET | `/abilities` | 列出所有可编译能力 |
| GET | `/abilities/{key}/coverage` | 查看覆盖度（不编译） |
| POST | `/abilities/{key}/compile` | 编译能力包 |
| GET | `/abilities/{key}` | 加载已编译能力 |

### 冲突（KEE）

| Method | Path | 说明 |
|--------|------|------|
| GET | `/conflicts?status=open` | 列出冲突卡 |
| POST | `/conflicts/{id}/resolve` | 人工裁决 |

### 运行时 LLM 配置（模型无关）

| Method | Path | 说明 |
|--------|------|------|
| GET | `/config/llm` | 读当前 LLM 配置（key 脱敏） |
| POST | `/config/llm` | 运行时切换 provider/key/model/base_url，**立即生效不重启**。`openai-compatible` 必须带 `base_url` |

## 典型流程

```bash
# 1. 摄入知识
curl -X POST http://localhost:8000/knowledge/ingest/text \
  -H "Content-Type: application/json" \
  -d '{"text":"巴菲特认为价值投资长期跑赢市场...","domain":"Investment","source_title":"投资笔记"}'

# 2. 搜索
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"价值投资","mode":"hybrid","top_k":5}'

# 3. 查看某能力还差哪些知识
curl http://localhost:8000/abilities/quant_analyst/coverage

# 4. 编译能力
curl -X POST http://localhost:8000/abilities/quant_analyst/compile

# 5. 查看冲突
curl http://localhost:8000/conflicts?status=open

# 6. 裁决冲突
curl -X POST http://localhost:8000/conflicts/CFT_00000001/resolve \
  -H "Content-Type: application/json" \
  -d '{"winner_id":"CLM_00000005","note":"更新研究支持"}'
```

## HTTP 状态码约定

| 码 | 含义 |
|----|------|
| 200 | 成功 |
| 400 | 请求参数错误（如无效 search mode） |
| 404 | 资源不存在（KU / 未知 Ability / 未编译 Ability） |
| 422 | 知识覆盖度不足，无法编译能力（返回 missing_skills） |
| 500 | 摄入/处理内部错误 |

## 架构说明

API 层通过 `HKCContainer` 单例装配所有组件，按依赖顺序：
IDGenerator → GraphStore → EventBus → KEE → ACE → KDE → Search。

KEE 在构造时自动订阅 `knowledge.created` 事件，所以每次摄入后冲突检测自动运行。
搜索索引也订阅了该事件，新 KU 写入后增量更新 BM25 和向量索引，无需手动重建。

向量后端通过 `HKC_EMBEDDING` 切换，生产环境推荐 `tei`（配合 Text Embeddings Inference 服务），
详见 `hkc-search/EMBEDDING_BACKENDS.md`。

## v1 已知限制（生产部署前需评估）

审核中识别、按 v1 边界保留的项：

- **CORS 已配置**（`main.py` 的 `CORSMiddleware`）：默认放开所有来源（本地工具），部署到受控网络可用 `HKC_CORS_ORIGINS`（逗号分隔）收紧。
- **多 worker 索引不共享**：uvicorn 多 worker 时每进程独立持有 BM25/向量内存索引，且各自连接同一 SQLite（WAL 支持并发写）。v1 建议单 worker；多 worker 需要把索引外置（如独立向量服务）。
- **搜索过滤 N+1**：带 `domain`/`ku_types`/状态过滤时逐条回查 GraphStore。`top_k` 通常较小，影响有限；大规模检索需引入批量查询。
- **build_index 启动容错**：装配时 `build_index` 异常被降级为 warning，向量后端真实故障时服务仍会启动（搜索失效但不阻断）。生产可改为 fail-fast。

## 已修复（v1.8 审核）

- ingest 端点增加 2MB 文本上限（超出返回 413），防止 OOM。
- ingest 路由不再误吞内部 `HTTPException`（先 re-raise 再兜底 500）。
- 搜索默认排除 `superseded/rejected/deleted/disputed` 状态的 KU（`exclude_inactive=True`），避免检索到已被 KEE 废弃的知识。
