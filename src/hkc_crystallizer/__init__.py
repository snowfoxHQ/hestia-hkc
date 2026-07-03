"""
hkc_crystallizer — 知识结晶器(HKC Integrations Layer)

Memory World → Knowledge World 的边界转换器。
把任何"可沉淀认知"事件(memory.matured / meeting.finished / research.finished …)
转化为 HKC 可接收的知识候选体。

架构铁律:
  Crystallizer is a knowledge candidate generator, not a knowledge identity resolver.
  KEE is the sole authority for knowledge deduplication and evolution.

用法:
    from hkc_crystallizer import (
        KnowledgeCrystallizer, HKCIngress, SystemBusEventSource,
    )
    from hkc_sdk import connect

    hkc = connect(container=hkc_container)

    crystallizer = KnowledgeCrystallizer(
        ingress = HKCIngress(hkc),                  # 只认 KnowledgeIngress 协议
        source  = SystemBusEventSource(system_bus), # 只认 KnowledgeEventSource 协议
        events  = ["memory.matured"],               # 未来可加 meeting.finished 等
    )
"""
from .crystallizer import KnowledgeCrystallizer, CrystallizeStats
from .candidate import (
    KnowledgeCandidate, CandidateEvidence,
    KnowledgeEventSource, KnowledgeIngress,
)
from .ingress import HKCIngress
from .adapters import (
    SystemBusEventSource, DEFAULT_TRANSLATORS, translate_memory_matured,
)

__version__ = "2.0.0"

__all__ = [
    "KnowledgeCrystallizer", "CrystallizeStats",
    "KnowledgeCandidate", "CandidateEvidence",
    "KnowledgeEventSource", "KnowledgeIngress",
    "HKCIngress",
    "SystemBusEventSource", "DEFAULT_TRANSLATORS", "translate_memory_matured",
]
