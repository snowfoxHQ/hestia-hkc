"""
tests / test_sdk.py
hkc_sdk 测试。

核心策略：同一组测试用例，分别在 DirectClient 和 HTTPClient 上跑，
验证两种实现行为一致（接口契约测试）。

- DirectClient：直接用 HKCContainer
- HTTPClient：用 FastAPI TestClient 包一层适配器（把 requests 调用转发给 TestClient）
- 都用 stub embedding + mock extractor，离线可跑
"""
import sys, os, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["HF_HUB_OFFLINE"] = "1"

from hkc_kde.models import RawExtraction, ExtractionResult
from hkc_kde.extractor import Extractor

from hkc_sdk import (
    connect, DirectClient, HTTPClient,
    HKCNotFoundError, HKCInsufficientCoverage, HKCBadRequest,
)
from hkc_sdk.models import KU, SearchHit, Ability


# ── Mock Extractor ───────────────────────────────────────────

QUANT_PRESET = {
    "facts": [],
    "claims": [],
    "entities": [],
    "concepts": [
        {"name": "momentum factor investing", "domain": "Investment",
         "definition": "alpha beta factor model momentum value quality"},
        {"name": "backtesting", "domain": "Investment",
         "definition": "historical simulation walk-forward overfitting transaction cost slippage"},
        {"name": "risk management", "domain": "Investment",
         "definition": "VaR drawdown sharpe ratio position sizing stop loss"},
        {"name": "portfolio optimization", "domain": "Investment",
         "definition": "mean-variance efficient frontier diversification rebalancing"},
        {"name": "data analysis", "domain": "Investment",
         "definition": "time series cross-sectional data cleaning feature engineering statistical testing"},
    ],
}

DEFAULT_PRESET = {
    "facts":    [{"statement": "巴菲特生于1930年", "source_hint": ""}],
    "claims":   [{"statement": "价值投资长期有效", "confidence": 0.85, "domain": "Investment"}],
    "entities": [{"name": "Warren Buffett", "type": "Person", "aliases": ["巴菲特"]}],
    "concepts": [{"name": "Value Investing", "domain": "Investment",
                  "definition": "buy undervalued value investing"}],
}


class MockExtractor(Extractor):
    def __init__(self, preset=None):
        self._preset = preset or DEFAULT_PRESET

    def extract(self, chunks):
        items = [RawExtraction(
            facts=self._preset.get("facts", []),
            claims=self._preset.get("claims", []),
            entities=self._preset.get("entities", []),
            concepts=self._preset.get("concepts", []),
            source_hint="test",
        )]
        doc_id = chunks[0].doc_id if chunks else "DOC_TEST"
        return ExtractionResult(doc_id=doc_id, source=doc_id, items=items)


# ── HTTPClient over TestClient 适配器 ────────────────────────

class _TestClientHTTP(HTTPClient):
    """把 HTTPClient 的 requests 调用转发给 FastAPI TestClient。"""

    def __init__(self, test_client):
        super().__init__("http://testserver")
        self._tc = test_client

    def _request(self, method, path, **kwargs):
        resp = self._tc.request(method, path, **kwargs)
        return self._handle_response(resp)


def _make_container(preset=None):
    import hkc_api.container as cm
    cm.reset_container()
    tmpdir = tempfile.mkdtemp()
    c = cm.init_container(data_dir=tmpdir, embedding_kind="stub")
    c.kde.extractor = MockExtractor(preset)
    return c


def _make_http_client(preset=None):
    from fastapi.testclient import TestClient
    import hkc_api.main as main_mod
    c = _make_container(preset)
    tc = TestClient(main_mod.app)
    return _TestClientHTTP(tc), c


def _make_direct_client(preset=None):
    c = _make_container(preset)
    return DirectClient(c), c


# ─────────────────────────────────────────────────────────────
# 共享测试逻辑（mixin）：子类只需提供 _client()
# ─────────────────────────────────────────────────────────────

