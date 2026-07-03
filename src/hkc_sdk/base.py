"""
hkc_sdk / base.py
SDK 客户端抽象接口。

两种实现共享这套方法签名：
  HTTPClient    通过 REST 调用 hkc-api（远程/跨进程）
  DirectClient  直接调 HKCContainer 的引擎（进程内）

用户代码只依赖 BaseClient，底层随时可换：
  from hkc_sdk import connect
  hkc = connect("http://localhost:8000")   # HTTP
  hkc = connect(container=my_container)     # 直连
  hits = hkc.search("价值投资")             # 两种写法完全一样
"""
from __future__ import annotations
from abc import ABC, abstractmethod

from .models import (
    KU, SearchHit, Ability, CoverageReport, Conflict, IngestResult,
)


class BaseClient(ABC):

    # ── 知识摄入 ─────────────────────────────────────────────

    @abstractmethod
    def ingest_text(
        self,
        text: str,
        source_title: str = "",
        domain: str = "",
        source: str = "inline",
        source_year: int = 0,
    ) -> IngestResult:
        """摄入文本，返回写入的 KU 信息。"""
        ...

    @abstractmethod
    def ingest_url(
        self,
        url: str,
        source_title: str = "",
        domain: str = "",
        source_year: int = 0,
    ) -> IngestResult:
        """摄入网页 URL。"""
        ...

    # ── KU 查询 ──────────────────────────────────────────────

    @abstractmethod
    def get_ku(self, ku_id: str) -> KU:
        """按 ID 获取 KU，不存在抛 HKCNotFoundError。"""
        ...

    @abstractmethod
    def list_by_domain(self, domain: str, limit: int = 50) -> list[KU]:
        """列出某领域的所有 KU。"""
        ...

    @abstractmethod
    def neighbors(self, ku_id: str, max_depth: int = 2, top_k: int = 20) -> list[SearchHit]:
        """获取 KU 的图谱邻居。"""
        ...

    # ── 搜索 ─────────────────────────────────────────────────

    @abstractmethod
    def search(
        self,
        query: str,
        mode: str = "hybrid",
        top_k: int = 10,
        domain: str = "",
        ku_types: list[str] | None = None,
    ) -> list[SearchHit]:
        """统一搜索。mode: bm25 | vector | graph | hybrid。"""
        ...

    @abstractmethod
    def find_path(self, from_id: str, to_id: str) -> list[str]:
        """两 KU 间最短路径。"""
        ...

    # ── 能力（ACE）───────────────────────────────────────────

    @abstractmethod
    def list_abilities(self) -> list[str]:
        """列出所有可编译的 Ability key。"""
        ...

    @abstractmethod
    def coverage_report(self, ability_key: str) -> CoverageReport:
        """查看某 Ability 的知识覆盖情况（不编译）。"""
        ...

    @abstractmethod
    def compile_ability(self, ability_key: str) -> Ability:
        """
        编译能力包。覆盖度不足时抛 HKCInsufficientCoverage。
        """
        ...

    @abstractmethod
    def get_ability(self, ability_key: str) -> Ability:
        """加载已编译的能力包，未编译抛 HKCNotFoundError。"""
        ...

    # ── 冲突（KEE）───────────────────────────────────────────

    @abstractmethod
    def list_conflicts(self, status: str = "open", domain: str = "") -> list[Conflict]:
        """列出冲突卡。"""
        ...

    @abstractmethod
    def resolve_conflict(
        self,
        conflict_id: str,
        winner_id: str,
        note: str = "",
        resolved_by: str = "sdk",
    ) -> bool:
        """人工裁决冲突。"""
        ...

    # ── 系统 ─────────────────────────────────────────────────

    @abstractmethod
    def stats(self) -> dict:
        """系统统计。"""
        ...

    @abstractmethod
    def health(self) -> bool:
        """检查 HKC 是否可用。"""
        ...

    # ── 便捷方法（默认实现，子类无需重写）────────────────────

    def ensure_ability(self, ability_key: str) -> Ability:
        """
        获取能力包，若未编译则尝试编译。
        覆盖度足够时返回 Ability，不足时抛 HKCInsufficientCoverage。
        """
        from .exceptions import HKCNotFoundError
        try:
            return self.get_ability(ability_key)
        except HKCNotFoundError:
            return self.compile_ability(ability_key)
