"""
hkc-search / hybrid.py
混合检索：BM25 + Vector 结果用 RRF（Reciprocal Rank Fusion）融合。

RRF 公式：score(d) = Σ 1 / (k + rank_i(d))
k = 60（标准值，平衡头部和尾部排名的影响）

三级检索策略：
  Cold  → BM25（关键词精确匹配）
  Warm  → Vector（语义相似）
  Hot   → Graph（关系链路）
  Hybrid → BM25 + Vector RRF（默认模式）
"""
from __future__ import annotations
import logging
import threading
from dataclasses import dataclass
from typing import Literal

from .bm25          import BM25Index, BM25Result
from .vector_search import VectorIndex, VectorResult
from .graph_search  import GraphSearch, GraphResult

logger = logging.getLogger(__name__)

RRF_K    = 60      # RRF 平滑常数
MAX_DOCS = 100     # 融合前每路最多取多少结果


@dataclass
class SearchResult:
    ku_id:   str
    score:   float
    mode:    str     # bm25 | vector | graph | hybrid
    name:    str
    summary: str
    ku_type: str = ""
    rel_type: str = ""   # 图谱邻居场景下的关系类型


SearchMode = Literal["bm25", "vector", "graph", "hybrid"]


class HybridSearch:

    def __init__(
        self,
        graph_store,
        bm25_index:    BM25Index   | None = None,
        vector_index:  VectorIndex | None = None,
    ):
        self.store  = graph_store
        self.bm25   = bm25_index   or BM25Index()
        self.vector = vector_index or VectorIndex()
        self.graph  = GraphSearch(graph_store)
        # 索引读写锁:摄入线程 append_ku(改 bm25/faiss)会撞 HTTP 线程的 search;
        # faiss 并发 add+search 是未定义行为(可能崩)。RLock 序列化改与读。
        self._lock  = threading.RLock()

    # ── 主接口 ───────────────────────────────────────────────

    # 搜索结果中默认排除的 KU 状态（已废弃/删除的不应出现在检索结果）
    _INACTIVE_STATUSES = {"superseded", "rejected", "deleted", "disputed"}

    def search(
        self,
        query:     str,
        mode:      SearchMode = "hybrid",
        top_k:     int = 10,
        domain:    str = "",
        ku_types:  list[str] | None = None,
        exclude_inactive: bool = True,
    ) -> list[SearchResult]:
        """
        统一搜索接口。
        mode: bm25 | vector | graph | hybrid（默认）
        domain: 限定领域
        ku_types: 限定 KU 类型，如 ["Claim", "Concept"]
        exclude_inactive: 排除 superseded/rejected/deleted/disputed 状态的 KU（默认 True）
        """
        with self._lock:      # 与 append_ku/build_index 互斥,防 faiss 并发 add+search
            if mode == "bm25":
                raw = self._bm25_search(query, top_k * 2)
            elif mode == "vector":
                raw = self._vector_search(query, top_k * 2)
            elif mode == "graph":
                raw = self._graph_search(query, top_k * 2)
            else:
                raw = self._hybrid_search(query, top_k * 2)

            # 过滤 domain / ku_types / 废弃状态
            filtered = self._filter(raw, domain, ku_types, exclude_inactive)
            return filtered[:top_k]

    def search_neighbors(
        self,
        ku_id:     str,
        max_depth: int = 2,
        top_k:     int = 20,
        rel_types: list[str] | None = None,
    ) -> list[SearchResult]:
        """从指定 KU 出发做图谱邻居展开。"""
        with self._lock:
            graph_results = self.graph.search_neighbors(
                ku_id, max_depth=max_depth, top_k=top_k, rel_types=rel_types
            )
            return [self._graph_to_search(r) for r in graph_results]

    def find_path(self, from_id: str, to_id: str) -> list[str]:
        """两 KU 间最短路径。"""
        return self.graph.find_path(from_id, to_id)

    # ── 索引维护 ─────────────────────────────────────────────

    def build_index(self, kus: list | None = None) -> None:
        """
        从 GraphStore 全量构建 BM25 + 向量索引。
        kus 为 None 时从 store 加载。
        """
        if kus is None:
            kus = self.store.query_all(limit=10000)

        if not kus:
            logger.warning("GraphStore 为空，索引未构建")
            return

        logger.info("构建搜索索引，共 %d 条 KU", len(kus))
        with self._lock:
            self.bm25.build(kus)
            self.vector.build(kus)

    def append_ku(self, ku) -> None:
        """新 KU 写入后增量更新索引。"""
        with self._lock:
            self.bm25.append_ku(ku)
            self.vector.append_ku(ku)

    # ── 各路检索 ─────────────────────────────────────────────

    def _bm25_search(self, query: str, top_k: int) -> list[SearchResult]:
        results = self.bm25.search(query, top_k=top_k)
        return [
            SearchResult(
                ku_id   = r.ku_id,
                score   = r.score,
                mode    = "bm25",
                name    = r.name,
                summary = r.summary,
            )
            for r in results
        ]

    def _vector_search(self, query: str, top_k: int) -> list[SearchResult]:
        results = self.vector.search(query, top_k=top_k)
        return [
            SearchResult(
                ku_id   = r.ku_id,
                score   = r.score,
                mode    = "vector",
                name    = r.name,
                summary = r.summary,
            )
            for r in results
        ]

    def _graph_search(self, query: str, top_k: int) -> list[SearchResult]:
        """
        图谱搜索：先用 BM25 找种子节点，再从种子展开邻居。
        """
        seeds = self.bm25.search(query, top_k=3)
        if not seeds:
            return []

        seen: set[str]         = set()
        all_results: list[SearchResult] = []

        for seed in seeds:
            graph_results = self.graph.search_neighbors(
                seed.ku_id, max_depth=2, top_k=top_k
            )
            for r in graph_results:
                if r.ku_id not in seen:
                    seen.add(r.ku_id)
                    all_results.append(self._graph_to_search(r))

        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results[:top_k]

    def _hybrid_search(self, query: str, top_k: int) -> list[SearchResult]:
        """
        RRF 融合 BM25 + 向量结果。
        """
        bm25_results   = self.bm25.search(query,   top_k=MAX_DOCS)
        vector_results = self.vector.search(query,  top_k=MAX_DOCS)

        # RRF 打分
        rrf_scores: dict[str, float] = {}

        for rank, r in enumerate(bm25_results):
            rrf_scores[r.ku_id] = rrf_scores.get(r.ku_id, 0) + 1.0 / (RRF_K + rank + 1)

        for rank, r in enumerate(vector_results):
            rrf_scores[r.ku_id] = rrf_scores.get(r.ku_id, 0) + 1.0 / (RRF_K + rank + 1)

        # 合并元数据
        meta: dict[str, tuple[str, str]] = {}
        for r in bm25_results:
            meta[r.ku_id] = (r.name, r.summary)
        for r in vector_results:
            if r.ku_id not in meta:
                meta[r.ku_id] = (r.name, r.summary)

        sorted_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)

        results = []
        for ku_id in sorted_ids[:top_k]:
            name, summary = meta.get(ku_id, ("", ""))
            results.append(SearchResult(
                ku_id   = ku_id,
                score   = rrf_scores[ku_id],
                mode    = "hybrid",
                name    = name,
                summary = summary,
            ))
        return results

    # ── 过滤与转换 ───────────────────────────────────────────

    def _filter(
        self,
        results:  list[SearchResult],
        domain:   str,
        ku_types: list[str] | None,
        exclude_inactive: bool = True,
    ) -> list[SearchResult]:
        """按 domain / ku_types / 状态过滤，需要回查 GraphStore。"""
        # 无任何过滤条件时直接返回，省去 N+1 查询
        if not domain and not ku_types and not exclude_inactive:
            return results

        filtered = []
        for r in results:
            ku = self.store.get(r.ku_id)
            if not ku:
                continue
            if exclude_inactive and ku.status in self._INACTIVE_STATUSES:
                continue
            # Claim 还要看 claim_status（active 之外的废弃状态）
            if exclude_inactive and ku.ku_type.value == "Claim":
                cs = ku.to_dict().get("extra", {}).get("claim_status", "active")
                if cs in self._INACTIVE_STATUSES:
                    continue
            if domain and ku.domain != domain:
                continue
            if ku_types and ku.ku_type.value not in ku_types:
                continue
            r.ku_type = ku.ku_type.value
            filtered.append(r)
        return filtered

    @staticmethod
    def _graph_to_search(r: GraphResult) -> SearchResult:
        return SearchResult(
            ku_id    = r.ku_id,
            score    = r.score,
            mode     = "graph",
            name     = r.name,
            summary  = r.summary,
            rel_type = getattr(r, "rel_type", ""),
        )
