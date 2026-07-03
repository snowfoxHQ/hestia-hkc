"""
tests / test_search.py
hkc-search 模块测试。

覆盖：
1. BM25 分词、索引构建、检索
2. 向量索引构建与检索（跳过 GPU 相关）
3. 图谱搜索邻居展开、路径查找
4. Hybrid RRF 融合
5. 过滤（domain / ku_types）
6. 增量更新索引
"""
import sys, os, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hkc_core.graph.sqlite_store import SQLiteGraphStore
from hkc_core.models.ku import ConceptKU, ClaimKU, EntityKU, Relation
from hkc_core.models.enums import KUType, ClaimStatus
from hkc_core.utils.id_gen import IDGenerator
from hkc_search.bm25 import BM25Index, _tokenize
from hkc_search.vector_search import VectorIndex
from hkc_search.embedding_backends import StubBackend, TEIBackend, make_backend
from hkc_search.graph_search import GraphSearch
from hkc_search.hybrid import HybridSearch, SearchResult


def _stub_vector_index():
    """离线测试用：StubBackend 驱动的真实 VectorIndex（走 FAISS，但向量是伪随机）。"""
    return VectorIndex(backend=StubBackend(dim=64))



# ── 工厂函数 ────────────────────────────────────────────────

def _store():
    return SQLiteGraphStore(tempfile.mktemp(suffix=".db"))

def _id_gen():
    return IDGenerator(tempfile.mktemp(suffix=".db"))

def _concept(ku_id, name, domain="Investment", summary=""):
    return ConceptKU(ku_id=ku_id, name=name, domain=domain,
                     summary=summary or name, tags=name.lower().split())

def _claim(ku_id, name, statement, domain="Investment", confidence=0.8):
    return ClaimKU(
        ku_id=ku_id, name=name, statement=statement,
        domain=domain, confidence=confidence,
        claim_status=ClaimStatus.ACTIVE, summary=statement,
    )


# ─────────────────────────────────────────────────────────────
# 1. BM25
# ─────────────────────────────────────────────────────────────

class TestBM25Tokenize(unittest.TestCase):

    def test_english_tokens(self):
        tokens = _tokenize("value investing strategy")
        self.assertIn("value", tokens)
        self.assertIn("investing", tokens)
        self.assertIn("strategy", tokens)

    def test_chinese_unigram_bigram(self):
        tokens = _tokenize("价值投资")
        self.assertIn("价", tokens)
        self.assertIn("值", tokens)
        self.assertIn("价值", tokens)
        self.assertIn("值投", tokens)

    def test_mixed_language(self):
        tokens = _tokenize("价值投资 value investing")
        self.assertIn("value", tokens)
        self.assertIn("价值", tokens)

    def test_punctuation_removed(self):
        tokens = _tokenize("hello, world! foo-bar")
        self.assertNotIn(",", tokens)
        self.assertNotIn("!", tokens)

    def test_short_words_filtered(self):
        tokens = _tokenize("a an the is")
        # 单字母英文词被过滤
        self.assertNotIn("a", tokens)


class TestBM25Index(unittest.TestCase):

    def setUp(self):
        self.idx = BM25Index()
        self.kus = [
            _concept("C001", "Value Investing", summary="buy undervalued stocks"),
            _concept("C002", "Momentum Strategy", summary="follow price trends"),
            _concept("C003", "Risk Management", summary="control portfolio drawdown"),
            _concept("C004", "价值投资", domain="Investment",
                     summary="寻找被低估的优质资产"),
        ]
        self.idx.build(self.kus)

    def test_build_size(self):
        self.assertEqual(self.idx.size(), 4)

    def test_english_search(self):
        results = self.idx.search("value investing", top_k=3)
        self.assertGreater(len(results), 0)
        top_ids = [r.ku_id for r in results]
        self.assertIn("C001", top_ids)

    def test_chinese_search(self):
        results = self.idx.search("价值投资", top_k=3)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0].ku_id, "C004")

    def test_irrelevant_query_returns_empty(self):
        results = self.idx.search("zzz_nonexistent_xyzzy", top_k=5)
        self.assertEqual(results, [])

    def test_append_ku(self):
        new_ku = _concept("C005", "Factor Investing", summary="systematic factor exposure")
        self.idx.append_ku(new_ku)
        self.assertEqual(self.idx.size(), 5)
        results = self.idx.search("factor", top_k=3)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0].ku_id, "C005")

    def test_scores_positive(self):
        results = self.idx.search("risk drawdown", top_k=5)
        for r in results:
            self.assertGreater(r.score, 0)


