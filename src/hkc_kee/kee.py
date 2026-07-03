"""
hkc-kee / kee.py
Knowledge Evolution Engine

职责：
1. 监听 knowledge.created 事件
2. 新 Claim 进入时与已有 Claim 做冲突检测
3. 生成 Conflict Card，运行裁决策略
4. 更新 Claim 状态（pending → active / disputed / superseded）
"""
# TODO: Replace with proper package install (pyproject.toml) before v1 release

from datetime import datetime, timezone
from dataclasses import dataclass

from hkc_core.graph.base import GraphStore
from hkc_core.models.ku import ClaimKU, ConflictCard
from hkc_core.models.enums import (
    ClaimStatus, ConflictStatus, ResolutionStrategy, KUType
)
from hkc_core.utils.id_gen import IDGenerator
from hkc_kep.event_bus import EventBus, KEPEvents
from .dedup import KnowledgeDeduplicator


# ── 语义相似度（轻量版，不依赖 GPU）────────────────────────

class _SimpleEmbedder:
    """
    v1 使用关键词重叠做简单语义判断。
    v2 替换为 sentence-transformers。
    接口保持不变：score() 返回 -1.0 ~ 1.0。
    """

    def score(self, text_a: str, text_b: str) -> float:
        """
        返回语义方向分数：
         > 0.3  → 同向（互相支持）
         -0.3 ~ 0.3 → 无关
         < -0.3 → 反向（互相矛盾）

        v1 简单实现：
        - 同向关键词出现多 → 正分
        - 否定词 + 相同主题 → 负分
        """
        a_words = set(text_a.lower().split())
        b_words = set(text_b.lower().split())

        negation = {"not", "no", "never", "false", "bad",
                    "harmful", "worse", "不", "非", "无", "差", "不好"}

        a_neg = bool(a_words & negation)
        b_neg = bool(b_words & negation)

        # 去除否定词后的主题词重叠
        a_topic = a_words - negation
        b_topic = b_words - negation
        overlap = len(a_topic & b_topic)

        if overlap == 0:
            return 0.0

        # 一方否定、一方不否定 → 反向
        if a_neg != b_neg:
            return -0.5

        # 双方都否定或都不否定 → 同向
        return min(0.8, overlap * 0.15)


# ── Claim 状态机 ─────────────────────────────────────────────

class ClaimStateMachine:
    """
    管理 Claim 的五种状态流转。

    pending    → active    : 找到支持 Evidence，confidence > 0.7
    pending    → disputed  : 对立 Claim，置信度相近
    pending    → rejected  : Evidence 明确反驳，confidence < 0.3
    active     → disputed  : 出现对立 Claim，置信度差 < 0.15
    active     → superseded: 新研究明确推翻
    disputed   → active    : 新 Evidence 倾向一方，差值 > 0.2
    """

    def transition(
        self,
        claim: ClaimKU,
        new_confidence: float,
        has_conflict: bool,
        conflict_gap: float = 0.0,
    ) -> ClaimStatus:
        current = claim.claim_status
        conf    = new_confidence

        if current == ClaimStatus.PENDING:
            if conf > 0.7 and not has_conflict:
                return ClaimStatus.ACTIVE
            if has_conflict and conflict_gap < 0.15:
                return ClaimStatus.DISPUTED
            if conf < 0.3:
                return ClaimStatus.REJECTED

        elif current == ClaimStatus.ACTIVE:
            if has_conflict and conflict_gap < 0.15:
                return ClaimStatus.DISPUTED
            if conf < 0.3:
                return ClaimStatus.SUPERSEDED

        elif current == ClaimStatus.DISPUTED:
            if conflict_gap > 0.2:
                return ClaimStatus.ACTIVE

        return current  # 无变化


# ── KEE 主引擎 ───────────────────────────────────────────────

