"""
tests / test_kee_dedup.py
KEE 知识级去重 + canonical fingerprint + 关系重定向 测试。
"""
import sys, os, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["HF_HUB_OFFLINE"] = "1"

from hkc_kee.fingerprint import canonical_fingerprint, normalize_name, fingerprint_of_ku
from hkc_core.graph.sqlite_store import SQLiteGraphStore
from hkc_core.models.ku import Relation, ConceptKU, ClaimKU
from hkc_core.models.enums import KUType


class TestCanonicalFingerprint(unittest.TestCase):
    def test_same_knowledge_same_fp(self):
        a = canonical_fingerprint("Concept", "价值投资", "Investment")
        b = canonical_fingerprint("Concept", "价值投资 ", "Investment")
        c = canonical_fingerprint("Concept", "价值-投资", "investment")
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_different_knowledge_different_fp(self):
        base = canonical_fingerprint("Concept", "价值投资", "Investment")
        self.assertNotEqual(base, canonical_fingerprint("Concept", "成长投资", "Investment"))
        self.assertNotEqual(base, canonical_fingerprint("Entity", "价值投资", "Investment"))
        self.assertNotEqual(base, canonical_fingerprint("Concept", "价值投资", "Psychology"))

    def test_normalize_unicode(self):
        self.assertEqual(normalize_name("Value Investing"), normalize_name("value investing"))
        self.assertEqual(normalize_name("价值——投资！"), normalize_name("价值投资"))

    def test_fp_length(self):
        self.assertEqual(len(canonical_fingerprint("Concept", "x", "y")), 24)


class TestRelationOps(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteGraphStore(db_path=tempfile.mktemp(suffix=".db"))
        for kid, name in [("A", "A"), ("B", "B"), ("C", "C")]:
            self.store.put(ConceptKU(ku_id=kid, name=name))

    def test_delete_relation(self):
        self.store.add_relation(Relation(rel_id="R1", from_ku="A", to_ku="B", rel_type="DERIVED_FROM"))
        self.assertEqual(len(self.store.get_relations("A")), 1)
        self.store.delete_relation("R1")
        self.assertEqual(len(self.store.get_relations("A")), 0)

    def test_redirect_relations(self):
        # C→B,把 C 重定向到 A ⇒ A→B
        self.store.add_relation(Relation(rel_id="R2", from_ku="C", to_ku="B", rel_type="DERIVED_FROM"))
        n = self.store.redirect_relations("C", "A")
        self.assertEqual(n, 1)
        self.assertEqual(len(self.store.get_relations("C")), 0)
        self.assertTrue(any(r.from_ku == "A" and r.to_ku == "B" for r in self.store.get_relations("A")))

    def test_redirect_drops_selfloop(self):
        # A→C,把 C 重定向到 A ⇒ A→A 自环,应丢弃
        self.store.add_relation(Relation(rel_id="R3", from_ku="A", to_ku="C", rel_type="DERIVED_FROM"))
        self.store.redirect_relations("C", "A")
        self.assertFalse(any(r.from_ku == "A" and r.to_ku == "A" for r in self.store.get_relations("A")))

    def test_redirect_dedups(self):
        # 已有 A→B,再把 C→B 重定向到 A,不应产生重复 A→B
        self.store.add_relation(Relation(rel_id="R4", from_ku="A", to_ku="B", rel_type="DERIVED_FROM"))
        self.store.add_relation(Relation(rel_id="R5", from_ku="C", to_ku="B", rel_type="DERIVED_FROM"))
        self.store.redirect_relations("C", "A")
        ab = [r for r in self.store.get_relations("A") if r.to_ku == "B" and r.rel_type == "DERIVED_FROM"]
        self.assertEqual(len(ab), 1)


class TestKnowledgeDedup(unittest.TestCase):
    def setUp(self):
        import hkc_api.container as cm
        from hkc_kde.models import RawExtraction, ExtractionResult
        from hkc_kde.extractor import Extractor
        cm.reset_container()
        self.c = cm.init_container(data_dir=tempfile.mkdtemp(), embedding_kind="stub")
        class Mock(Extractor):
            def extract(self, chunks):
                d = chunks[0].doc_id if chunks else "D"
                return ExtractionResult(doc_id=d, source=d, items=[RawExtraction(
                    concepts=[{"name": "价值投资", "domain": "Investment", "definition": "x"}],
                    claims=[], entities=[], facts=[])])
        self.c.kde.extractor = Mock()

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def _active_concepts(self):
        return [k for k in self.c.graph_store.query_all(limit=200)
                if k.ku_type.value == "Concept" and k.status == "active" and k.name == "价值投资"]

    def test_dedup_no_duplicate_ku(self):
        self.c.kde.ingest_text("价值投资", source="mem:M1", source_title="记忆1", domain="Investment")
        n1 = len(self._active_concepts())
        self.c.kde.ingest_text("价值投资", source="mem:M2", source_title="记忆2", domain="Investment")
        n2 = len(self._active_concepts())
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 1)  # 第二次未新建重复 KU

    def test_dedup_merges_provenance_across_sources(self):
        """跨来源重复录入同一知识时,KEE 应把两次来源(Evidence)都并入存活 KU,
        保全可追溯链 KU←Evidence。这是 KEE 接管知识身份(Principle 07)相对
        legacy 路径(仅跳过、不并来源)的关键改进:删除 legacy 后此断言成立。"""
        self.c.kde.ingest_text("价值投资", source="mem:M1", source_title="记忆1", domain="Investment")
        self.c.kde.ingest_text("价值投资", source="mem:M2", source_title="记忆2", domain="Investment")
        concepts = self._active_concepts()
        self.assertEqual(len(concepts), 1)               # 仍只有 1 个存活概念
        self.assertGreaterEqual(len(concepts[0].sources), 2)  # 两次来源都并入(KEE 接管)

    def test_different_concept_not_deduped(self):
        import hkc_api.container as cm
        from hkc_kde.models import RawExtraction, ExtractionResult
        from hkc_kde.extractor import Extractor
        self.c.kde.ingest_text("价值投资", source="m:1", domain="Investment")
        # 换一个不同概念
        class Mock2(Extractor):
            def extract(self, chunks):
                d = chunks[0].doc_id if chunks else "D"
                return ExtractionResult(doc_id=d, source=d, items=[RawExtraction(
                    concepts=[{"name": "成长投资", "domain": "Investment", "definition": "y"}],
                    claims=[], entities=[], facts=[])])
        self.c.kde.extractor = Mock2()
        self.c.kde.ingest_text("成长投资", source="m:2", domain="Investment")
        all_active = [k for k in self.c.graph_store.query_all(limit=200)
                      if k.ku_type.value == "Concept" and k.status == "active"]
        names = {k.name for k in all_active}
        self.assertIn("价值投资", names)
        self.assertIn("成长投资", names)  # 不同概念不被去重


if __name__ == "__main__":
    unittest.main(verbosity=2)