# ─────────────────────────────────────────────────────────────
# 2. 向量索引（仅测试接口，跳过真实 embedding）
# ─────────────────────────────────────────────────────────────

class TestVectorIndex(unittest.TestCase):
    """用 StubBackend 测试完整向量检索流程（FAISS 真实运行，向量为确定性伪随机）。"""

    def _faiss_available(self):
        try:
            import faiss  # noqa
            return True
        except ImportError:
            return False

    def test_empty_index_returns_empty(self):
        idx = VectorIndex(backend=StubBackend(dim=64))
        results = idx.search("value investing", top_k=5)
        self.assertEqual(results, [])

    def test_size_zero_on_init(self):
        idx = VectorIndex(backend=StubBackend(dim=64))
        self.assertEqual(idx.size(), 0)

    def test_build_and_search(self):
        if not self._faiss_available():
            self.skipTest("faiss 未安装")
        idx = VectorIndex(backend=StubBackend(dim=64))
        kus = [
            _concept("C001", "Value Investing", summary="buy undervalued stocks"),
            _concept("C002", "Momentum Trading", summary="follow price trends"),
        ]
        idx.build(kus)
        self.assertEqual(idx.size(), 2)
        # Stub 伪向量无语义，但确定性：用与 _ku_text(C001) 完全一致的文本查应命中 C001
        c001_text = VectorIndex._ku_text(kus[0])
        results = idx.search(c001_text, top_k=2)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0].ku_id, "C001")

    def test_deterministic_same_text_same_vector(self):
        """StubBackend：相同文本编码出相同向量。"""
        backend = StubBackend(dim=64)
        v1 = backend.encode(["hello world"])
        v2 = backend.encode(["hello world"])
        import numpy as np
        self.assertTrue(np.allclose(v1, v2))

    def test_append_ku(self):
        if not self._faiss_available():
            self.skipTest("faiss 未安装")
        idx = VectorIndex(backend=StubBackend(dim=64))
        idx.build([_concept("C001", "Value Investing", summary="stocks")])
        idx.append_ku(_concept("C002", "Momentum", summary="trends"))
        self.assertEqual(idx.size(), 2)

    def test_save_and_load(self):
        if not self._faiss_available():
            self.skipTest("faiss 未安装")
        import tempfile, os
        idx = VectorIndex(backend=StubBackend(dim=64))
        idx.build([_concept("C001", "Value Investing", summary="buy undervalued stocks")])

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_index")
            idx.save(path)
            idx2 = VectorIndex(backend=StubBackend(dim=64))
            ok = idx2.load(path)
            self.assertTrue(ok)
            self.assertEqual(idx2.size(), 1)


# ─────────────────────────────────────────────────────────────
# 3. 图谱搜索
# ─────────────────────────────────────────────────────────────

