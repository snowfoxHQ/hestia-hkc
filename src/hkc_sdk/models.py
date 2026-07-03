"""
hkc_sdk / models.py
SDK 对外数据模型。

这是给 SDK 用户的"门面"类型，刻意与内部 KU dataclass 解耦：
- 内部 KU 字段多、有枚举、随版本演进
- SDK 模型字段少而稳定，用户代码不会因内部重构而崩

所有模型都能从 dict（HTTP JSON / 直连 to_dict）构造，用 from_dict。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class KU:
    """知识单元的对外视图。"""
    ku_id:      str
    ku_type:    str
    name:       str
    summary:    str = ""
    domain:     str = ""
    confidence: float = 1.0
    status:     str = "active"
    tags:       list[str] = field(default_factory=list)
    extra:      dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "KU":
        return cls(
            ku_id      = d["ku_id"],
            ku_type    = d["ku_type"],
            name       = d["name"],
            summary    = d.get("summary", ""),
            domain     = d.get("domain", ""),
            confidence = d.get("confidence", 1.0),
            status     = d.get("status", "active"),
            tags       = d.get("tags", []),
            extra      = d.get("extra", {}),
        )

    @property
    def statement(self) -> str:
        """Claim / Fact 的陈述内容（如果有）。"""
        return self.extra.get("statement", "")


@dataclass
class SearchHit:
    ku_id:   str
    score:   float
    name:    str
    summary: str = ""
    mode:    str = ""
    ku_type: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "SearchHit":
        return cls(
            ku_id   = d["ku_id"],
            score   = d.get("score", 0.0),
            name    = d.get("name", ""),
            summary = d.get("summary", ""),
            mode    = d.get("mode", ""),
            ku_type = d.get("ku_type", ""),
        )


@dataclass
class Skill:
    skill_key:    str
    display_name: str
    coverage:     float = 0.0
    concept_hits: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Skill":
        return cls(
            skill_key    = d.get("skill_key", ""),
            display_name = d.get("display_name", ""),
            coverage     = d.get("coverage", 0.0),
            concept_hits = d.get("concept_hits", []),
        )


@dataclass
class Ability:
    ability_key:  str
    display_name: str
    domain:       str
    coverage:     dict[str, float] = field(default_factory=dict)
    skills:       list[Skill] = field(default_factory=list)
    workflows:    list[dict] = field(default_factory=list)
    version:      str = "1.0.0"

    @classmethod
    def from_dict(cls, d: dict) -> "Ability":
        return cls(
            ability_key  = d["ability_key"],
            display_name = d.get("display_name", ""),
            domain       = d.get("domain", ""),
            coverage     = d.get("coverage", {}),
            skills       = [Skill.from_dict(s) for s in d.get("skills", [])],
            workflows    = d.get("workflows", []),
            version      = d.get("version", "1.0.0"),
        )

    def skill_context(self, skill_key: str) -> Skill | None:
        """获取某个 Skill 的上下文，Agent 用。"""
        for s in self.skills:
            if s.skill_key == skill_key:
                return s
        return None


@dataclass
class CoverageReport:
    ability_key:    str
    can_compile:    bool
    coverage:       dict[str, float] = field(default_factory=dict)
    missing_skills: list[str] = field(default_factory=list)
    ku_count:       int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "CoverageReport":
        return cls(
            ability_key    = d["ability_key"],
            can_compile    = d.get("can_compile", False),
            coverage       = d.get("coverage", {}),
            missing_skills = d.get("missing_skills", []),
            ku_count       = d.get("ku_count", 0),
        )


@dataclass
class Conflict:
    conflict_id:         str
    claim_a_id:          str
    claim_b_id:          str
    domain:              str = ""
    status:              str = "open"
    resolution_strategy: str = ""
    resolution_note:     str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Conflict":
        return cls(
            conflict_id         = d["conflict_id"],
            claim_a_id          = d["claim_a_id"],
            claim_b_id          = d["claim_b_id"],
            domain              = d.get("domain", ""),
            status              = d.get("status", "open"),
            resolution_strategy = d.get("resolution_strategy", ""),
            resolution_note     = d.get("resolution_note", ""),
        )


@dataclass
class IngestResult:
    ku_count: int
    ku_ids:   list[str] = field(default_factory=list)
    counts:   dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "IngestResult":
        return cls(
            ku_count = d.get("ku_count", 0),
            ku_ids   = d.get("ku_ids", []),
            counts   = d.get("counts", {}),
        )