class _SharedSDKTests:
    """两种客户端共享的测试用例。子类实现 _new_client()。"""

    def _new_client(self, preset=None):
        raise NotImplementedError

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    # ── 摄入 ──

    def test_ingest_text(self):
        hkc = self._new_client()
        result = hkc.ingest_text("巴菲特价值投资内容", domain="Investment",
                                  source_title="笔记")
        self.assertGreater(result.ku_count, 0)
        self.assertIsInstance(result.ku_ids, list)

    def test_ingest_then_get_ku(self):
        hkc = self._new_client()
        result = hkc.ingest_text("测试内容", domain="Investment")
        ku = hkc.get_ku(result.ku_ids[0])
        self.assertIsInstance(ku, KU)
        self.assertEqual(ku.ku_id, result.ku_ids[0])

    # ── KU 查询 ──

    def test_get_ku_not_found(self):
        hkc = self._new_client()
        with self.assertRaises(HKCNotFoundError):
            hkc.get_ku("NONEXISTENT")

    def test_list_by_domain(self):
        hkc = self._new_client()
        hkc.ingest_text("内容", domain="Investment")
        kus = hkc.list_by_domain("Investment")
        self.assertGreater(len(kus), 0)
        self.assertTrue(all(isinstance(k, KU) for k in kus))

    def test_list_by_domain_empty(self):
        hkc = self._new_client()
        kus = hkc.list_by_domain("EmptyDomain")
        self.assertEqual(len(kus), 0)

    # ── 搜索 ──

    def test_search_returns_hits(self):
        hkc = self._new_client()
        hkc.ingest_text("value investing content", domain="Investment")
        hits = hkc.search("value investing", mode="bm25")
        self.assertIsInstance(hits, list)
        for h in hits:
            self.assertIsInstance(h, SearchHit)

    def test_search_invalid_mode(self):
        hkc = self._new_client()
        with self.assertRaises(HKCBadRequest):
            hkc.search("test", mode="bogus")

    def test_find_path_unreachable(self):
        hkc = self._new_client()
        path = hkc.find_path("FAKE_A", "FAKE_B")
        self.assertEqual(path, [])

    # ── 能力 ──

    def test_list_abilities(self):
        hkc = self._new_client()
        abilities = hkc.list_abilities()
        self.assertIn("quant_analyst", abilities)

    def test_coverage_report(self):
        hkc = self._new_client()
        report = hkc.coverage_report("quant_analyst")
        self.assertEqual(report.ability_key, "quant_analyst")
        self.assertIsInstance(report.coverage, dict)

    def test_coverage_unknown_ability(self):
        hkc = self._new_client()
        with self.assertRaises(HKCNotFoundError):
            hkc.coverage_report("nonexistent")

    def test_compile_ability_success(self):
        hkc = self._new_client(QUANT_PRESET)
        hkc.ingest_text("quant content", domain="Investment")
        ability = hkc.compile_ability("quant_analyst")
        self.assertIsInstance(ability, Ability)
        self.assertEqual(ability.ability_key, "quant_analyst")

    def test_compile_insufficient_coverage(self):
        hkc = self._new_client()  # 默认 preset 不够 quant
        hkc.ingest_text("少量内容", domain="Investment")
        with self.assertRaises(HKCInsufficientCoverage) as ctx:
            hkc.compile_ability("quant_analyst")
        # 异常带 missing_skills
        self.assertIsInstance(ctx.exception.missing_skills, list)

    def test_get_ability_not_compiled(self):
        hkc = self._new_client()
        with self.assertRaises(HKCNotFoundError):
            hkc.get_ability("value_investor")

    def test_ensure_ability_compiles(self):
        """ensure_ability：未编译时自动编译。"""
        hkc = self._new_client(QUANT_PRESET)
        hkc.ingest_text("quant content", domain="Investment")
        ability = hkc.ensure_ability("quant_analyst")
        self.assertEqual(ability.ability_key, "quant_analyst")
        # 第二次调用应直接返回已编译的
        ability2 = hkc.ensure_ability("quant_analyst")
        self.assertEqual(ability2.ability_key, "quant_analyst")

    # ── 冲突 ──

    def test_list_conflicts_empty(self):
        hkc = self._new_client()
        conflicts = hkc.list_conflicts()
        self.assertEqual(len(conflicts), 0)

    def test_resolve_nonexistent_conflict(self):
        hkc = self._new_client()
        ok = hkc.resolve_conflict("FAKE", "CLM_X")
        self.assertFalse(ok)

    # ── 系统 ──

    def test_stats(self):
        hkc = self._new_client()
        stats = hkc.stats()
        self.assertTrue(stats["assembled"])

    def test_health(self):
        hkc = self._new_client()
        self.assertTrue(hkc.health())

    def test_neighbors_mode_consistent(self):
        """neighbors() 返回的 SearchHit.mode 两种客户端应一致为 graph。"""
        hkc = self._new_client()
        result = hkc.ingest_text("内容", domain="Investment")
        # 取一个 KU，查邻居（可能为空，但若有则 mode 应为 graph）
        hits = hkc.neighbors(result.ku_ids[0])
        for h in hits:
            self.assertEqual(h.mode, "graph")


# ─────────────────────────────────────────────────────────────
# 两种实现各跑一遍共享测试
# ─────────────────────────────────────────────────────────────

class TestDirectClient(_SharedSDKTests, unittest.TestCase):
    def _new_client(self, preset=None):
        client, self._c = _make_direct_client(preset)
        return client


class TestHTTPClient(_SharedSDKTests, unittest.TestCase):
    def _new_client(self, preset=None):
        client, self._c = _make_http_client(preset)
        return client


# ─────────────────────────────────────────────────────────────
# connect() 工厂 + 模型测试
# ─────────────────────────────────────────────────────────────

class TestConnectFactory(unittest.TestCase):

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_connect_http(self):
        hkc = connect("http://localhost:8000")
        self.assertIsInstance(hkc, HTTPClient)

    def test_connect_direct(self):
        c = _make_container()
        hkc = connect(container=c)
        self.assertIsInstance(hkc, DirectClient)

    def test_connect_requires_arg(self):
        with self.assertRaises(ValueError):
            connect()


class TestSDKModels(unittest.TestCase):

    def test_ku_from_dict(self):
        ku = KU.from_dict({
            "ku_id": "ENT_001", "ku_type": "Entity", "name": "Test",
            "summary": "s", "domain": "D", "confidence": 0.9,
            "status": "active", "tags": ["a"], "extra": {"statement": "stmt"},
        })
        self.assertEqual(ku.ku_id, "ENT_001")
        self.assertEqual(ku.statement, "stmt")

    def test_ability_from_dict(self):
        ab = Ability.from_dict({
            "ability_key": "quant_analyst", "display_name": "量化",
            "domain": "Investment", "coverage": {"backtesting": 0.8},
            "skills": [{"skill_key": "backtesting", "display_name": "回测",
                        "coverage": 0.8, "concept_hits": ["VaR"]}],
            "workflows": [], "version": "1.0.0",
        })
        self.assertEqual(ab.ability_key, "quant_analyst")
        self.assertEqual(len(ab.skills), 1)
        ctx = ab.skill_context("backtesting")
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.coverage, 0.8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