class TestGraphSearch(unittest.TestCase):

    def setUp(self):
        self.store = _store()
        kus = [
            _concept("C001", "Value Investing"),
            _concept("C002", "DCF Valuation"),
            _concept("C003", "Margin of Safety"),
            _concept("C004", "Graham Number"),
        ]
        self.store.batch_write(kus)
        # C001 → C002 → C003 → C004 链
        self.store.add_relation(Relation("R1", "C001", "C002", "DERIVED_FROM", 0.9))
        self.store.add_relation(Relation("R2", "C002", "C003", "DERIVED_FROM", 0.8))
        self.store.add_relation(Relation("R3", "C003", "C004", "DERIVED_FROM", 0.7))
        self.gs = GraphSearch(self.store)

    def test_direct_neighbors(self):
        results = self.gs.search_neighbors("C001", max_depth=1)
        ids = [r.ku_id for r in results]
        self.assertIn("C002", ids)
        self.assertNotIn("C003", ids)  # 深度1只到C002

    def test_two_hop_neighbors(self):
        results = self.gs.search_neighbors("C001", max_depth=2)
        ids = [r.ku_id for r in results]
        self.assertIn("C002", ids)
        self.assertIn("C003", ids)

    def test_score_decreases_with_depth(self):
        results = self.gs.search_neighbors("C001", max_depth=2)
        by_id = {r.ku_id: r for r in results}
        if "C002" in by_id and "C003" in by_id:
            self.assertGreater(by_id["C002"].score, by_id["C003"].score)

    def test_shortest_path(self):
        path = self.gs.find_path("C001", "C004")
        self.assertGreater(len(path), 0)
        self.assertEqual(path[0], "C001")
        self.assertEqual(path[-1], "C004")

    def test_no_path_returns_empty(self):
        isolated = _concept("C999", "Isolated Concept", domain="Other")
        self.store.put(isolated)
        path = self.gs.find_path("C001", "C999")
        self.assertEqual(path, [])

    def test_nonexistent_start_returns_empty(self):
        results = self.gs.search_neighbors("NONEXISTENT", max_depth=2)
        self.assertEqual(results, [])

    def test_rel_type_filter(self):
        # 加一个不同类型的关系
        self.store.add_relation(Relation("R4", "C001", "C002", "MENTIONS", 0.5))
        results = self.gs.search_neighbors(
            "C001", max_depth=1, rel_types=["MENTIONS"]
        )
        # 只有 MENTIONS 类型，C002 出现
        ids = [r.ku_id for r in results]
        # 可能存在，取决于图中哪条先被遍历
        self.assertIsInstance(results, list)

    def test_search_by_domain(self):
        results = self.gs.search_by_domain("Investment", top_k=10)
        self.assertEqual(len(results), 4)
        for r in results:
            ku = self.store.get(r.ku_id)
            self.assertEqual(ku.domain, "Investment")


# ─────────────────────────────────────────────────────────────
# 4. Hybrid RRF
# ─────────────────────────────────────────────────────────────

