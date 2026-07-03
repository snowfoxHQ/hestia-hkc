"""
hkc-api / schemas.py
API 请求 / 响应模型（Pydantic）。
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, Any


# ── Ingest ───────────────────────────────────────────────────

class IngestTextRequest(BaseModel):
    text:         str
    source:       str = "inline"
    source_title: str = ""
    source_year:  int = 0
    domain:       str = ""


class IngestURLRequest(BaseModel):
    url:          str
    source_title: str = ""
    source_year:  int = 0
    domain:       str = ""


class CrystallizeRequest(BaseModel):
    """外部系统经 Crystallizer 集成层推送的知识候选(边界转换入口)。"""
    content:       str
    title:         str = ""
    domain:        str = ""
    evidence_type: str = "document"   # memory | meeting | research | document | reflection ...
    source_id:     str = ""
    agent:         str = ""
    event_refs:    list[str] = []


class IngestResponse(BaseModel):
    ku_count:  int
    ku_ids:    list[str]
    counts:    dict[str, int] = Field(default_factory=dict)


# ── KU ───────────────────────────────────────────────────────

class KUResponse(BaseModel):
    ku_id:      str
    ku_type:    str
    name:       str
    summary:    str
    domain:     str
    confidence: float
    status:     str
    tags:       list[str]      = Field(default_factory=list)
    extra:      dict[str, Any] = Field(default_factory=dict)


class GraphResponse(BaseModel):
    """全量知识图谱：所有 KU + 所有关系 + 统计。前端星球一次拉取用。"""
    kus:       list[KUResponse]
    relations: list[dict[str, Any]] = Field(default_factory=list)
    stats:     dict[str, Any]       = Field(default_factory=dict)


# ── Search ───────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query:    str
    mode:     str = "hybrid"          # bm25 | vector | graph | hybrid
    top_k:    int = 10
    domain:   str = ""
    ku_types: Optional[list[str]] = None


class SearchHit(BaseModel):
    ku_id:   str
    score:   float
    mode:    str
    name:    str
    summary: str
    ku_type: str = ""


class SearchResponse(BaseModel):
    query:   str
    mode:    str
    hits:    list[SearchHit]


class NeighborsRequest(BaseModel):
    ku_id:     str
    max_depth: int = 2
    top_k:     int = 20
    rel_types: Optional[list[str]] = None


# ── Ability ──────────────────────────────────────────────────

class AbilityCompileRequest(BaseModel):
    ability_key: str


class CoverageReportResponse(BaseModel):
    ability_key:    str
    display_name:   str = ""            # 能力显示名(可中文),供前端列表展示
    domains:        list[str] = []      # 该能力参与覆盖匹配的领域列表
    can_compile:    bool
    coverage:       dict[str, float]
    missing_skills: list[str]
    ku_count:       int


class AbilityResponse(BaseModel):
    ability_key:  str
    display_name: str
    domain:       str
    coverage:     dict[str, float]
    skills:       list[dict]
    workflows:    list[dict]
    version:      str


# ── Conflict ─────────────────────────────────────────────────

class ConflictResponse(BaseModel):
    conflict_id:         str
    claim_a_id:          str
    claim_b_id:          str
    domain:              str
    status:              str
    resolution_strategy: str
    resolution_note:     str


class ConflictResolveRequest(BaseModel):
    winner_id:   str
    note:        str = ""
    resolved_by: str = "human"


# ── Stats ────────────────────────────────────────────────────

class StatsResponse(BaseModel):
    assembled:       bool
    data_dir:        str = ""          # 后端实际使用的数据目录(绝对路径),供前端显示/排查"连错目录"
    embedding:       str = ""          # 生效的向量后端名(Stub 无语义 / Local / TEI)
    total_kus:       int = 0
    ku_counts:       dict[str, int] = Field(default_factory=dict)
    total_relations: int = 0
    open_conflicts:  int = 0
    bm25_size:       int = 0
    vector_size:     int = 0
    abilities:       list[str] = Field(default_factory=list)
