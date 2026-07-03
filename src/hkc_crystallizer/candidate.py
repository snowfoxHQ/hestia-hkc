"""
hkc_crystallizer / candidate.py
知识候选体 (KnowledgeCandidate) 与边界协议。

这是 Memory World → Knowledge World 的边界数据结构与契约定义。

核心理念:
  Crystallizer 处理的不是"记忆",而是"任何可沉淀认知"——
  来源可以是 memory.matured、meeting.finished、research.finished、
  document.ingested、agent.reflection.completed …

  因此边界数据结构叫 KnowledgeCandidate(知识候选体),不叫 Memory。
  这样 HKC 永远不会被 HMR 绑定死。

职责边界(架构铁律):
  Crystallizer is a knowledge candidate generator, not a knowledge identity resolver.
  KEE is the sole authority for knowledge deduplication and evolution.

  → Candidate 携带 light_fingerprint(结构哈希,仅作标签),
    但**不**参与"是否已存在、是否合并"的决策。
    那个决策(canonical fingerprint + KU 查重 + Evidence 合并)是 KEE 的天职。
"""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, Callable, Any, Optional, runtime_checkable


# ── 结构化 Evidence ──────────────────────────────────────────

@dataclass
class CandidateEvidence:
    """
    候选体携带的结构化来源证据。

    取代旧版的 source="hmr_memory:agent:finance:mem_xxx" 字符串拼接 ——
    结构化后可进图谱,形成 KU ← Evidence ← (Memory/Meeting/...) 的可追溯链。

    未来查询"为什么系统认为用户长期关注 Agent 架构",可顺着这条链回溯。
    """
    evidence_type: str                       # memory | meeting | research | document | reflection ...
    source_id:     str                       # 原始单元 ID(memory_id / meeting_id / ...)
    agent:         Optional[str] = None       # 产出该认知的 Agent
    confidence:    float = 1.0
    timestamp:     str = ""                   # ISO8601
    extra:         dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "evidence_type": self.evidence_type,
            "source_id":     self.source_id,
            "agent":         self.agent,
            "confidence":    self.confidence,
            "timestamp":     self.timestamp,
            "extra":         dict(self.extra),
        }


# ── 知识候选体 ───────────────────────────────────────────────

@dataclass
class KnowledgeCandidate:
    """
    进入 HKC 前的知识候选体 —— 边界转换的产物。

    由 Crystallizer 构造,经 KnowledgeIngress 送入 HKC。
    它是"事件视角"的产物;到了 KEE 才转为"知识语义视角"。
    """
    content:             str                              # 待结晶的文本
    title:               str = ""
    evidence:            list[CandidateEvidence] = field(default_factory=list)
    event_refs:          list[str] = field(default_factory=list)   # 触发事件的引用(event_id / memory_id)
    domain_hint:         str = ""                          # 领域提示(非强制)
    light_fingerprint:   str = ""                          # 结构哈希(仅标签,KEE 才算 canonical)

    def __post_init__(self):
        if not self.light_fingerprint:
            self.light_fingerprint = self.compute_light_fingerprint(self.content)

    @staticmethod
    def compute_light_fingerprint(content: str) -> str:
        """
        轻量结构指纹:归一化(去空白/小写)后取 sha256 前 16 位。

        这是"事件视角"的粗去重标签 —— 只能识别"字面几乎相同"。
        语义层面的同义归并(canonical fingerprint)是 KEE 的职责,不在这里做。
        """
        norm = re.sub(r"\s+", " ", (content or "").strip().lower())
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

    @property
    def text_for_ingest(self) -> str:
        if self.title and self.content:
            return f"{self.title}\n\n{self.content}"
        return self.content or self.title

    def to_dict(self) -> dict:
        return {
            "content":           self.content,
            "title":             self.title,
            "evidence":          [e.to_dict() for e in self.evidence],
            "event_refs":        list(self.event_refs),
            "domain_hint":       self.domain_hint,
            "light_fingerprint": self.light_fingerprint,
        }


# ── 协议:知识事件来源 ───────────────────────────────────────

@runtime_checkable
class KnowledgeEventSource(Protocol):
    """
    知识事件来源协议。

    不绑定"记忆"——任何能产出"可沉淀认知"事件的系统都可实现它:
    HMR(memory.matured)、会议系统(meeting.finished)、
    研究 Agent(research.finished)、文档系统(document.ingested)…

    按 event_name 订阅,handler 收到该事件的 payload(dict)。
    """
    def subscribe(self, event_name: str, handler: Callable[[dict], None]) -> None:
        ...


# ── 协议:知识入口 ───────────────────────────────────────────

@runtime_checkable
class KnowledgeIngress(Protocol):
    """
    HKC 对外的知识入口契约。

    Crystallizer 只认这个协议,不知道 KDE/KEE/ACE 的存在。
    具体实现(HKCIngress)内部再去调 kde.ingest_text 等 —— 
    这样 KDE 重构不会波及 Crystallizer。

    返回:本次摄入产出的 KU 数量(或可转为数量的结果)。
    """
    def ingest(self, candidate: KnowledgeCandidate) -> Any:
        ...