class KnowledgeEvolutionEngine:

    def __init__(
        self,
        graph_store: GraphStore,
        event_bus:   EventBus,
        id_gen:      IDGenerator,
        similarity_threshold: float = 0.75,
        auto_resolve_gap:     float = 0.20,
    ):
        self.store   = graph_store
        self.bus     = event_bus
        self.id_gen  = id_gen
        self.embedder = _SimpleEmbedder()
        self.state_machine = ClaimStateMachine()
        self.similarity_threshold = similarity_threshold
        self.auto_resolve_gap     = auto_resolve_gap

        # 注册监听
        self.bus.subscribe(KEPEvents.KNOWLEDGE_CREATED, self._on_knowledge_created)

        # 知识级去重器(KEE 是知识身份唯一权威)
        self.dedup = KnowledgeDeduplicator(graph_store)

    # ── 事件入口 ──────────────────────────────────────────────

    def _on_knowledge_created(self, event: dict):
        ku_ids = event.get("payload", {}).get("ku_ids", [])
        for ku_id in ku_ids:
            ku = self.store.get(ku_id)
            if ku is None:
                continue

            # ── 知识级去重(所有 KU 类型,先于一切处理)──
            # 命中已有同一知识 → Evidence 已合并入旧 KU → 删除这个重复新 KU,不再处理
            existing_id = self.dedup.check_and_merge(ku)
            if existing_id is not None:
                try:
                    self.store.delete(ku_id)
                except Exception:
                    pass
                continue

            # 未命中(是新知识)→ 按类型走后续演化
            if ku.ku_type == KUType.CLAIM:
                self._process_new_claim(ku)

    # ── 核心流程 ──────────────────────────────────────────────

    def _process_new_claim(self, new_claim: ClaimKU):
        """
        新 Claim 进入时的完整处理流程：
        1. 搜索同领域已有 Claim
        2. 计算语义方向
        3. 冲突 → 生成 Conflict Card → 尝试裁决
        4. 非冲突 → 融合 Evidence，更新置信度
        """
        existing_claims = self.store.find_claims_by_domain(
            new_claim.domain or ""
        )
        # 排除自身
        existing_claims = [c for c in existing_claims
                           if c.ku_id != new_claim.ku_id]

        conflict_found = False

        for old_claim in existing_claims:
            score = self.embedder.score(
                new_claim.statement,
                old_claim.statement,
            )

            if score < -0.3:
                # 语义方向相反 → 冲突
                conflict_found = True
                self._handle_conflict(new_claim, old_claim)

            elif score > 0.3:
                # 同向 → 融合 Evidence，强化置信度
                self._merge_evidence(new_claim, old_claim)

        # 更新 new_claim 自身状态
        has_conflict  = conflict_found
        conflict_gap  = 0.0
        if conflict_found:
            # 找到最强对立 Claim 的置信度差
            conflict_gap = self._max_conflict_gap(new_claim, existing_claims)

        new_status = self.state_machine.transition(
            new_claim,
            new_confidence=new_claim.confidence,
            has_conflict=has_conflict,
            conflict_gap=conflict_gap,
        )

        if new_status != new_claim.claim_status:
            old_status = new_claim.claim_status
            new_claim.claim_status = new_status
            self.store.put(new_claim)

            self.bus.publish({
                "event":   KEPEvents.CLAIM_STATUS_CHANGED,
                "source":  "KEE",
                "payload": {
                    "ku_id":      new_claim.ku_id,
                    "old_status": old_status.value,
                    "new_status": new_status.value,
                }
            })

    def _handle_conflict(self, new_claim: ClaimKU, old_claim: ClaimKU):
        """生成 Conflict Card 并尝试自动裁决。"""
        # 避免重复创建同一对 Claim 的冲突卡
        existing = self.store.list_conflicts(status="open", domain=new_claim.domain)
        for card in existing:
            if (card.claim_a_id in (new_claim.ku_id, old_claim.ku_id) and
                    card.claim_b_id in (new_claim.ku_id, old_claim.ku_id)):
                return  # 已存在

        card = ConflictCard(
            conflict_id=self.id_gen.next("Conflict"),
            claim_a_id=old_claim.ku_id,
            claim_b_id=new_claim.ku_id,
            domain=new_claim.domain or old_claim.domain,
            status=ConflictStatus.OPEN,
            resolution_strategy=ResolutionStrategy.EVIDENCE_WEIGHT,
        )
        self.store.save_conflict(card)

        self.bus.publish({
            "event":   KEPEvents.CONFLICT_DETECTED,
            "source":  "KEE",
            "payload": {
                "conflict_id": card.conflict_id,
                "claim_a":     old_claim.ku_id,
                "claim_b":     new_claim.ku_id,
                "domain":      card.domain,
            }
        })

        # 尝试自动裁决
        self._try_auto_resolve(card, old_claim, new_claim)

    def _try_auto_resolve(
        self,
        card: ConflictCard,
        claim_a: ClaimKU,
        claim_b: ClaimKU,
    ):
        """
        evidence_weight 策略：
        置信度差 > auto_resolve_gap (0.20) 才自动裁决。
        差距不足 → 保持 open，等待人工介入。
        """
        gap = abs(claim_a.confidence - claim_b.confidence)
        if gap < self.auto_resolve_gap:
            return  # 差距不足，不自动裁决

        winner = claim_a if claim_a.confidence >= claim_b.confidence else claim_b
        loser  = claim_b if winner.ku_id == claim_a.ku_id else claim_a

        # 更新状态
        winner.claim_status = ClaimStatus.ACTIVE
        loser.claim_status  = ClaimStatus.SUPERSEDED
        self.store.put(winner)
        self.store.put(loser)

        # 关闭冲突卡
        card.status      = ConflictStatus.RESOLVED
        card.resolved_at = datetime.now(timezone.utc).isoformat()
        card.resolution_note = (
            f"自动裁决：{winner.ku_id} 置信度更高 "
            f"({winner.confidence:.2f} vs {loser.confidence:.2f})"
        )
        self.store.save_conflict(card)

        self.bus.publish({
            "event":   KEPEvents.CONFLICT_RESOLVED,
            "source":  "KEE",
            "payload": {
                "conflict_id": card.conflict_id,
                "winner":      winner.ku_id,
                "loser":       loser.ku_id,
            }
        })

    def _merge_evidence(self, new_claim: ClaimKU, old_claim: ClaimKU):
        """
        新 Claim 与旧 Claim 同向时：
        把新 Claim 的 Evidence 合并到旧 Claim，提升其置信度。
        """
        if not new_claim.supports:
            return
        added = [e for e in new_claim.supports if e not in old_claim.supports]
        if not added:
            return
        old_claim.supports.extend(added)
        # 每多一条 Evidence，置信度 +0.02，上限 0.98
        old_claim.confidence = min(0.98, old_claim.confidence + len(added) * 0.02)
        self.store.put(old_claim)

    def _max_conflict_gap(
        self,
        claim: ClaimKU,
        others: list[ClaimKU],
    ) -> float:
        """找到与 claim 最接近（最危险）的对立 Claim 的置信度差。"""
        gaps = []
        for other in others:
            score = self.embedder.score(claim.statement, other.statement)
            if score < -0.3:
                gaps.append(abs(claim.confidence - other.confidence))
        return min(gaps) if gaps else 1.0

    # ── 手动裁决接口（供 API 层调用）─────────────────────────

    def manual_resolve(
        self,
        conflict_id: str,
        winner_id:   str,
        note:        str = "",
        resolved_by: str = "human",
    ) -> bool:
        card = self.store.get_conflict(conflict_id)
        if not card or card.status != ConflictStatus.OPEN:
            return False

        loser_id = (
            card.claim_b_id
            if winner_id == card.claim_a_id
            else card.claim_a_id
        )
        winner = self.store.get(winner_id)
        loser  = self.store.get(loser_id)
        if not winner or not loser:
            return False

        winner.claim_status = ClaimStatus.ACTIVE
        loser.claim_status  = ClaimStatus.SUPERSEDED
        self.store.put(winner)
        self.store.put(loser)

        card.status      = ConflictStatus.RESOLVED
        card.resolved_by = resolved_by
        card.resolved_at = datetime.now(timezone.utc).isoformat()
        card.resolution_note = note or f"人工裁决：{winner_id} 胜出"
        self.store.save_conflict(card)

        self.bus.publish({
            "event":   KEPEvents.CONFLICT_RESOLVED,
            "source":  "KEE",
            "payload": {
                "conflict_id": conflict_id,
                "winner":      winner_id,
                "loser":       loser_id,
                "by":          resolved_by,
            }
        })
        return True
