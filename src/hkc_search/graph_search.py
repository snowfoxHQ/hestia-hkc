"""
hkc-search / graph_search.py
图谱检索（Hot 层）。

给定一个起始 KU ID，BFS 展开邻居，按关系权重和深度衰减排分。
适用于：
- "查找与 X 相关的所有概念"
- "X 的证据链"
- "从 A 到 B 的知识路径"
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from collections import deque

logger = logging.getLogger(__name__)

# 深度衰减因子：每深一层分数 × DECAY
DEPTH_DECAY = 0.7


@dataclass
class GraphResult:
    ku_id:    str
    score:    float   # 基于深度衰减和关系权重
    depth:    int
    rel_type: str
    name:     str
    summary:  str


class GraphSearch:

    def __init__(self, graph_store):
        self.store = graph_store

    def search_neighbors(
        self,
        start_id:  str,
        max_depth: int = 2,
        top_k:     int = 20,
        rel_types: list[str] | None = None,  # None = 所有关系类型
    ) -> list[GraphResult]:
        """
        从 start_id 出发 BFS，返回邻居 KU 及关系信息。
        按 score（深度衰减 × 关系权重）排序。
        """
        start_ku = self.store.get(start_id)
        if not start_ku:
            return []

        visited: set[str] = {start_id}
        results: list[GraphResult] = []
        # 队列：(ku_id, depth, accumulated_score, rel_type)
        queue: deque = deque([(start_id, 0, 1.0, "")])

        while queue:
            ku_id, depth, score, rel_type = queue.popleft()

            if depth > 0:
                ku = self.store.get(ku_id)
                if ku:
                    results.append(GraphResult(
                        ku_id    = ku_id,
                        score    = score,
                        depth    = depth,
                        rel_type = rel_type,
                        name     = ku.name,
                        summary  = ku.summary[:100],
                    ))

            if depth >= max_depth:
                continue

            # 展开出边和入边
            rels = self.store.get_relations(ku_id, direction="both")
            for rel in rels:
                # 过滤关系类型
                if rel_types and rel.rel_type not in rel_types:
                    continue

                neighbor_id = (
                    rel.to_ku if rel.from_ku == ku_id else rel.from_ku
                )
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)

                next_score = score * rel.weight * DEPTH_DECAY
                queue.append((neighbor_id, depth + 1, next_score, rel.rel_type))

        # 按分数排序
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def find_path(
        self,
        from_id: str,
        to_id:   str,
    ) -> list[str]:
        """
        返回两个 KU 之间的最短路径（KU ID 列表）。
        不可达返回空列表。
        """
        return self.store.shortest_path(from_id, to_id)

    def search_by_domain(
        self,
        domain:    str,
        ku_types:  list[str] | None = None,
        top_k:     int = 20,
    ) -> list[GraphResult]:
        """
        按领域过滤 KU，按置信度排序。
        不走图谱，直接从 GraphStore 查询。
        """
        kus = self.store.query_by_domain(domain, limit=top_k * 2)
        if ku_types:
            kus = [k for k in kus if k.ku_type.value in ku_types]

        results = []
        for ku in kus[:top_k]:
            results.append(GraphResult(
                ku_id    = ku.ku_id,
                score    = ku.confidence,
                depth    = 0,
                rel_type = "",
                name     = ku.name,
                summary  = ku.summary[:100],
            ))
        results.sort(key=lambda r: r.score, reverse=True)
        return results
