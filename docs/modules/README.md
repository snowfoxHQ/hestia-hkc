# 模块文档索引

各子系统的参考文档。总体架构原则见根目录 [`ARCHITECTURE.md`](../../ARCHITECTURE.md)，
上手与约定见 [`CONTRIBUTING.md`](../../CONTRIBUTING.md)。

| 文档 | 内容 |
|------|------|
| [hkc-api_README.md](hkc-api_README.md) | REST API 使用指南：端点总览、可选鉴权、环境变量、典型流程 |
| [hkc-kee_DEDUP.md](hkc-kee_DEDUP.md) | KEE 知识级去重：canonical fingerprint、Evidence 合并、并发加锁 |
| [hkc-search_EMBEDDING_BACKENDS.md](hkc-search_EMBEDDING_BACKENDS.md) | 混合检索的可插拔 embedding 后端：`stub` / `local` / `tei` |
| [hkc-sdk_README.md](hkc-sdk_README.md) | Python SDK：一套接口，两种后端（远程 HTTP / 进程内直连） |
| [hkc-crystallizer_README.md](hkc-crystallizer_README.md) | Crystallizer 集成层：把外部事件转成知识候选送入 KDE→KEE |
| [hkc-crystallizer_LAYER.md](hkc-crystallizer_LAYER.md) | Crystallizer 的分层定位：属 Integrations 层，非 HKC Core |
