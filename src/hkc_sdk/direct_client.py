"""
hkc_sdk / direct_client.py
直连客户端：直接调用 HKCContainer 的引擎，不走 HTTP。

用于 HAR 和 HKC 跑在同一进程的场景，省掉网络序列化开销。
方法签名与 HTTPClient 完全一致，上层代码无感切换。
"""
from __future__ import annotations
import logging

from .base import BaseClient
from .models import (
    KU, SearchHit, Ability, CoverageReport, Conflict, IngestResult,
)
from .exceptions import (
    HKCNotFoundError, HKCInsufficientCoverage, HKCBadRequest,
)

logger = logging.getLogger(__name__)


class DirectClient(BaseClient):

    def __init__(self, container):
        """
        container: 已 assemble 的 HKCContainer 实例。
        """
        if not getattr(container, "_assembled", False):
            container.assemble()
        self.c = container

    # ── 知识摄入 ─────────────────────────────────────────────

    def ingest_text(self, text, source_title="", domain="",
                    source="inline", source_year=0) -> IngestResult:
        kus = self.c.kde.ingest_text(
            text=text, source=source, source_title=source_title,
            source_year=source_year, domain=domain,
        )
        return self._ingest_result(kus)

    def ingest_url(self, url, source_title="", domain="", source_year=0) -> IngestResult:
        kus = self.c.kde.ingest_url(
            url=url, source_title=source_title,
            source_year=source_year, domain=domain,
        )
        return self._ingest_result(kus)

    def _ingest_result(self, kus) -> IngestResult:
        counts = {}
        for ku in kus:
            t = ku.ku_type.value
            counts[t] = counts.get(t, 0) + 1
        return IngestResult(
            ku_count=len(kus),
            ku_ids=[ku.ku_id for ku in kus],
            counts=counts,
        )

    # ── KU 查询 ──────────────────────────────────────────────

    def get_ku(self, ku_id) -> KU:
        ku = self.c.graph_store.get(ku_id)
        if not ku:
            raise HKCNotFoundError(f"KU 不存在: {ku_id}")
        return KU.from_dict(ku.to_dict())

    def list_by_domain(self, domain, limit=50) -> list[KU]:
        kus = self.c.graph_store.query_by_domain(domain, limit=limit)
        return [KU.from_dict(ku.to_dict()) for ku in kus]

    def neighbors(self, ku_id, max_depth=2, top_k=20) -> list[SearchHit]:
        ku = self.c.graph_store.get(ku_id)
        if not ku:
            raise HKCNotFoundError(f"KU 不存在: {ku_id}")
        hits = self.c.search.search_neighbors(ku_id, max_depth=max_depth, top_k=top_k)
        return [
            SearchHit(ku_id=h.ku_id, score=h.score, name=h.name,
                      summary=h.summary, mode="graph")
            for h in hits
        ]

    # ── 搜索 ─────────────────────────────────────────────────

    def search(self, query, mode="hybrid", top_k=10, domain="",
               ku_types=None) -> list[SearchHit]:
        if mode not in ("bm25", "vector", "graph", "hybrid"):
            raise HKCBadRequest(f"无效 mode: {mode}")
        results = self.c.search.search(
            query=query, mode=mode, top_k=top_k,
            domain=domain, ku_types=ku_types,
        )
        return [
            SearchHit(ku_id=r.ku_id, score=r.score, name=r.name,
                      summary=r.summary, mode=r.mode, ku_type=r.ku_type)
            for r in results
        ]

    def find_path(self, from_id, to_id) -> list[str]:
        return self.c.search.find_path(from_id, to_id)

    # ── 能力 ─────────────────────────────────────────────────

    def list_abilities(self) -> list[str]:
        return self.c.ace.list_available()

    def coverage_report(self, ability_key) -> CoverageReport:
        report = self.c.ace.coverage_report(ability_key)
        if not report:
            raise HKCNotFoundError(f"未知 Ability: {ability_key}")
        return CoverageReport.from_dict(report)

    def compile_ability(self, ability_key) -> Ability:
        try:
            pkg = self.c.ace.compile(ability_key)
        except ValueError as e:
            raise HKCNotFoundError(str(e))
        if pkg is None:
            report = self.c.ace.coverage_report(ability_key)
            raise HKCInsufficientCoverage(
                "知识覆盖度不足，无法编译此 Ability",
                {"coverage": report.get("coverage", {}),
                 "missing_skills": report.get("missing_skills", [])},
            )
        return Ability.from_dict(pkg.to_dict())

    def get_ability(self, ability_key) -> Ability:
        pkg = self.c.ace.load_ability(ability_key)
        if not pkg:
            raise HKCNotFoundError(f"Ability 尚未编译: {ability_key}")
        return Ability.from_dict(pkg.to_dict())

    # ── 冲突 ─────────────────────────────────────────────────

    def list_conflicts(self, status="open", domain="") -> list[Conflict]:
        cards = self.c.graph_store.list_conflicts(status=status, domain=domain)
        return [Conflict.from_dict(card.to_dict()) for card in cards]

    def resolve_conflict(self, conflict_id, winner_id, note="",
                         resolved_by="sdk") -> bool:
        return self.c.kee.manual_resolve(
            conflict_id=conflict_id, winner_id=winner_id,
            note=note, resolved_by=resolved_by,
        )

    # ── 系统 ─────────────────────────────────────────────────

    def stats(self) -> dict:
        return self.c.stats()

    def health(self) -> bool:
        """直连模式：容器已装配即健康。"""
        return getattr(self.c, "_assembled", False)
