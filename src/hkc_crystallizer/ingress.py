"""
hkc_crystallizer / ingress.py
HKC 知识入口适配器。

实现 KnowledgeIngress 协议,是 Crystallizer 与 HKC 内部(KDE/KEE/ACE)
之间的唯一耦合点。Crystallizer 只认 KnowledgeIngress,不认 KDE。

设计意图:
  KDE 的接口(ingest_text 签名)是 KDE 的内部契约,不是 HKC 的稳定对外契约。
  若 Crystallizer 直接调 ingest_text,KDE 重构就会波及 Crystallizer。
  HKCIngress 把这层耦合收敛到一处:KDE 变了,只改这里。

  candidate 的结构化 Evidence 在这里翻译成 HKC 能理解的来源信息。
  注:第二层"知识级去重 + Evidence 合并"由 KEE 负责,不在此处 ——
      本适配器只负责"把候选体送进 HKC",不做任何知识身份判断。
"""
from __future__ import annotations
import logging
from typing import Any

from .candidate import KnowledgeCandidate, KnowledgeIngress

logger = logging.getLogger(__name__)


class HKCIngress:
    """
    把 KnowledgeCandidate 送入 HKC 的适配器。

    hkc_ingest_fn: 实际的 HKC 摄入入口。可为:
      - HKC SDK DirectClient / HTTPClient(有 ingest_text 方法)
      - KDE 实例(有 ingest_text 方法)
      - 自定义 callable(text, source=, source_title=, domain=) -> result
    """

    def __init__(self, hkc_ingest_fn: Any):
        self._ingest = self._resolve(hkc_ingest_fn)

    @staticmethod
    def _resolve(fn: Any):
        if callable(fn) and not hasattr(fn, "ingest_text"):
            return fn
        if hasattr(fn, "ingest_text"):
            return fn.ingest_text
        raise TypeError(
            "hkc_ingest_fn 必须可调用,或带 ingest_text 方法"
            "(HKC SDK 客户端 / KDE 实例)"
        )

    def ingest(self, candidate: KnowledgeCandidate) -> Any:
        """实现 KnowledgeIngress.ingest。"""
        text = candidate.text_for_ingest.strip()
        if not text:
            return None

        # 结构化 Evidence → 来源串(供 KDE 当前接口使用)。
        # 注:完整结构化 Evidence 进图谱由后续 KEE 演化步骤处理,
        #    这里先把主来源信息传给 KDE,保证可追溯不丢。
        source = self._primary_source(candidate)
        source_title = candidate.title or self._fallback_title(candidate)

        return self._ingest(
            text,
            source=source,
            source_title=source_title,
            domain=candidate.domain_hint or "",
        )

    @staticmethod
    def _primary_source(candidate: KnowledgeCandidate) -> str:
        """从首条 Evidence 构造来源标识(保持可追溯)。"""
        if not candidate.evidence:
            return "knowledge_candidate"
        e = candidate.evidence[0]
        parts = [e.evidence_type or "candidate"]
        if e.agent:
            parts.append(f"agent:{e.agent}")
        if e.source_id:
            parts.append(e.source_id)
        return ":".join(parts)

    @staticmethod
    def _fallback_title(candidate: KnowledgeCandidate) -> str:
        if candidate.event_refs:
            return f"候选 {candidate.event_refs[0]}"
        return "知识候选"


# 协议一致性断言(显式声明 HKCIngress 实现 KnowledgeIngress)
_: KnowledgeIngress = HKCIngress.__new__(HKCIngress)
