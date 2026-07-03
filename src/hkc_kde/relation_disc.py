"""
hkc-kde / relation_disc.py
关系发现器：分析 KU 列表，自动建立 Relation。

v1 规则（确定性，不依赖 LLM）：
1. Entity → Concept  : CREATED_BY（名字出现在 Concept.definition 里）
2. Evidence → Claim  : SUPPORTS（来自同一 Chunk 的 Claim）
3. Concept → Concept : DERIVED_FROM（名字包含关系，如"量化投资"包含"投资"）
4. 跨文档去重        : 由 KEE 的 KnowledgeDeduplicator 负责（Principle 07），不在此处理
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hkc_core.models.ku import KU, Relation

logger = logging.getLogger(__name__)


class RelationshipDiscovery:

    def discover(
        self,
        new_kus:      list["KU"],
        existing_kus: list["KU"],
        id_gen,
    ) -> list["Relation"]:
        """
        分析新写入的 KU，建立与已有 KU 和内部 KU 的关系。
        返回 Relation 列表（未写入 GraphStore，由 Packager 批量写入）。
        """
        from hkc_core.models.ku import Relation
        from hkc_core.models.enums import RelationType

        all_kus = existing_kus + new_kus
        rels: list[Relation] = []

        # 1. Entity → Concept (CREATED_BY)
        rels += self._entity_concept(new_kus, all_kus, id_gen)

        # 2. Evidence → Claim (SUPPORTS)：来自同一文档的 Evidence 和 Claim
        rels += self._evidence_claim(new_kus, id_gen)

        # 3. Concept → Concept (DERIVED_FROM)
        rels += self._concept_hierarchy(new_kus, all_kus, id_gen)

        # 去重（同一对 from/to/type 只保留一条）
        seen  = set()
        dedup = []
        for r in rels:
            key = (r.from_ku, r.to_ku, r.rel_type)
            if key not in seen:
                seen.add(key)
                dedup.append(r)

        return dedup

    # ── 规则 1：Entity → Concept ─────────────────────────────

    def _entity_concept(
        self,
        new_kus:  list["KU"],
        all_kus:  list["KU"],
        id_gen,
    ) -> list["Relation"]:
        from hkc_core.models.ku import Relation, EntityKU, ConceptKU
        from hkc_core.models.enums import KUType

        entities = [ku for ku in new_kus if ku.ku_type.value == "Entity"]
        concepts = [ku for ku in all_kus  if ku.ku_type.value == "Concept"]
        rels = []

        for entity in entities:
            name_lower = entity.name.lower()
            for concept in concepts:
                defn  = getattr(concept, 'definition', '').lower()
                c_sum = concept.summary.lower()
                # 实体名出现在概念定义或摘要里 → CREATED_BY
                if name_lower and (name_lower in defn or name_lower in c_sum):
                    rels.append(Relation(
                        rel_id   = id_gen.next("Relation"),
                        from_ku  = entity.ku_id,
                        to_ku    = concept.ku_id,
                        rel_type = "CREATED_BY",
                        weight   = 0.8,
                    ))
        return rels

    # ── 规则 2：Evidence → Claim (SUPPORTS) ──────────────────

    def _evidence_claim(
        self,
        new_kus: list["KU"],
        id_gen,
    ) -> list["Relation"]:
        from hkc_core.models.ku import Relation, EvidenceKU, ClaimKU

        evidences = [ku for ku in new_kus if ku.ku_type.value == "Evidence"]
        claims    = [ku for ku in new_kus if ku.ku_type.value == "Claim"]
        rels = []

        # 同批次（同文档）的 Evidence 默认支持同批次的 Claim
        # 权重 0.6（比人工标注 1.0 低，后期可通过 KEE 调整）
        for evd in evidences:
            for clm in claims:
                rels.append(Relation(
                    rel_id   = id_gen.next("Relation"),
                    from_ku  = evd.ku_id,
                    to_ku    = clm.ku_id,
                    rel_type = "SUPPORTS",
                    weight   = 0.6,
                    source   = evd.ku_id,
                ))
        return rels

    # ── 规则 3：Concept → Concept (DERIVED_FROM) ─────────────

    def _concept_hierarchy(
        self,
        new_kus:  list["KU"],
        all_kus:  list["KU"],
        id_gen,
    ) -> list["Relation"]:
        from hkc_core.models.ku import Relation

        new_concepts = [ku for ku in new_kus if ku.ku_type.value == "Concept"]
        all_concepts = [ku for ku in all_kus  if ku.ku_type.value == "Concept"]
        rels = []

        for new_c in new_concepts:
            new_name = new_c.name.lower()
            for old_c in all_concepts:
                if old_c.ku_id == new_c.ku_id:
                    continue
                old_name = old_c.name.lower()
                # 新概念名包含旧概念名（且旧名 >= 2 字）→ DERIVED_FROM
                if (len(old_name) >= 2
                        and old_name in new_name
                        and old_name != new_name):
                    rels.append(Relation(
                        rel_id   = id_gen.next("Relation"),
                        from_ku  = new_c.ku_id,
                        to_ku    = old_c.ku_id,
                        rel_type = "DERIVED_FROM",
                        weight   = 0.7,
                    ))
        return rels
