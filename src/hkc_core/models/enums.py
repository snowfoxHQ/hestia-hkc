"""
hkc-core / models / enums.py
所有枚举类型集中定义，整个 HKC 统一使用这里的常量。
"""
from enum import Enum


class KUType(str, Enum):
    ENTITY   = "Entity"
    CONCEPT  = "Concept"
    FACT     = "Fact"
    CLAIM    = "Claim"
    EVIDENCE = "Evidence"
    ABILITY  = "Ability"


class EntityType(str, Enum):
    PERSON       = "Person"
    ORGANIZATION = "Organization"
    PRODUCT      = "Product"
    EVENT        = "Event"
    PLACE        = "Place"


class ClaimStatus(str, Enum):
    PENDING    = "pending"     # 证据不足，暂不结论
    ACTIVE     = "active"      # 置信度 > 0.7，无冲突
    DISPUTED   = "disputed"    # 存在对立 Claim，置信度相近
    SUPERSEDED = "superseded"  # 被新研究推翻，历史归档
    REJECTED   = "rejected"    # 置信度 < 0.3，明确反驳


class RelationType(str, Enum):
    CREATED_BY   = "CREATED_BY"    # Entity → Concept
    SUPPORTS     = "SUPPORTS"      # Evidence → Claim
    CONTRADICTS  = "CONTRADICTS"   # Evidence → Claim
    MENTIONS     = "MENTIONS"      # Evidence → Entity
    BELONGS_TO   = "BELONGS_TO"    # Concept → Domain
    DERIVED_FROM = "DERIVED_FROM"  # Concept → Concept
    IMPLEMENTS   = "IMPLEMENTS"    # Ability → Skill
    USES         = "USES"          # Ability → Concept
    SUPERSEDES   = "SUPERSEDES"    # Claim → Claim
    INTERESTED_IN = "INTERESTED_IN" # UserProfile → KU (HMR bridge)


class ConflictStatus(str, Enum):
    OPEN     = "open"      # 等待裁决
    RESOLVED = "resolved"  # 已裁决
    DEFERRED = "deferred"  # 人工延后


class ResolutionStrategy(str, Enum):
    EVIDENCE_WEIGHT = "evidence_weight"  # 按 Evidence 数量和质量加权（默认）
    RECENCY         = "recency"          # 越新的研究权重越高
    AUTHORITY       = "authority"        # 按来源权威度
    MANUAL          = "manual"           # 人工标注裁决


class SourceType(str, Enum):
    BOOK    = "Book"
    PAPER   = "Paper"
    ARTICLE = "Article"
    SPEECH  = "Speech"
    DATASET = "Dataset"
    WEB     = "Web"
