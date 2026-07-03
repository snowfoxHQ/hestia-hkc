"""
hkc-kde / models.py
KDE 内部数据结构。
ParsedDocument → ExtractionResult → KU（交给 Packager）
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Section:
    """文档的最小文本单元，对应一个段落或小节。"""
    section_id: str
    content:    str
    tables:     list[dict] = field(default_factory=list)  # 表格内容
    images:     list[str]  = field(default_factory=list)  # OCR 文字


@dataclass
class Chapter:
    chapter_id: str
    title:      str
    sections:   list[Section] = field(default_factory=list)


@dataclass
class ParsedDocument:
    doc_id:   str
    title:    str
    source:   str           # 文件路径或 URL
    doc_type: str           # PDF | MARKDOWN | WEB | TXT
    chapters: list[Chapter] = field(default_factory=list)
    metadata: dict          = field(default_factory=dict)

    def all_sections(self) -> list[Section]:
        """展平所有章节，返回全部 Section 列表。"""
        result = []
        for ch in self.chapters:
            result.extend(ch.sections)
        return result

    def full_text(self) -> str:
        """返回全文拼接，用于整体摘要等场景。"""
        parts = []
        for ch in self.chapters:
            if ch.title:
                parts.append(ch.title)
            for sec in ch.sections:
                if sec.content.strip():
                    parts.append(sec.content)
        return "\n\n".join(parts)


@dataclass
class RawExtraction:
    """LLM 从单个 Chunk 提取的原始结果（未分配 ID）。"""
    facts:    list[dict] = field(default_factory=list)
    claims:   list[dict] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    concepts: list[dict] = field(default_factory=list)
    source_hint: str = ""   # 所在章节标题，用于 Evidence 溯源


@dataclass
class ExtractionResult:
    """整个文档的提取结果汇总。"""
    doc_id:   str
    source:   str
    items:    list[RawExtraction] = field(default_factory=list)

    def all_facts(self)    -> list[dict]: return [f for r in self.items for f in r.facts]
    def all_claims(self)   -> list[dict]: return [c for r in self.items for c in r.claims]
    def all_entities(self) -> list[dict]: return [e for r in self.items for e in r.entities]
    def all_concepts(self) -> list[dict]: return [c for r in self.items for c in r.concepts]
