"""
hkc-core / graph / base.py
GraphStore 抽象接口。
v1 实现：SQLiteGraphStore
v2 可替换：Neo4jGraphStore
上层代码只依赖这个接口，底层实现随时可换。
"""
from abc import ABC, abstractmethod
from typing import Optional
from ..models.ku import KU, Relation, ConflictCard, ClaimKU
from ..models.enums import KUType, ClaimStatus


class GraphStore(ABC):

    # ── 单个 KU ──────────────────────────────

    @abstractmethod
    def get(self, ku_id: str) -> Optional[KU]:
        """按 ID 获取 KU，不存在返回 None。"""
        ...

    @abstractmethod
    def put(self, ku: KU) -> None:
        """写入或更新单个 KU。"""
        ...

    @abstractmethod
    def delete(self, ku_id: str) -> None:
        """软删除：把 status 改为 'deleted'。"""
        ...

    # ── 批量操作 ─────────────────────────────

    @abstractmethod
    def batch_write(self, kus: list[KU]) -> None:
        """批量写入，原子操作。"""
        ...

    # ── 查询 ─────────────────────────────────

    @abstractmethod
    def query_by_type(
        self,
        ku_type: KUType,
        domain: str = "",
        status: str = "active",
        limit: int = 100,
    ) -> list[KU]:
        ...

    @abstractmethod
    def query_by_domain(self, domain: str, limit: int = 200) -> list[KU]:
        ...

    @abstractmethod
    def search_by_name(self, name: str, fuzzy: bool = True) -> list[KU]:
        ...

    # ── 关系 ─────────────────────────────────

    @abstractmethod
    def add_relation(self, rel: Relation) -> None:
        ...

    @abstractmethod
    def delete_relation(self, rel_id: str) -> None:
        """删除一条关系(SQLite 表 + 图结构同步移除)。"""
        ...

    @abstractmethod
    def redirect_relations(self, from_ku_id: str, to_ku_id: str) -> int:
        """
        把所有指向/来自 from_ku_id 的关系,重定向到 to_ku_id。
        用于知识级去重:重复 KU 被合并入 canonical KU 后,
        其关系不应悬空,而应转移到 canonical KU 上。
        返回重定向的关系数。自环(重定向后 from==to)会被丢弃。
        """
        ...

    @abstractmethod
    def get_relations(
        self,
        ku_id: str,
        rel_type: str = "",
        direction: str = "both",   # "out" | "in" | "both"
    ) -> list[Relation]:
        ...

    @abstractmethod
    def get_all_relations(self, limit: int = 5000) -> list[Relation]:
        """全量拉取所有关系（用于一次性构建全图，如前端星球）。"""
        ...

    @abstractmethod
    def neighbors(
        self,
        ku_id: str,
        rel_type: str = "",
        depth: int = 1,
    ) -> list[KU]:
        ...

    @abstractmethod
    def shortest_path(self, from_id: str, to_id: str) -> list[str]:
        """返回 KU ID 路径列表，不可达返回空列表。"""
        ...

    # ── Conflict Cards ────────────────────────

    @abstractmethod
    def save_conflict(self, card: ConflictCard) -> None:
        ...

    @abstractmethod
    def get_conflict(self, conflict_id: str) -> Optional[ConflictCard]:
        ...

    @abstractmethod
    def list_conflicts(
        self,
        status: str = "open",
        domain: str = "",
    ) -> list[ConflictCard]:
        ...

    # ── Claim 专用 ────────────────────────────

    def query_all(self, limit: int = 2000) -> list[KU]:
        """全量查询所有非删除 KU，默认实现：遍历所有类型。"""
        from ..models.enums import KUType
        result = []
        for ku_type in KUType:
            result.extend(self.query_by_type(ku_type, status='active', limit=limit))
        return result

    @abstractmethod
    def find_claims_by_domain(
        self,
        domain: str,
        status: ClaimStatus = None,
    ) -> list[ClaimKU]:
        ...

    # ── 统计 ─────────────────────────────────

    @abstractmethod
    def stats(self) -> dict:
        """返回各类型 KU 数量、关系数量等基础统计。"""
        ...
