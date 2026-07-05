"""
hkc-core / models / ku.py
Knowledge Unit 数据结构定义。
六种类型共用 BaseKU，各自扩展专属字段。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from .enums import (
    KUType, EntityType, ClaimStatus,
    SourceType, ResolutionStrategy, ConflictStatus
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────
# Base
# ─────────────────────────────────────────

@dataclass
class BaseKU:
    """所有 KU 类型共用的基础字段。"""
    ku_id:      str
    ku_type:    KUType
    name:       str
    summary:    str                  = ""
    tags:       list[str]            = field(default_factory=list)
    sources:    list[str]            = field(default_factory=list)   # EVD_xxx
    relations:  list[str]            = field(default_factory=list)   # REL_xxx
    confidence: float                = 1.0
    status:     str                  = "active"
    domain:     str                  = ""
    version:    int                  = 1
    created_at: str                  = field(default_factory=_now)
    updated_at: str                  = field(default_factory=_now)
    extra:      dict[str, Any]       = field(default_factory=dict)   # 扩展字段

    def to_dict(self) -> dict:
        return {
            "ku_id":      self.ku_id,
            "ku_type":    self.ku_type.value,
            "name":       self.name,
            "summary":    self.summary,
            "tags":       self.tags,
            "sources":    self.sources,
            "relations":  self.relations,
            "confidence": self.confidence,
            "status":     self.status,
            "domain":     self.domain,
            "version":    self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "extra":      self.extra,
        }


# ─────────────────────────────────────────
# Entity
# ─────────────────────────────────────────

@dataclass
class EntityKU(BaseKU):
    """
    真实存在的人 / 机构 / 产品 / 事件 / 地点。
    Fact 和 Claim 的锚点。
    """
    ku_type:     KUType      = field(default=KUType.ENTITY, init=False)
    entity_type: EntityType  = EntityType.PERSON
    aliases:     list[str]   = field(default_factory=list)
    birth:       str         = ""    # ISO date，仅 Person
    active:      bool        = True
    source_text: str         = ""    # 提炼自的原文段落(供前端"看原文")

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["extra"].update({
            "entity_type": self.entity_type.value,
            "aliases":     self.aliases,
            "birth":       self.birth,
            "active":      self.active,
            "source_text": self.source_text,
        })
        return d


# ─────────────────────────────────────────
# Concept
# ─────────────────────────────────────────

@dataclass
class ConceptKU(BaseKU):
    """
    抽象知识单元：理论、模型、方法论。
    不可辩驳，但可被 DERIVED_FROM 细化。
    """
    ku_type:    KUType     = field(default=KUType.CONCEPT, init=False)
    aka:        list[str]  = field(default_factory=list)   # 别名
    definition: str        = ""
    source_text: str       = ""   # 提炼自的原文段落(供前端"看原文")

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["extra"].update({
            "aka":         self.aka,
            "definition":  self.definition,
            "source_text": self.source_text,
        })
        return d


# ─────────────────────────────────────────
# Fact   ← 关键：与 Claim 分开
# ─────────────────────────────────────────

@dataclass
class FactKU(BaseKU):
    """
    不可辩驳的客观陈述。
    confidence 固定 1.0，不参与 KEE 冲突检测。
    """
    ku_type:    KUType  = field(default=KUType.FACT, init=False)
    statement:  str     = ""
    verifiable: bool    = True
    source_ref: str     = ""    # 直接指向 EVD_xxx
    source_text: str    = ""    # 提炼自的原文段落(供前端"看原文")

    def __post_init__(self):
        # Fact 的置信度永远是 1.0
        self.confidence = 1.0

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["extra"].update({
            "statement":   self.statement,
            "verifiable":  self.verifiable,
            "source_ref":  self.source_ref,
            "source_text": self.source_text,
        })
        return d


# ─────────────────────────────────────────
# Claim  ← KEE 的核心处理对象
# ─────────────────────────────────────────

@dataclass
class ClaimKU(BaseKU):
    """
    有置信度的观点 / 理论。
    可被质疑，参与 KEE 冲突检测和状态机流转。
    """
    ku_type:       KUType       = field(default=KUType.CLAIM, init=False)
    statement:     str          = ""
    claim_status:  ClaimStatus  = ClaimStatus.PENDING
    supports:      list[str]    = field(default_factory=list)    # EVD_xxx 支持
    contradicts:   list[str]    = field(default_factory=list)    # EVD_xxx 反对
    conflict_refs: list[str]    = field(default_factory=list)    # CFT_xxx
    source_text:   str          = ""   # 提炼自的原文段落(供前端"看原文")

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["extra"].update({
            "statement":     self.statement,
            "claim_status":  self.claim_status.value,
            "supports":      self.supports,
            "contradicts":   self.contradicts,
            "conflict_refs": self.conflict_refs,
            "source_text":   self.source_text,
        })
        return d


# ─────────────────────────────────────────
# Evidence
# ─────────────────────────────────────────

@dataclass
class EvidenceKU(BaseKU):
    """
    支持或反对 Claim 的来源。
    是 Claim 置信度计算的基础材料。
    """
    ku_type:      KUType      = field(default=KUType.EVIDENCE, init=False)
    source:       str         = ""        # 书名 / 论文标题 / URL
    source_type:  SourceType  = SourceType.BOOK
    author:       str         = ""
    year:         int         = 0
    page:         int         = 0
    quote:        str         = ""        # 严格限制 < 200 字
    supports:     list[str]   = field(default_factory=list)   # CLM_xxx
    contradicts:          list[str] = field(default_factory=list)  # CLM_xxx

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["extra"].update({
            "source":       self.source,
            "source_type":  self.source_type.value,
            "author":       self.author,
            "year":         self.year,
            "page":         self.page,
            "quote":        self.quote,
            "supports":     self.supports,
            "contradicts":          self.contradicts,
        })
        return d


# ─────────────────────────────────────────
# Ability  ← ACE 的最终产物
# ─────────────────────────────────────────

@dataclass
class AbilityKU(BaseKU):
    """
    ACE 编译产出的能力包。
    Agent 直接加载，无需读原始书籍。
    """
    ku_type:       KUType          = field(default=KUType.ABILITY, init=False)
    ability_key:   str             = ""        # "quant_analyst"
    skills:        list[str]       = field(default_factory=list)    # SKL_xxx
    workflows:     list[str]       = field(default_factory=list)    # WFL_xxx
    coverage:      dict[str, float] = field(default_factory=dict)   # skill → 覆盖率
    knowledge_refs: list[str]      = field(default_factory=list)    # KU_xxx
    package_path:  str             = ""        # abilities/xxx.hkap
    pkg_version:   str             = "1.0.0"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["extra"].update({
            "ability_key":   self.ability_key,
            "skills":        self.skills,
            "workflows":     self.workflows,
            "coverage":      self.coverage,
            "knowledge_refs": self.knowledge_refs,
            "package_path":  self.package_path,
            "pkg_version":   self.pkg_version,
        })
        return d


# ─────────────────────────────────────────
# Relation
# ─────────────────────────────────────────

@dataclass
class Relation:
    rel_id:   str
    from_ku:  str
    to_ku:    str
    rel_type: str          # RelationType value
    weight:   float        = 1.0
    source:   str          = ""    # EVD_xxx（可选，记录关系来源）
    created_at: str        = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "rel_id":     self.rel_id,
            "from_ku":    self.from_ku,
            "to_ku":      self.to_ku,
            "rel_type":   self.rel_type,
            "weight":     self.weight,
            "source":     self.source,
            "created_at": self.created_at,
        }


# ─────────────────────────────────────────
# Conflict Card  ← KEE 产出
# ─────────────────────────────────────────

@dataclass
class ConflictCard:
    conflict_id:         str
    claim_a_id:          str
    claim_b_id:          str
    domain:              str                = ""
    status:              ConflictStatus     = ConflictStatus.OPEN
    resolution_strategy: ResolutionStrategy = ResolutionStrategy.EVIDENCE_WEIGHT
    resolution_note:     str                = ""
    resolved_by:         Optional[str]      = None
    resolved_at:         Optional[str]      = None
    created_at:          str                = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "conflict_id":         self.conflict_id,
            "claim_a_id":          self.claim_a_id,
            "claim_b_id":          self.claim_b_id,
            "domain":              self.domain,
            "status":              self.status.value,
            "resolution_strategy": self.resolution_strategy.value,
            "resolution_note":     self.resolution_note,
            "resolved_by":         self.resolved_by,
            "resolved_at":         self.resolved_at,
            "created_at":          self.created_at,
        }


# ─────────────────────────────────────────
# Union type helper
# ─────────────────────────────────────────

KU = EntityKU | ConceptKU | FactKU | ClaimKU | EvidenceKU | AbilityKU
