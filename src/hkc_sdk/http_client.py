"""
hkc_sdk / http_client.py
HTTP 客户端：通过 REST 调用 hkc-api。

用于 HKC 作为独立服务部署、调用方在另一进程/机器的场景。
"""
from __future__ import annotations
import logging

from .base import BaseClient
from .models import (
    KU, SearchHit, Ability, CoverageReport, Conflict, IngestResult,
)
from .exceptions import (
    HKCError, HKCNotFoundError, HKCInsufficientCoverage,
    HKCBadRequest, HKCServerError,
)

logger = logging.getLogger(__name__)


class HTTPClient(BaseClient):

    def __init__(self, base_url: str = "http://localhost:8000", timeout: int = 60):
        self._base_url = base_url.rstrip("/")
        self._timeout  = timeout

    # ── 内部请求封装 ─────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs):
        try:
            import requests
        except ImportError:
            raise ImportError("requests 未安装: pip install requests")

        url = f"{self._base_url}{path}"
        try:
            resp = requests.request(
                method, url, timeout=self._timeout, **kwargs
            )
        except requests.RequestException as e:
            raise HKCServerError(f"连接失败: {e}")

        return self._handle_response(resp)

    def _handle_response(self, resp):
        # 解析错误 detail（可能是 dict 或字符串）
        if resp.status_code == 200:
            return resp.json()

        try:
            detail = resp.json().get("detail")
        except Exception:
            detail = resp.text

        if resp.status_code == 404:
            raise HKCNotFoundError(str(detail), detail)
        if resp.status_code == 422:
            msg = detail.get("message") if isinstance(detail, dict) else str(detail)
            raise HKCInsufficientCoverage(msg or "覆盖度不足", detail)
        if resp.status_code in (400, 413):
            raise HKCBadRequest(str(detail), detail)
        raise HKCServerError(f"HTTP {resp.status_code}: {detail}", detail)

    # ── 知识摄入 ─────────────────────────────────────────────

    def ingest_text(self, text, source_title="", domain="",
                    source="inline", source_year=0) -> IngestResult:
        data = self._request("POST", "/knowledge/ingest/text", json={
            "text": text, "source": source, "source_title": source_title,
            "source_year": source_year, "domain": domain,
        })
        return IngestResult.from_dict(data)

    def ingest_url(self, url, source_title="", domain="", source_year=0) -> IngestResult:
        data = self._request("POST", "/knowledge/ingest/url", json={
            "url": url, "source_title": source_title,
            "source_year": source_year, "domain": domain,
        })
        return IngestResult.from_dict(data)

    # ── KU 查询 ──────────────────────────────────────────────

    def get_ku(self, ku_id) -> KU:
        data = self._request("GET", f"/knowledge/ku/{ku_id}")
        return KU.from_dict(data)

    def list_by_domain(self, domain, limit=50) -> list[KU]:
        data = self._request("GET", f"/knowledge/domain/{domain}",
                            params={"limit": limit})
        return [KU.from_dict(k) for k in data.get("kus", [])]

    def neighbors(self, ku_id, max_depth=2, top_k=20) -> list[SearchHit]:
        data = self._request("GET", f"/knowledge/ku/{ku_id}/neighbors",
                            params={"max_depth": max_depth, "top_k": top_k})
        hits = []
        for h in data.get("neighbors", []):
            h.setdefault("mode", "graph")   # 与 DirectClient 一致
            hits.append(SearchHit.from_dict(h))
        return hits

    # ── 搜索 ─────────────────────────────────────────────────

    def search(self, query, mode="hybrid", top_k=10, domain="",
               ku_types=None) -> list[SearchHit]:
        data = self._request("POST", "/search", json={
            "query": query, "mode": mode, "top_k": top_k,
            "domain": domain, "ku_types": ku_types,
        })
        return [SearchHit.from_dict(h) for h in data.get("hits", [])]

    def find_path(self, from_id, to_id) -> list[str]:
        data = self._request("GET", "/search/path",
                            params={"from_id": from_id, "to_id": to_id})
        return data.get("path", [])

    # ── 能力 ─────────────────────────────────────────────────

    def list_abilities(self) -> list[str]:
        data = self._request("GET", "/abilities")
        return data.get("abilities", [])

    def coverage_report(self, ability_key) -> CoverageReport:
        data = self._request("GET", f"/abilities/{ability_key}/coverage")
        return CoverageReport.from_dict(data)

    def compile_ability(self, ability_key) -> Ability:
        data = self._request("POST", f"/abilities/{ability_key}/compile")
        return Ability.from_dict(data)

    def get_ability(self, ability_key) -> Ability:
        data = self._request("GET", f"/abilities/{ability_key}")
        return Ability.from_dict(data)

    # ── 冲突 ─────────────────────────────────────────────────

    def list_conflicts(self, status="open", domain="") -> list[Conflict]:
        data = self._request("GET", "/conflicts",
                            params={"status": status, "domain": domain})
        return [Conflict.from_dict(c) for c in data.get("conflicts", [])]

    def resolve_conflict(self, conflict_id, winner_id, note="",
                         resolved_by="sdk") -> bool:
        # 裁决失败（冲突不存在/已解决/winner 无效）API 返回 400，
        # SDK 统一为返回 False（与 DirectClient 行为一致），而非抛异常。
        from .exceptions import HKCBadRequest
        try:
            data = self._request("POST", f"/conflicts/{conflict_id}/resolve", json={
                "winner_id": winner_id, "note": note, "resolved_by": resolved_by,
            })
            return data.get("resolved", False)
        except HKCBadRequest:
            return False

    # ── 系统 ─────────────────────────────────────────────────

    def stats(self) -> dict:
        return self._request("GET", "/stats")

    def health(self) -> bool:
        from .exceptions import HKCError
        try:
            self._request("GET", "/health")
            return True
        except HKCError:
            return False