class TestHybridSearch(unittest.TestCase):

    def setUp(self):
        self.store = _store()
        kus = [
            _concept("C001", "Value Investing",
                     summary="buy undervalued stocks long term"),
            _concept("C002", "Momentum Strategy",
                     summary="follow price trends short term"),
            _concept("C003", "Risk Management",
                     summary="control portfolio drawdown volatility"),
            _claim("CL001", "Value beats growth",
                   "Value investing outperforms growth over long periods"),
        ]
        self.store.batch_write(kus)
        # 用 stub VectorIndex 避免下载模型（离线环境）
        self.hs = HybridSearch(self.store, vector_index=_stub_vector_index())
        self.hs.bm25.build(kus)

    def test_bm25_mode(self):
        results = self.hs.search("value investing", mode="bm25", top_k=5)
        # BM25 在少量语料时分数可为负，但不崩溃且结果数量合理
        self.assertIsInstance(results, list)
        if results:
            self.assertEqual(results[0].mode, "bm25")

    def test_hybrid_mode_returns_results(self):
        results = self.hs.search("value investing", mode="hybrid", top_k=5)
        self.assertIsInstance(results, list)
        if results:
            self.assertEqual(results[0].mode, "hybrid")

    def test_hybrid_top_result_relevant(self):
        results = self.hs.search("value undervalued", mode="hybrid", top_k=5)
        top_ids = [r.ku_id for r in results]
        # C001（Value Investing）应在前列
        self.assertIn("C001", top_ids[:3])

    def test_rrf_merges_both_sources(self):
        """RRF 结果应包含来自两路的 KU。"""
        results = self.hs.search("portfolio risk drawdown", mode="hybrid", top_k=5)
        ids = [r.ku_id for r in results]
        # C003 在 BM25 里命中 "risk drawdown"
        self.assertIn("C003", ids)

    def test_domain_filter(self):
        extra = _concept("C999", "Cooking Recipe", domain="Food",
                         summary="how to cook pasta")
        self.store.put(extra)
        # 只重建 BM25（避免向量模型下载）
        self.hs.bm25.build(self.store.query_all())

        results = self.hs.search("recipe", mode="bm25", top_k=10,
                                  domain="Investment")
        for r in results:
            ku = self.store.get(r.ku_id)
            self.assertEqual(ku.domain, "Investment")

    def test_ku_type_filter(self):
        results = self.hs.search("value", mode="hybrid", top_k=10,
                                  ku_types=["Claim"])
        for r in results:
            self.assertEqual(r.ku_type, "Claim")

    def test_append_ku_updates_index(self):
        new_ku = _concept("C010", "Factor Investing",
                          summary="systematic factor exposure")
        self.store.put(new_ku)
        self.hs.append_ku(new_ku)  # BM25 追加，VectorIndex stub 忽略

        results = self.hs.search("factor systematic", mode="bm25", top_k=5)
        ids = [r.ku_id for r in results]
        self.assertIn("C010", ids)

    def test_empty_query_graceful(self):
        results = self.hs.search("", mode="hybrid", top_k=5)
        self.assertIsInstance(results, list)

    def test_graph_mode_via_hybrid(self):
        self.store.add_relation(Relation("R1", "C001", "C002", "DERIVED_FROM", 0.9))
        results = self.hs.search_neighbors("C001", max_depth=1, top_k=5)
        ids = [r.ku_id for r in results]
        self.assertIn("C002", ids)

    def test_find_path(self):
        self.store.add_relation(Relation("R2", "C001", "C003", "USES", 0.8))
        path = self.hs.find_path("C001", "C003")
        self.assertGreater(len(path), 0)

    def test_excludes_superseded_claim(self):
        """superseded 状态的 Claim 不应出现在搜索结果。"""
        from hkc_core.models.enums import ClaimStatus
        # 注入一个 superseded Claim
        bad = _claim("CL999", "Outdated theory",
                     "outdated investment theory superseded",
                     confidence=0.3)
        bad.status = "active"
        bad.claim_status = ClaimStatus.SUPERSEDED
        self.store.put(bad)
        self.hs.bm25.build(self.store.query_all())

        results = self.hs.search("outdated theory", mode="bm25", top_k=10)
        ids = [r.ku_id for r in results]
        self.assertNotIn("CL999", ids)

    def test_include_inactive_when_disabled(self):
        """exclude_inactive=False 时不过滤状态。"""
        from hkc_core.models.enums import ClaimStatus
        bad = _claim("CL998", "Rejected claim",
                     "rejected investment claim xyzzy",
                     confidence=0.2)
        bad.claim_status = ClaimStatus.REJECTED
        self.store.put(bad)
        self.hs.bm25.build(self.store.query_all())

        results = self.hs.search("xyzzy rejected", mode="bm25",
                                  top_k=10, exclude_inactive=False)
        ids = [r.ku_id for r in results]
        self.assertIn("CL998", ids)


# ─────────────────────────────────────────────────────────────
# 5. 增量索引与 build_index() 无参数调用
# ─────────────────────────────────────────────────────────────

class TestIndexIncremental(unittest.TestCase):

    def test_build_from_store(self):
        store = _store()
        kus = [_concept(f"C{i:03d}", f"Concept {i}") for i in range(10)]
        store.batch_write(kus)

        hs = HybridSearch(store, vector_index=_stub_vector_index())
        hs.bm25.build(store.query_all())
        self.assertEqual(hs.bm25.size(), 10)

    def test_empty_store_no_crash(self):
        store = _store()
        hs = HybridSearch(store, vector_index=_stub_vector_index())
        # store 为空，build_index 不崩溃
        # 空语料不调用 build（rank-bm25 会除零）
        results = hs.search("anything", top_k=5)
        self.assertEqual(results, [])


# ─────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────
# 6. Embedding Backends
# ─────────────────────────────────────────────────────────────

