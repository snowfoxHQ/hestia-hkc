"""
hkc_crystallizer / adapters.py
事件适配器:把各类"可沉淀认知"事件转为 KnowledgeCandidate。

KnowledgeEventSource 协议本身在 candidate.py 定义。
这里提供:
  - SystemBusEventSource: 基于 HestiaOS SystemBus 的事件来源(实现 KnowledgeEventSource)
  - EventToCandidate:    把事件 payload 翻译成 KnowledgeCandidate 的可插拔翻译器

为什么要 EventToCandidate 这一层:
  不同事件的 payload 结构不同(memory.matured 有 memory_id/confidence,
  meeting.finished 可能有 meeting_id/participants)。翻译逻辑按事件类型可插拔,
  新增事件类型只需注册一个翻译函数,不改 Crystallizer 主体。
"""
from __future__ import annotations
import logging
from typing import Callable, Any, Optional

from .candidate import (
    KnowledgeCandidate, CandidateEvidence, KnowledgeEventSource,
)

logger = logging.getLogger(__name__)


# ── 事件 → 候选体 翻译器 ─────────────────────────────────────

# 翻译函数签名: (payload: dict) -> Optional[KnowledgeCandidate]
TranslatorFn = Callable[[dict], Optional[KnowledgeCandidate]]


def translate_memory_matured(payload: dict) -> Optional[KnowledgeCandidate]:
    """memory.matured 事件 → KnowledgeCandidate。"""
    content = (payload.get("content") or "").strip()
    title = (payload.get("title") or "").strip()
    if not content and not title:
        return None

    ev = CandidateEvidence(
        evidence_type = "memory",
        source_id     = payload.get("memory_id", ""),
        agent         = payload.get("agent_id"),
        confidence    = payload.get("confidence", 1.0),
        extra         = {
            "memory_type":     payload.get("memory_type", ""),
            "temporal_weight": payload.get("temporal_weight", 0.0),
            "matured_reason":  payload.get("reason", ""),
            "shared":          payload.get("shared", False),
        },
    )
    return KnowledgeCandidate(
        content     = content or title,
        title       = title,
        evidence    = [ev],
        event_refs  = [payload.get("memory_id", "")] if payload.get("memory_id") else [],
        domain_hint = payload.get("domain_hint", ""),
    )


# 默认翻译器注册表:事件名 → 翻译函数
DEFAULT_TRANSLATORS: dict[str, TranslatorFn] = {
    "memory.matured": translate_memory_matured,
    # 未来扩展(只需加翻译函数,不改 Crystallizer):
    #   "meeting.finished":  translate_meeting_finished,
    #   "research.finished": translate_research_finished,
    #   "document.ingested": translate_document_ingested,
}


# ── SystemBus 事件来源 ───────────────────────────────────────

class SystemBusEventSource:
    """
    基于 HestiaOS SystemBus 的知识事件来源,实现 KnowledgeEventSource 协议。

    bus: 任何提供 subscribe(channel, handler) 的对象(HestiaOS SystemBus)。
    """

    def __init__(self, bus):
        self._bus = bus

    def subscribe(self, event_name: str, handler: Callable[[dict], None]) -> None:
        def _bus_handler(msg: Any):
            # SystemBus handler 收到 BusMessage(有 .payload);也兼容直接 dict
            payload = getattr(msg, "payload", None)
            if payload is None and isinstance(msg, dict):
                payload = msg
            if payload is not None:
                handler(payload)
        self._bus.subscribe(event_name, _bus_handler)


# 协议一致性断言(显式声明 SystemBusEventSource 实现 KnowledgeEventSource)
_: KnowledgeEventSource = SystemBusEventSource.__new__(SystemBusEventSource)
