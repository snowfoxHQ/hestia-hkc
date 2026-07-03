# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。Python 包版本见 `pyproject.toml`；前端打包号见界面顶栏 `#app-ver`。

## [Unreleased]

### Added
- **网页 URL 摄入接入界面**：摄入弹窗新增网址输入框，前端直接调 `POST /knowledge/ingest/url`。
- **高级检索界面**：两节点间最短路径 / 邻居展开（`/search/path`、`/search/neighbors`）。
- **Crystallizer 接入运行时**：装配进容器 + `POST /crystallize` 端点，外部系统可推送知识候选。
- **可选 API 鉴权**：设 `HKC_API_KEY` 后所有写操作需带该 key；未设则开放（本地默认）。
- **数据/后端可见性**：界面显示当前数据目录、全库 KU 数、活跃 embedding 后端。
- **GitHub Actions CI**：每个 PR 自动跑全量测试（Python 3.11 / 3.12）。
- 贡献指南 `CONTRIBUTING.md`、本 `CHANGELOG.md`、`LICENSE`(MIT)、`.gitignore`。
- GitHub 社区标准件：`CODE_OF_CONDUCT.md`(Contributor Covenant)、`SECURITY.md`、Issue 模板、PR 模板。
- 文档规范化：多 provider 配置示例（不再只有 Anthropic）、启动命令统一为 `python -m uvicorn`。

### Changed / Security
- **移除前端「清空知识库」按钮**：破坏性清库操作不再作为任何访客可点的按钮暴露在界面。清库改走后端 API `POST /knowledge/reset`（设了 `HKC_API_KEY` 时需带 `X-API-Key`）。

## [1.0.0]

首个成型版本。

### Core
- **KDE** 多 provider LLM 抽取（anthropic / deepseek / openai / openai-compatible 本地模型），并发抽取，文件解析（PDF / Markdown / TXT / EPUB / MOBI / AZW3）。
- **KEE** 知识演化引擎：canonical fingerprint 去重、Evidence 合并、冲突裁决——知识身份的唯一权威（Principle 07）。
- **ACE** 能力编译：从稳定知识编译 `.hkap` 能力包，能力定义走可配置的 `skill_taxonomy.json`（内置中/英文能力，多领域匹配）。
- **Search** 混合检索：BM25 + 语义向量(faiss) + 图，可插拔 embedding 后端（stub / local / TEI）。

### UI
- 单文件前端 + Three.js 3D「知识星球」（球面分布 + 多轴自转，离线 vendor）。
- 综合页：确定性组装 wiki 词条 + 按需 LLM 综述（派生只读视图，独立 `synthesis.db` 缓存，守 Principle 07）。
- 异步文件上传 + 实时进度、连接设置持久化、清库重来。

### API
- FastAPI REST：摄入 / 图谱 / 检索 / 能力 / 冲突 / 综述 / 运行时 LLM 配置。全量图接口剥离 `source_text` 瘦身 ~92%。