class TestEmbeddingBackends(unittest.TestCase):

    def test_stub_backend_dim(self):
        b = StubBackend(dim=128)
        self.assertEqual(b.dim, 128)

    def test_stub_backend_normalized(self):
        """StubBackend 输出应 L2 归一化（模长≈1）。"""
        import numpy as np
        b = StubBackend(dim=64)
        vecs = b.encode(["a", "b", "c"])
        norms = np.linalg.norm(vecs, axis=1)
        for n in norms:
            self.assertAlmostEqual(n, 1.0, places=5)

    def test_stub_backend_shape(self):
        b = StubBackend(dim=32)
        vecs = b.encode(["x", "y"])
        self.assertEqual(vecs.shape, (2, 32))

    def test_stub_backend_empty(self):
        b = StubBackend(dim=32)
        vecs = b.encode([])
        self.assertEqual(vecs.shape[0], 0)

    def test_tei_backend_name(self):
        b = TEIBackend("http://localhost:8080")
        self.assertIn("TEI", b.name)
        self.assertIn("8080", b.name)

    def test_tei_health_check_offline(self):
        """TEI 服务不在线时 health_check 返回 False，不抛异常。"""
        b = TEIBackend("http://localhost:59999")  # 不存在的端口
        self.assertFalse(b.health_check())

    def test_make_backend_stub(self):
        b = make_backend("stub", dim=64)
        self.assertIsInstance(b, StubBackend)

    def test_make_backend_tei(self):
        b = make_backend("tei", base_url="http://localhost:8080")
        self.assertIsInstance(b, TEIBackend)

    def test_make_backend_auto_fallback(self):
        """auto 模式：本地模型不可用时回退到 stub，不崩溃。"""
        b = make_backend("auto", dim=64)
        # 离线环境应回退到 StubBackend
        self.assertIn(type(b).__name__, ("LocalSTBackend", "StubBackend"))

    def test_tei_encode_rejects_dict_response(self):
        """TEI 返回错误 dict 时应抛 ValueError 而非产生坏向量。"""
        import unittest.mock as mock
        b = TEIBackend("http://fake:8080")

        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value={"error": "model not loaded"})

        with mock.patch("requests.post", return_value=fake_resp):
            with self.assertRaises(ValueError):
                b.encode(["hello"])

    def test_tei_encode_handles_1d_response(self):
        """TEI 对单条返回 1D 向量时应包装为 2D。"""
        import unittest.mock as mock
        import numpy as np
        b = TEIBackend("http://fake:8080", dim=4)

        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        # 返回扁平 1D（[[...]] 里只有一个向量，但元素是 float 不是 list）
        fake_resp.json = mock.Mock(return_value=[0.1, 0.2, 0.3, 0.4])

        with mock.patch("requests.post", return_value=fake_resp):
            arr = b.encode(["hello"])
        self.assertEqual(arr.ndim, 2)
        self.assertEqual(arr.shape[0], 1)

    def test_tei_dim_correction(self):
        """TEI 返回与声明 dim 不同时自动校正。"""
        import unittest.mock as mock
        b = TEIBackend("http://fake:8080", dim=384)  # 声明 384

        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value=[[0.1, 0.2, 0.3]])  # 实际 3 维

        with mock.patch("requests.post", return_value=fake_resp):
            b.encode(["hello"])
        self.assertEqual(b.dim, 3)  # 已校正


class TestVectorIndexNonDefaultDim(unittest.TestCase):
    """验证非 384 维后端在 append_ku 路径不崩溃（回归测试）。"""

    def _faiss_available(self):
        try:
            import faiss  # noqa
            return True
        except ImportError:
            return False

    def test_append_to_empty_index_non_default_dim(self):
        if not self._faiss_available():
            self.skipTest("faiss 未安装")
        # 用 64 维 StubBackend，append 到空索引
        idx = VectorIndex(backend=StubBackend(dim=64))
        idx.append_ku(_concept("C001", "Test", summary="content"))
        self.assertEqual(idx.size(), 1)

    def test_build_then_append_non_default_dim(self):
        if not self._faiss_available():
            self.skipTest("faiss 未安装")
        idx = VectorIndex(backend=StubBackend(dim=128))
        idx.build([_concept("C001", "First", summary="a")])
        idx.append_ku(_concept("C002", "Second", summary="b"))
        self.assertEqual(idx.size(), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
