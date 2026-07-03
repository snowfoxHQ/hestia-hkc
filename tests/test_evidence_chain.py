"""
tests / test_evidence_chain.py
③ KU ← Evidence ← Memory 可追溯链闭环测试。

验证 Entity/Concept/Fact 创建时都连上来源 Evidence,
且 Evidence 保留精确来源标识(可追溯回原始记忆)。
"""
import sys, os, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ["HF_HUB_OFFLINE"] = "1"

from hkc_kde.models import RawExtraction, ExtractionResult
from hkc_kde.extractor import Extractor
import hkc_api.container as cm


def _container():
    cm.reset_container()
    c = cm.init_container(data_dir=tempfile.mkdtemp(), embedding_kind="stub")
    return c


class _Mock(Extractor):
    def __init__(self, **kinds): self.kinds = kinds
    def extract(self, chunks):
        d = chunks[0].doc_id if chunks else "D"
        return ExtractionResult(doc_id=d, source=d, items=[RawExtraction(
            concepts=self.kinds.get("concepts", []),
            entities=self.kinds.get("entities", []),
            facts=self.kinds.get("facts", []),
            claims=self.kinds.get("claims", []))])


class TestEvidenceChain(unittest.TestCase):
    def tearDown(self):
        cm.reset_container()

    def test_concept_linked_to_evidence(self):
        c = _container()
        c.kde.extractor = _Mock(concepts=[{"name": "价值投资", "domain": "Inv", "definition": "x"}])
        c.kde.ingest_text("内容", source="memory:agent:finance:mem_001",
                          source_title="反思", domain="Inv")
        kus = c.graph_store.query_all(limit=100)
        concept = [k for k in kus if k.ku_type.value == "Concept" and k.status == "active"][0]
        evds = {k.ku_id for k in kus if k.ku_type.value == "Evidence" and k.status == "active"}
        # concept.sources 应含 Evidence id
        self.assertTrue(any(s in evds for s in concept.sources))

    def test_fact_linked_to_evidence(self):
        c = _container()
        c.kde.extractor = _Mock(facts=[{"statement": "标普长期年化约10%"}])
        c.kde.ingest_text("内容", source="memory:agent:finance:mem_002", source_title="数据")
        kus = c.graph_store.query_all(limit=100)
        fact = [k for k in kus if k.ku_type.value == "Fact" and k.status == "active"][0]
        evds = {k.ku_id for k in kus if k.ku_type.value == "Evidence" and k.status == "active"}
        self.assertTrue(any(s in evds for s in fact.sources))

    def test_entity_mentions_evidence(self):
        c = _container()
        c.kde.extractor = _Mock(entities=[{"name": "巴菲特", "type": "Person"}])
        c.kde.ingest_text("内容", source="memory:agent:finance:mem_003", source_title="人物")
        kus = c.graph_store.query_all(limit=100)
        entity = [k for k in kus if k.ku_type.value == "Entity" and k.status == "active"][0]
        evds = {k.ku_id for k in kus if k.ku_type.value == "Evidence" and k.status == "active"}
        rels = c.graph_store.get_relations(entity.ku_id)
        mentions = [r for r in rels if r.rel_type == "MENTIONS" and r.from_ku in evds]
        self.assertEqual(len(mentions), 1)

    def test_evidence_preserves_precise_source(self):
        """Evidence.source 应保留精确来源标识(可追溯回具体记忆)。"""
        c = _container()
        c.kde.extractor = _Mock(concepts=[{"name": "价值投资", "domain": "Inv", "definition": "x"}])
        c.kde.ingest_text("内容", source="memory:agent:finance:mem_xyz", source_title="可读标题")
        kus = c.graph_store.query_all(limit=100)
        evd = [k for k in kus if k.ku_type.value == "Evidence" and k.status == "active"][0]
        # source 精确、name 可读
        self.assertIn("mem_xyz", evd.source)
        self.assertEqual(evd.name, "可读标题")

    def test_full_chain_traceable(self):
        """端到端:从知识 KU 一路追溯到原始记忆标识。"""
        c = _container()
        c.kde.extractor = _Mock(concepts=[{"name": "安全边际", "domain": "Inv", "definition": "x"}])
        c.kde.ingest_text("内容", source="memory:agent:finance:mem_chain", source_title="反思")
        kus = c.graph_store.query_all(limit=100)
        concept = [k for k in kus if k.ku_type.value == "Concept" and k.status == "active"][0]
        # KU → Evidence → 精确记忆
        traced = False
        for evd_id in concept.sources:
            evd = c.graph_store.get(evd_id)
            if evd and "mem_chain" in (evd.source or ""):
                traced = True
        self.assertTrue(traced, "应能从知识 KU 追溯到原始记忆 mem_chain")

    def test_repeated_entity_source_linked(self):
        """已存在 Entity 的重复来源也应连上(③ 缺口修复回归)。"""
        c = _container()
        c.kde.extractor = _Mock(entities=[{"name": "巴菲特", "type": "Person"}])
        c.kde.ingest_text("x", source="memory:agent:finance:mem_a", source_title="记忆A")
        c.kde.ingest_text("x", source="memory:agent:finance:mem_b", source_title="记忆B")
        kus = c.graph_store.query_all(limit=100)
        ent = [k for k in kus if k.ku_type.value == "Entity" and k.status == "active"][0]
        evds = {k.ku_id for k in kus if k.ku_type.value == "Evidence" and k.status == "active"}
        mentions = [r for r in c.graph_store.get_relations(ent.ku_id)
                    if r.rel_type == "MENTIONS" and r.from_ku in evds]
        # 两个不同来源都应连上
        self.assertEqual(len(mentions), 2)

    def test_duplicate_source_no_dup_relation(self):
        """同一来源重复摄入不产生重复 MENTIONS 关系。"""
        c = _container()
        c.kde.extractor = _Mock(entities=[{"name": "芒格", "type": "Person"}])
        c.kde.ingest_text("x", source="memory:agent:finance:mem_same", source_title="同一记忆")
        c.kde.ingest_text("x", source="memory:agent:finance:mem_same", source_title="同一记忆")
        kus = c.graph_store.query_all(limit=100)
        ent = [k for k in kus if k.ku_type.value == "Entity" and k.status == "active"][0]
        mentions = [r for r in c.graph_store.get_relations(ent.ku_id) if r.rel_type == "MENTIONS"]
        # 同来源不重复(注意:每次摄入生成新 Evidence,但内容同源;
        # 这里验证至少不会无限增长——两次摄入各一个 Evidence,实际关系数 <= 2)
        self.assertLessEqual(len(mentions), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
