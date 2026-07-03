"""
hkc_crystallizer / crystallizer.py
知识结晶器 (KnowledgeCrystallizer) —— 边界转换器 / Knowledge Candidate Generator

定位:Memory World → Knowledge World 之间的边界转换器(Boundary Translator)。
      属于 HKC Integrations Layer,不属于 HKC Core。

架构铁律:
  Crystallizer is a knowledge candidate generator, not a knowledge identity resolver.
  KEE is the sole authority for knowledge deduplication and evolution.

职责严格锁死为四件:
  1. 接收事件   (通过 KnowledgeEventSource 订阅)
  2. 事件筛选   (合法性 / 类型判断 / 可选二次复核)
  3. 事件级去重 (event_id / source_id,避免重复消费同一事件)
  4. 构造候选体 (KnowledgeCandidate,含结构化 Evidence + light fingerprint)

绝不负责(交给 HKC Core):
  - 知识抽取        → KDE
  - 知识身份判断/去重 → KEE(canonical fingerprint + KU 查重 + Evidence 合并)
  - 图谱更新        → KEE
  - Ability 生成     → ACE

  特别地:Crystallizer **永不查询 KU 是否存在**,**永不做合并/更新决策**。
  它计算的 light_fingerprint 只是候选体的结构标签,不参与任何知识决策。
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from .candidate import (
    KnowledgeCandidate, KnowledgeEventSource, KnowledgeIngress,
)
from .adapters import DEFAULT_TRANSLATORS, TranslatorFn

logger = logging.getLogger(__name__)


@dataclass
class CrystallizeStats:
    received:       int = 0
    crystallized:   int = 0
    skipped_dup:    int = 0
    skipped_filter: int = 0
    skipped_empty:  int = 0
    failed:         int = 0

    def as_dict(self) -> dict:
        return {
            "received": self.received, "crystallized": self.crystallized,
            "skipped_dup": self.skipped_dup, "skipped_filter": self.skipped_filter,
            "skipped_empty": self.skipped_empty, "failed": self.failed,
        }


class KnowledgeCrystallizer:
    """
    参数:
      ingress:   KnowledgeIngress,HKC 对外入口(如 HKCIngress)。
                 Crystallizer 只认这个协议,不认 KDE/KEE/ACE。
      source:    KnowledgeEventSource,事件来源(如 SystemBusEventSource)。
      events:    要订阅的事件名列表(默认只订阅 memory.matured)。
      translators: 事件名 → 翻译函数。默认用 DEFAULT_TRANSLATORS。
      candidate_filter: 可选 (KnowledgeCandidate)->bool,二次筛选(防御性复核)。
      auto_start: 构造时立即订阅(默认 True)。
    """

    def __init__(
        self,
        ingress: KnowledgeIngress,
        source: KnowledgeEventSource,
        *,
        events: Optional[list] = None,
        translators: Optional[dict] = None,
        candidate_filter: Optional[Callable[[KnowledgeCandidate], bool]] = None,
        auto_start: bool = True,
    ):
        self._ingress = ingress
        self._source = source
        self._events = events or ["memory.matured"]
        self._translators: dict[str, TranslatorFn] = dict(DEFAULT_TRANSLATORS)
        if translators:
            self._translators.update(translators)
        self._filter = candidate_filter
        self.stats = CrystallizeStats()
        self._seen_refs = set()
        self._started = False
        if auto_start:
            self.start()

    def start(self) -> None:
        """订阅所有配置的事件。幂等。"""
        if self._started:
            return
        for ev in self._events:
            self._source.subscribe(ev, self._make_handler(ev))
        self._started = True
        logger.info("KnowledgeCrystallizer 已启动,订阅事件: %s", self._events)

    def _make_handler(self, event_name: str):
        def _handler(payload: dict):
            self._on_event(event_name, payload)
        return _handler

    # ── 核心:收到一个事件 ──────────────────────────────────
    def _on_event(self, event_name: str, payload: dict) -> None:
        self.stats.received += 1

        # (2) 筛选:有无对应翻译器
        translator = self._translators.get(event_name)
        if translator is None:
            logger.debug("无翻译器,跳过事件: %s", event_name)
            self.stats.skipped_filter += 1
            return

        # 翻译 event → candidate
        try:
            candidate = translator(payload)
        except Exception as e:
            logger.warning("事件翻译失败 (%s): %s", event_name, e)
            self.stats.skipped_filter += 1
            return
        if candidate is None:
            self.stats.skipped_empty += 1
            return

        # (3) 事件级去重(基于 event_refs)
        ref = self._dedup_key(candidate)
        if ref and ref in self._seen_refs:
            self.stats.skipped_dup += 1
            return

        # (2') 二次筛选(防御性复核)
        if self._filter is not None:
            try:
                if not self._filter(candidate):
                    self.stats.skipped_filter += 1
                    return
            except Exception as e:
                logger.warning("二次筛选异常,保守跳过: %s", e)
                self.stats.skipped_filter += 1
                return

        # 内容非空
        if not candidate.text_for_ingest.strip():
            self.stats.skipped_empty += 1
            return

        # (4) candidate 已构造好(含结构化 Evidence + light fingerprint)
        #     → 交给 KnowledgeIngress 送入 HKC。到此为止,不做知识身份判断。
        try:
            self._ingress.ingest(candidate)
        except Exception as e:
            self.stats.failed += 1
            logger.error("候选体送入 HKC 失败 (refs=%s): %s",
                         candidate.event_refs, e)
            return

        # 成功后才记事件级去重(失败可重试)
        if ref:
            self._seen_refs.add(ref)
        self.stats.crystallized += 1
        logger.info("候选体已送入 HKC: refs=%s fp=%s",
                    candidate.event_refs, candidate.light_fingerprint)

    @staticmethod
    def _dedup_key(candidate: KnowledgeCandidate) -> str:
        """事件级去重键:优先 event_refs,退化用 light_fingerprint。"""
        if candidate.event_refs:
            return "|".join(candidate.event_refs)
        return candidate.light_fingerprint

    # ── 运维 ──────────────────────────────────────────────
    def get_stats(self) -> dict:
        return self.stats.as_dict()

    def reset_dedup(self) -> None:
        self._seen_refs.clear()
