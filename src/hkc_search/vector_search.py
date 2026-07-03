"""
hkc-search / vector_search.py
向量检索（Warm 层）。

向量生成委托给可插拔的 EmbeddingBackend（见 embedding_backends.py）：
  LocalSTBackend  本地 sentence-transformers
  TEIBackend      远程 TEI 服务
  StubBackend     离线/测试

FAISS IndexFlatIP 做近似最近邻，向量已 L2 归一化，内积 = 余弦相似度。
VectorIndex 不再关心模型如何加载，只调用 backend.encode()。
"""
from __future__ import annotations
import logging
import os
from dataclasses import dataclass

import numpy as np

from .embedding_backends import EmbeddingBackend, make_backend

logger = logging.getLogger(__name__)


@dataclass
class VectorResult:
    ku_id:   str
    score:   float    # 余弦相似度 0-1
    name:    str
    summary: str


class VectorIndex:

    def __init__(
        self,
        backend: EmbeddingBackend | None = None,
        model_name: str | None = None,   # 向后兼容旧调用
    ):
        """
        backend: 显式传入 embedding 后端。
        不传则用 make_backend("auto")（本地优先，回退 stub）。
        model_name: 兼容旧接口，等价于 make_backend("local", model_name=...)。
        """
        if backend is not None:
            self._backend = backend
        elif model_name is not None:
            self._backend = make_backend("local", model_name=model_name)
        else:
            self._backend = make_backend("auto")

        self._index = None
        self._ku_ids:    list[str] = []
        self._names:     list[str] = []
        self._summaries: list[str] = []

    @property
    def backend_name(self) -> str:
        return self._backend.name

    # ── 构建 ──────────────────────────────────────────────────

    def build(self, kus: list) -> None:
        if not kus:
            return
        texts      = [self._ku_text(ku) for ku in kus]
        embeddings = self._backend.encode(texts)
        self._init_index()
        self._index.add(embeddings)
        self._ku_ids    = [ku.ku_id         for ku in kus]
        self._names     = [ku.name          for ku in kus]
        self._summaries = [ku.summary[:100] for ku in kus]
        logger.info("向量索引构建完成，共 %d 条 (backend=%s)",
                    len(self._ku_ids), self._backend.name)

    def append_ku(self, ku) -> None:
        # 先 encode（触发后端维度校正），再用校正后的 dim 初始化索引
        vec = self._backend.encode([self._ku_text(ku)])
        if self._index is None:
            self._init_index()
        self._index.add(vec)
        self._ku_ids.append(ku.ku_id)
        self._names.append(ku.name)
        self._summaries.append(ku.summary[:100])

    # ── 检索 ──────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 10) -> list[VectorResult]:
        if self._index is None or self._index.ntotal == 0:
            return []
        vec = self._backend.encode([query])
        k   = min(top_k, self._index.ntotal)
        distances, indices = self._index.search(vec, k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(self._ku_ids):
                continue
            score = float(dist)
            if score < 0.1:
                continue
            results.append(VectorResult(
                ku_id   = self._ku_ids[idx],
                score   = score,
                name    = self._names[idx],
                summary = self._summaries[idx],
            ))
        return results

    def size(self) -> int:
        return self._index.ntotal if self._index else 0

    # ── 持久化 ────────────────────────────────────────────────

    def save(self, path: str) -> None:
        import faiss, pickle
        if self._index is None:
            logger.warning("索引为空，跳过保存")
            return
        faiss.write_index(self._index, path + ".faiss")
        with open(path + ".meta", "wb") as f:
            pickle.dump({
                "ku_ids":    self._ku_ids,
                "names":     self._names,
                "summaries": self._summaries,
                "backend":   self._backend.name,
            }, f)
        logger.info("向量索引已保存: %s", path)

    def load(self, path: str) -> bool:
        import faiss, pickle
        faiss_path = path + ".faiss"
        meta_path  = path + ".meta"
        if not (os.path.exists(faiss_path) and os.path.exists(meta_path)):
            return False
        self._index = faiss.read_index(faiss_path)
        with open(meta_path, "rb") as f:
            meta = pickle.load(f)
        self._ku_ids    = meta["ku_ids"]
        self._names     = meta["names"]
        self._summaries = meta["summaries"]
        logger.info("向量索引已加载: %d 条", self._index.ntotal)
        return True

    # ── 内部方法 ─────────────────────────────────────────────

    def _init_index(self) -> None:
        try:
            import faiss
            self._index = faiss.IndexFlatIP(self._backend.dim)
        except ImportError:
            raise ImportError("faiss-cpu 未安装: pip install faiss-cpu")

    @staticmethod
    def _ku_text(ku) -> str:
        parts = [ku.name]
        if ku.summary:
            parts.append(ku.summary)
        if ku.tags:
            parts.append(" ".join(ku.tags))
        stmt = getattr(ku, "statement", "")
        if stmt:
            parts.append(stmt)
        return " ".join(parts)
