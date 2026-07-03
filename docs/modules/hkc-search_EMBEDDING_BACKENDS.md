# HKC 向量检索 — Embedding 后端配置

`hkc-search` 的向量检索把"如何生成向量"抽象成可插拔的 `EmbeddingBackend`，
检索逻辑（FAISS 索引、相似度计算）与向量来源完全解耦。

## 三种后端

| 后端 | 用途 | 依赖 |
|------|------|------|
| `LocalSTBackend` | 进程内 sentence-transformers | 模型权重在本地 |
| `TEIBackend` | 远程调用 TEI 推理服务 | 一个运行中的 TEI HTTP 服务 |
| `StubBackend` | 离线 / 测试 / CI | 无，确定性伪向量 |

## 用法

### 默认（自动选择）

```python
from hkc_search.vector_search import VectorIndex

# auto：优先本地模型，加载失败自动回退 StubBackend
index = VectorIndex()
```

### 本地模型

```python
from hkc_search.embedding_backends import LocalSTBackend
from hkc_search.vector_search import VectorIndex

backend = LocalSTBackend("paraphrase-multilingual-MiniLM-L12-v2")
index   = VectorIndex(backend=backend)
```

### TEI 远程服务（推荐生产用）

第一步，在有 GPU / 网络的机器上启动 TEI：

```bash
# Docker 方式（最简单）
docker run -p 8080:80 --gpus all \
  ghcr.io/huggingface/text-embeddings-inference:1.x \
  --model-id BAAI/bge-m3
```

或用你上传的源码自行编译（需要 Rust 工具链）：

```bash
cd text-embeddings-inference-main
cargo install --path router -F candle-cuda   # GPU
# 或 cargo install --path router -F candle    # CPU
text-embeddings-router --model-id BAAI/bge-m3 --port 8080
```

第二步，HKC 端配置指向该服务：

```python
from hkc_search.embedding_backends import TEIBackend
from hkc_search.vector_search import VectorIndex

backend = TEIBackend("http://localhost:8080")

# 启动前可以先检查服务在线
if backend.health_check():
    index = VectorIndex(backend=backend)
else:
    print("TEI 服务不在线")
```

### 与 HybridSearch 配合

```python
from hkc_search.hybrid import HybridSearch
from hkc_search.vector_search import VectorIndex
from hkc_search.embedding_backends import TEIBackend

vector_index = VectorIndex(backend=TEIBackend("http://localhost:8080"))
hs = HybridSearch(graph_store, vector_index=vector_index)
hs.build_index()   # 从 GraphStore 全量构建 BM25 + 向量索引
```

## 维度自适应

后端会在首次编码时自动校正维度：
- `LocalSTBackend` 读取模型的 `get_sentence_embedding_dimension()`
- `TEIBackend` 读取首次响应的向量长度
- FAISS 索引按实际维度初始化

所以换模型（384 维 → 1024 维）不需要改任何代码。

## 注意

- `StubBackend` 的伪向量**没有语义**，只能验证索引/检索/持久化流程跑通，
  不能验证检索质量。生产环境必须用 `LocalSTBackend` 或 `TEIBackend`。
- TEI 服务本身在启动时需要联网下载模型权重（或指向本地权重目录），
  这一步在 TEI 端完成，与 HKC 无关。
- 离线测试时设 `HF_HUB_OFFLINE=1` 可避免 sentence-transformers 反复尝试联网。
