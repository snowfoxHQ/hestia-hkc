# 贡献指南 (Contributing to HKC)

感谢参与 Hestia Knowledge Core！本文件是最简上手 + 硬性约定。架构原则见 [`ARCHITECTURE.md`](ARCHITECTURE.md)，模块 / API 文档见 [`docs/modules/`](docs/modules/)。

## 开发环境

```bash
git clone https://github.com/SnowFoxHQ/hestia-hkc
cd hestia-hkc
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[all]"                          # 全量依赖
```

> 需 Python ≥ 3.11。`.[all]` 含语义向量(sentence-transformers/faiss)、文件解析(PyMuPDF/ebooklib/mobi)、LLM SDK。

## 跑测试

```bash
HF_HUB_OFFLINE=1 python -m pytest tests/ -q
```

基线应全绿（250+ 用例）。CI 会在每个 PR 上自动跑（见 `.github/workflows/tests.yml`）。

## 硬性约定（提交前请确认）

1. **Principle 07 — 知识主权唯一**：知识的身份/合并/演化**只能由 KEE 决定**。前端、KDE、综合页、3D 星球都是只读投影层，不得自行合并/生成/改写知识。详见 ARCHITECTURE.md。
2. **模型无关**：任何 LLM 都必须能接入，绝不硬编码某个具体模型。具体模型名只能作为可覆盖的 fallback 默认值。新增 provider 走 `extractor.py` 的 `_apply_config`/`_get_client` + `config.py` 白名单。
3. **线程安全**：异步摄入跑在后台线程、请求路由跑在请求线程池——任何跨线程共享的 SQLite 连接或内存索引都必须加锁（与 `SQLiteGraphStore` 一致）。
4. **离线优先**：不得引入强制联网/CDN 依赖；要有本地降级路径。
5. **改动循环**：实现 → 验证(pytest / 前端 playwright 截图) → 修复 → 回归(全量测试) → **改前端则递增顶栏打包号**（`hkc-ui/index.html` 的 `#app-ver`）。

## 提交规范

- 一个 PR 聚焦一件事，附带对应测试。
- 新增共享可变状态时，先想它会不会被摄入线程与请求线程同时碰。
- 给 KU 加重字段时，考虑是否该排除出 `/knowledge/graph` 负载（该接口刻意剥离 `source_text` 瘦身，避免重字段拖慢星球加载；详见 `docs/modules/hkc-api_README.md`）。

## 目录速览

`src/hkc_*`（9 个包，自下而上：core → kep → kee/ace/kde → search → crystallizer/sdk → api）、`hkc-ui/index.html`（单文件前端 + Three.js）、`tests/`、`docs/`。
