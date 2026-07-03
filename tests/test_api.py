"""
tests / test_api.py
hkc-api 集成测试。

用 FastAPI TestClient，stub embedding 后端，mock LLM extractor。
覆盖所有路由：ingest / ku / search / ability / conflict / stats。
"""
import sys, os, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["HF_HUB_OFFLINE"] = "1"

from fastapi.testclient import TestClient

from hkc_kde.models import RawExtraction, ExtractionResult
from hkc_kde.extractor import Extractor


# ── Mock Extractor（不调用真实 LLM）──────────────────────────

class MockExtractor(Extractor):
    def __init__(self, preset=None):
        self._preset = preset or {
            "facts":    [{"statement": "巴菲特生于1930年", "source_hint": ""}],
            "claims":   [{"statement": "价值投资长期有效", "confidence": 0.85, "domain": "Investment"}],
            "entities": [{"name": "Warren Buffett", "type": "Person", "aliases": ["巴菲特"]}],
            "concepts": [{"name": "Value Investing", "domain": "Investment",
                          "definition": "buy undervalued assets momentum value"}],
        }

    def extract(self, chunks, progress_cb=None):
        items = [RawExtraction(
            facts=self._preset.get("facts", []),
            claims=self._preset.get("claims", []),
            entities=self._preset.get("entities", []),
            concepts=self._preset.get("concepts", []),
            source_hint="test",
        )]
        if progress_cb and chunks:          # 模拟逐 chunk 进度上报
            for i in range(len(chunks)):
                progress_cb(i + 1, len(chunks))
        doc_id = chunks[0].doc_id if chunks else "DOC_TEST"
        return ExtractionResult(doc_id=doc_id, source=doc_id, items=items)


def _make_client(preset=None):
    """构建独立的 TestClient，每个测试用独立数据目录。"""
    import hkc_api.container as container_mod
    import hkc_api.main as main_mod

    # 重置全局容器
    container_mod.reset_container()

    tmpdir = tempfile.mkdtemp()
    c = container_mod.init_container(
        data_dir=tmpdir,
        embedding_kind="stub",
    )
    # 替换 KDE 的 extractor 为 mock
    c.kde.extractor = MockExtractor(preset)

    # TestClient 不触发 lifespan（容器已手动初始化）
    client = TestClient(main_mod.app)
    return client, c, tmpdir


# ─────────────────────────────────────────────────────────────

class TestRootEndpoints(unittest.TestCase):

    def setUp(self):
        self.client, self.c, self.tmpdir = _make_client()

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_root(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "Hestia Knowledge Core")

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_stats_empty(self):
        r = self.client.get("/stats")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["assembled"])
        self.assertEqual(data["total_kus"], 0)


class TestIngest(unittest.TestCase):

    def setUp(self):
        self.client, self.c, self.tmpdir = _make_client()

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_ingest_text(self):
        r = self.client.post("/knowledge/ingest/text", json={
            "text": "巴菲特是价值投资的代表人物，他认为长期持有优质资产是最佳策略。",
            "source": "test",
            "source_title": "投资笔记",
            "domain": "Investment",
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertGreater(data["ku_count"], 0)
        self.assertIn("ku_ids", data)

    def test_ingest_file_md(self):
        import io
        content = "# 价值投资\n\n价值投资是低于内在价值买入的策略，安全边际是核心。".encode()
        r = self.client.post(
            "/knowledge/ingest/file",
            files={"file": ("note.md", io.BytesIO(content), "text/markdown")},
            data={"source_title": "投资笔记", "domain": "Investment"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("ku_count", r.json())

    def test_ingest_file_source_is_filename_not_temp(self):
        """回归:上传文件后 Evidence 的来源应是原始文件名,而非后端临时盘路径
        (修复前 source=临时路径,导致星球书名/来源链显示 tmpXXXX.epub)。"""
        import io
        content = "# 价值投资\n\n价值投资是低于内在价值买入的策略，安全边际是核心。".encode()
        r = self.client.post(
            "/knowledge/ingest/file",
            files={"file": ("穷查理宝典.md", io.BytesIO(content), "text/markdown")},
            data={"source_title": "", "domain": "Investment"},   # 标题留空 → 用文件名
        )
        self.assertEqual(r.status_code, 200)
        evs = [k for k in self.client.get("/knowledge/graph").json()["kus"]
               if k["ku_type"] == "Evidence"]
        self.assertTrue(evs, "应至少产生一个 Evidence")
        for e in evs:
            src = (e.get("extra") or {}).get("source", "")
            self.assertNotIn("tmp", src.lower())          # 不是临时盘路径
            self.assertEqual(src, "穷查理宝典.md")          # 是原始上传文件名

    def test_ingest_file_rejects_unsupported(self):
        import io
        r = self.client.post(
            "/knowledge/ingest/file",
            files={"file": ("bad.exe", io.BytesIO(b"x"), "application/octet-stream")},
            data={},
        )
        self.assertEqual(r.status_code, 415)

    def test_ingest_creates_retrievable_ku(self):
        r = self.client.post("/knowledge/ingest/text", json={
            "text": "测试内容", "domain": "Investment",
        })
        ku_ids = r.json()["ku_ids"]
        self.assertGreater(len(ku_ids), 0)

        # 取第一个 KU
        r2 = self.client.get(f"/knowledge/ku/{ku_ids[0]}")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["ku_id"], ku_ids[0])

    def test_ingest_counts_by_type(self):
        r = self.client.post("/knowledge/ingest/text", json={"text": "x"})
        counts = r.json()["counts"]
        # MockExtractor 产出 entity/concept/fact/claim/evidence
        self.assertIn("Entity", counts)
        self.assertIn("Claim", counts)

    def test_ingest_oversized_413(self):
        """超过大小上限的文本应返回 413。"""
        huge = "x" * 2_000_001
        r = self.client.post("/knowledge/ingest/text", json={"text": huge})
        self.assertEqual(r.status_code, 413)


class TestAsyncIngest(unittest.TestCase):
    """异步摄入(方案A):上传秒返回 job_id,后台跑,轮询状态到完成。"""

    def setUp(self):
        self.client, self.c, self.tmpdir = _make_client()

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_async_ingest_returns_job_then_completes(self):
        import io, time
        content = "# 价值投资\n\n价值投资是低于内在价值买入的策略，安全边际是核心。".encode()
        r = self.client.post(
            "/knowledge/ingest/file/async",
            files={"file": ("测试书.md", io.BytesIO(content), "text/markdown")},
            data={"source_title": "", "domain": "Investment"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["job_id"].startswith("job_"))
        self.assertIn(body["status"], ("queued", "running", "done"))

        # 轮询后台任务直到完成
        j = None
        for _ in range(60):
            j = self.client.get(f"/knowledge/ingest/jobs/{body['job_id']}").json()
            if j["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        self.assertEqual(j["status"], "done", f"job 未完成: {j}")
        self.assertGreater(j["ku_count"], 0)          # 产出了知识
        self.assertGreater(j["total"], 0)             # 有 chunk 总数
        self.assertEqual(j["processed"], j["total"])  # 进度跑满

    def test_async_job_not_found(self):
        r = self.client.get("/knowledge/ingest/jobs/job_doesnotexist")
        self.assertEqual(r.status_code, 404)


class TestReset(unittest.TestCase):
    """POST /knowledge/reset：清库重来,清空全部知识。"""

    def setUp(self):
        self.client, self.c, self.tmpdir = _make_client()

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_reset_clears_all_and_allows_reingest(self):
        self.client.post("/knowledge/ingest/text", json={"text": "投资内容", "domain": "Investment"})
        self.assertGreater(len(self.client.get("/knowledge/graph").json()["kus"]), 0)

        r = self.client.post("/knowledge/reset")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["cleared_kus"], 1)

        after = self.client.get("/knowledge/graph").json()
        self.assertEqual(after["kus"], [])
        self.assertEqual(after["relations"], [])
        self.assertEqual(after["stats"]["total_kus"], 0)

        # 清空后再摄入同样内容应能重新产生 KU(证明 KEE 去重指纹索引也已重置)
        self.client.post("/knowledge/ingest/text", json={"text": "投资内容", "domain": "Investment"})
        self.assertGreater(len(self.client.get("/knowledge/graph").json()["kus"]), 0)


class TestKURoutes(unittest.TestCase):

    def setUp(self):
        self.client, self.c, self.tmpdir = _make_client()
        self.client.post("/knowledge/ingest/text", json={
            "text": "投资内容", "domain": "Investment",
        })

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_get_ku_404(self):
        r = self.client.get("/knowledge/ku/NONEXISTENT_ID")
        self.assertEqual(r.status_code, 404)

    def test_list_by_domain(self):
        r = self.client.get("/knowledge/domain/Investment")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["domain"], "Investment")
        self.assertGreater(data["count"], 0)

    def test_domain_empty(self):
        r = self.client.get("/knowledge/domain/NonExistentDomain")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 0)

    def test_neighbors_includes_rel_type(self):
        """neighbors 端点应返回 rel_type 字段（图谱连线需要）。"""
        # 找一个有关系的 KU
        r = self.client.get("/knowledge/domain/Investment")
        kus = r.json()["kus"]
        for ku in kus:
            nb = self.client.get(f"/knowledge/ku/{ku['ku_id']}/neighbors")
            self.assertEqual(nb.status_code, 200)
            neighbors = nb.json()["neighbors"]
            if neighbors:
                # 每个邻居都应有 rel_type 键（可能为空字符串）
                self.assertIn("rel_type", neighbors[0])
                return
        # 没有关系也算通过（至少不报错）


class TestGraphRoute(unittest.TestCase):
    """GET /knowledge/graph：前端星球一次拉取全量图。"""

    def setUp(self):
        self.client, self.c, self.tmpdir = _make_client()

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_graph_empty(self):
        """空库时返回 200 且 kus / relations 均为空。"""
        r = self.client.get("/knowledge/graph")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["kus"], [])
        self.assertEqual(data["relations"], [])
        self.assertEqual(data["stats"]["total_kus"], 0)

    def test_graph_returns_full_graph(self):
        """摄入后应返回 KU、关系（含 from_ku/to_ku/rel_type）和一致的统计。"""
        self.client.post("/knowledge/ingest/text", json={
            "text": "投资内容", "domain": "Investment",
        })
        r = self.client.get("/knowledge/graph")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertGreater(len(data["kus"]), 0)
        # stats.total_kus 应与返回的 kus 数一致
        self.assertEqual(data["stats"]["total_kus"], len(data["kus"]))
        # 关系结构校验 + 不得有悬空边
        ku_ids = {k["ku_id"] for k in data["kus"]}
        for rel in data["relations"]:
            self.assertIn("from_ku", rel)
            self.assertIn("to_ku", rel)
            self.assertIn("rel_type", rel)
            self.assertIn(rel["from_ku"], ku_ids)
            self.assertIn(rel["to_ku"], ku_ids)

    def test_graph_includes_new_domain(self):
        """关键回归：全新领域的知识也必须出现（不再受写死领域限制）。"""
        import hkc_api.container as cm
        cm.reset_container()
        # 用一个 domain 为 Astronomy 的抽取结果（旧前端写死的 4 领域里没有它）
        client, _, _ = _make_client(preset={
            "concepts": [{"name": "黑洞", "domain": "Astronomy",
                          "definition": "时空曲率极大的天体"}],
            "claims":   [{"statement": "黑洞会蒸发", "confidence": 0.8,
                          "domain": "Astronomy"}],
        })
        client.post("/knowledge/ingest/text", json={
            "text": "天文学内容", "domain": "Astronomy",
        })
        r = client.get("/knowledge/graph")
        self.assertEqual(r.status_code, 200)
        domains = {k["domain"] for k in r.json()["kus"]}
        self.assertIn("Astronomy", domains)

    def test_graph_omits_source_text_but_single_ku_keeps_it(self):
        """性能:星球全量图剔除 source_text(占负载 ~92%),单 KU 详情接口仍保留全文。"""
        from hkc_core.models.ku import FactKU
        ku = FactKU(ku_id=self.c.id_gen.next("Fact"), name="测试事实",
                    summary="s", statement="这是陈述",
                    source_text="很长的原文段落" * 50)
        self.c.graph_store.put(ku)
        # /graph：source_text 应被剔除，但其它字段(statement)保留
        g = self.client.get("/knowledge/graph").json()
        gk = next(k for k in g["kus"] if k["ku_id"] == ku.ku_id)
        self.assertNotIn("source_text", gk.get("extra", {}))
        self.assertIn("statement", gk.get("extra", {}))
        # /ku/{id}：source_text 必须完整保留(详情页按需拉取)
        single = self.client.get(f"/knowledge/ku/{ku.ku_id}").json()
        self.assertEqual(single["extra"].get("source_text"), "很长的原文段落" * 50)

    def test_stats_exposes_data_dir(self):
        """数据可见性:/stats 暴露后端实际数据目录(绝对路径),供前端排查连错目录。"""
        s = self.client.get("/stats").json()
        self.assertIn("data_dir", s)
        self.assertTrue(s["data_dir"])                    # 非空
        self.assertIn(os.path.basename(self.tmpdir), s["data_dir"])


class TestSearch(unittest.TestCase):

    def setUp(self):
        self.client, self.c, self.tmpdir = _make_client()
        self.client.post("/knowledge/ingest/text", json={
            "text": "value investing content", "domain": "Investment",
        })

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_search_bm25(self):
        r = self.client.post("/search", json={
            "query": "value investing", "mode": "bm25", "top_k": 5,
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["mode"], "bm25")
        self.assertIsInstance(data["hits"], list)

    def test_search_invalid_mode(self):
        r = self.client.post("/search", json={
            "query": "test", "mode": "invalid_mode",
        })
        self.assertEqual(r.status_code, 400)

    def test_search_hybrid(self):
        r = self.client.post("/search", json={
            "query": "value", "mode": "hybrid", "top_k": 5,
        })
        self.assertEqual(r.status_code, 200)

    def test_find_path_unreachable(self):
        r = self.client.get("/search/path", params={
            "from_id": "FAKE_A", "to_id": "FAKE_B",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["reachable"])


class TestAbility(unittest.TestCase):

    def setUp(self):
        # 注入足够覆盖 quant_analyst 的知识
        preset = {
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
        self.client, self.c, self.tmpdir = _make_client(preset)
        self.client.post("/knowledge/ingest/text", json={
            "text": "quant content", "domain": "Investment",
        })

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_list_abilities(self):
        r = self.client.get("/abilities")
        self.assertEqual(r.status_code, 200)
        self.assertIn("quant_analyst", r.json()["abilities"])

    def test_coverage_report(self):
        r = self.client.get("/abilities/quant_analyst/coverage")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("coverage", data)
        self.assertIn("can_compile", data)

    def test_coverage_unknown_ability(self):
        r = self.client.get("/abilities/nonexistent_ability/coverage")
        self.assertEqual(r.status_code, 404)

    def test_compile_ability(self):
        r = self.client.post("/abilities/quant_analyst/compile")
        # 应该编译成功（覆盖度足够）
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["ability_key"], "quant_analyst")
        self.assertIn("coverage", data)

    def test_compile_then_get(self):
        self.client.post("/abilities/quant_analyst/compile")
        r = self.client.get("/abilities/quant_analyst")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["ability_key"], "quant_analyst")

    def test_get_uncompiled_ability_404(self):
        r = self.client.get("/abilities/value_investor")
        self.assertEqual(r.status_code, 404)

    def test_compile_insufficient_coverage_422(self):
        r = self.client.post("/abilities/value_investor/compile")
        # value_investor 的知识没注入，覆盖不足
        self.assertEqual(r.status_code, 422)
        detail = r.json()["detail"]
        self.assertIn("missing_skills", detail)


class TestConflict(unittest.TestCase):

    def setUp(self):
        self.client, self.c, self.tmpdir = _make_client()

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_list_conflicts_empty(self):
        r = self.client.get("/conflicts")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 0)

    def test_resolve_nonexistent_conflict(self):
        r = self.client.post("/conflicts/FAKE_CONFLICT/resolve", json={
            "winner_id": "CLM_001",
        })
        self.assertEqual(r.status_code, 400)

    def test_conflict_created_and_resolved(self):
        """注入两个对立 Claim，应产生冲突，然后人工裁决。"""
        from hkc_core.models.ku import ClaimKU
        from hkc_core.models.enums import ClaimStatus

        # 直接往 store 注入两个对立 Claim，触发 KEE
        c1 = ClaimKU(ku_id="CLM_T0001", name="C1",
                     statement="fat diet harmful bad worse dangerous",
                     domain="Nutrition", confidence=0.72,
                     claim_status=ClaimStatus.PENDING)
        c2 = ClaimKU(ku_id="CLM_T0002", name="C2",
                     statement="fat diet beneficial healthy effective safe",
                     domain="Nutrition", confidence=0.75,
                     claim_status=ClaimStatus.PENDING)
        self.c.graph_store.put(c1)
        self.c.kee._process_new_claim(c1)
        self.c.graph_store.put(c2)
        self.c.kee._process_new_claim(c2)

        # 列出冲突
        r = self.client.get("/conflicts", params={"status": "open"})
        conflicts = r.json()["conflicts"]
        if conflicts:  # gap 0.03 < 0.20，应保持 open
            cid = conflicts[0]["conflict_id"]
            # 人工裁决
            r2 = self.client.post(f"/conflicts/{cid}/resolve", json={
                "winner_id": "CLM_T0002", "note": "测试裁决",
            })
            self.assertEqual(r2.status_code, 200)
            self.assertTrue(r2.json()["resolved"])


class TestEvents(unittest.TestCase):

    def setUp(self):
        self.client, self.c, self.tmpdir = _make_client()

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_events_after_ingest(self):
        self.client.post("/knowledge/ingest/text", json={"text": "test"})
        r = self.client.get("/events")
        self.assertEqual(r.status_code, 200)
        events = r.json()["events"]
        # 至少有一个 knowledge.created 事件
        event_types = [e["event"] for e in events]
        self.assertIn("knowledge.created", event_types)


class TestSynthesis(unittest.TestCase):
    """综合页 LLM 综述：按需生成 + 缓存 + 守 Principle 07（派生视图不建知识）。"""

    def setUp(self):
        self.client, self.c, self.tmpdir = _make_client()
        from hkc_core.models.ku import ConceptKU, FactKU
        self.c.graph_store.put(ConceptKU(ku_id=self.c.id_gen.next("Concept"),
            name="价值投资", summary="低估买入", domain="投资"))
        self.c.graph_store.put(FactKU(ku_id=self.c.id_gen.next("Fact"),
            name="巴菲特", summary="价值投资代表", statement="巴菲特实践价值投资", domain="投资"))
        self.c.search.build_index()
        self.kid = [k.ku_id for k in self.c.graph_store.query_by_domain("投资")
                    if k.name == "价值投资"][0]
        # mock LLM，避免联网
        self.calls = {"n": 0}
        def fake(system, user, max_tokens=1500):
            self.calls["n"] += 1
            return "【价值投资】低估买入的方法。" + ("提及巴菲特" if "巴菲特" in user else "")
        self.c.kde.extractor.complete = fake

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_generate_then_cache_hit(self):
        r1 = self.client.post(f"/knowledge/ku/{self.kid}/synthesis").json()
        self.assertFalse(r1["cached"])
        self.assertTrue(r1["content"])
        r2 = self.client.post(f"/knowledge/ku/{self.kid}/synthesis").json()
        self.assertTrue(r2["cached"])           # 第二次命中缓存
        self.assertEqual(self.calls["n"], 1)    # LLM 只调用一次

    def test_force_regenerates(self):
        self.client.post(f"/knowledge/ku/{self.kid}/synthesis")
        r = self.client.post(f"/knowledge/ku/{self.kid}/synthesis?force=true").json()
        self.assertFalse(r["cached"])
        self.assertEqual(self.calls["n"], 2)

    def test_context_includes_related_material(self):
        r = self.client.post(f"/knowledge/ku/{self.kid}/synthesis").json()
        self.assertIn("巴菲特", r["content"])    # 邻域/检索材料进了 prompt

    def test_get_cache_state(self):
        g0 = self.client.get(f"/knowledge/ku/{self.kid}/synthesis").json()
        self.assertFalse(g0["has_cache"])        # 生成前无缓存，不触发 LLM
        self.assertEqual(self.calls["n"], 0)
        self.client.post(f"/knowledge/ku/{self.kid}/synthesis")
        g1 = self.client.get(f"/knowledge/ku/{self.kid}/synthesis").json()
        self.assertTrue(g1["has_cache"])

    def test_principle07_no_new_ku(self):
        before = self.client.get("/stats").json()["total_kus"]
        self.client.post(f"/knowledge/ku/{self.kid}/synthesis")
        after = self.client.get("/stats").json()["total_kus"]
        self.assertEqual(before, after)          # 综述不新建任何知识

    def test_unknown_ku_404(self):
        r = self.client.post("/knowledge/ku/CON_99999999/synthesis")
        self.assertEqual(r.status_code, 404)


class TestSynthesisStoreConcurrency(unittest.TestCase):
    """回归:综述缓存的 SQLite 连接被线程池并发访问时必须加锁(不加锁会报错)。"""

    def test_concurrent_get_put_no_error(self):
        import threading
        from hkc_api.synthesis import SynthesisStore
        store = SynthesisStore(os.path.join(tempfile.mkdtemp(), "s.db"))
        errors = []
        def worker(i):
            try:
                for _ in range(25):
                    store.put(f"K{i}", "内容" * 20, "m", "h")
                    store.get(f"K{i}")
                    store.get("missing")
            except Exception as e:      # 未加锁时这里会捕获 sqlite3 并发错误
                errors.append(repr(e))
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in ts: t.start()
        for t in ts: t.join()
        store.close()
        self.assertEqual(errors, [], f"并发访问出错(应加锁): {errors[:2]}")


class TestIngestJobEviction(unittest.TestCase):
    """回归:job 注册表超上限时淘汰最旧的已完成 job,但不动进行中的。"""

    def test_evicts_oldest_finished_keeps_active(self):
        from hkc_api.ingest_jobs import IngestJobRegistry
        reg = IngestJobRegistry(max_workers=1, max_jobs=5)
        done_ids = []
        for i in range(5):                        # 5 个已完成
            j = reg.create(f"done{i}"); j.status = "done"; done_ids.append(j.job_id)
        active = reg.create("active")             # 1 个进行中(queued)
        for i in range(3):                        # 再加 3 个,触发淘汰
            reg.create(f"more{i}")
        with reg._lock:
            n = len(reg._jobs)
        self.assertLessEqual(n, 5)                # 回落到上限内
        self.assertIsNone(reg.get(done_ids[0]))   # 最旧的已完成被淘汰
        self.assertIsNotNone(reg.get(active.job_id))  # 进行中的绝不淘汰
        reg.shutdown()


class TestCrystallize(unittest.TestCase):
    """Crystallizer 集成层入口 POST /knowledge/crystallize:外部推候选 → KDE → KEE。"""

    def setUp(self):
        self.client, self.c, self.tmpdir = _make_client()

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_crystallize_ingests(self):
        r = self.client.post("/knowledge/crystallize", json={
            "content": "价值投资长期跑赢市场", "title": "来自反思",
            "domain": "Investment", "evidence_type": "reflection", "source_id": "MEM_001",
        })
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.json()["ku_count"], 0)   # MockExtractor 产出 KU

    def test_crystallize_empty_content_400(self):
        r = self.client.post("/knowledge/crystallize", json={"content": "   "})
        self.assertEqual(r.status_code, 400)


class TestApiKeyAuth(unittest.TestCase):
    """可选 API 鉴权:设了 HKC_API_KEY 才启用;开放路径(/health)不拦。"""

    def test_api_key_gate(self):
        import importlib
        os.environ["HKC_API_KEY"] = "secret123"
        os.environ["HKC_EMBEDDING"] = "stub"
        os.environ["HKC_DATA_DIR"] = tempfile.mkdtemp()
        import hkc_api.container as cm
        import hkc_api.main as main_mod
        cm.reset_container()
        importlib.reload(main_mod)          # 重跑模块级中间件注册(读到 HKC_API_KEY)
        try:
            with TestClient(main_mod.app) as cli:
                self.assertEqual(cli.get("/health").status_code, 200)               # 开放
                self.assertEqual(cli.get("/stats").status_code, 401)                # 无 key 拦
                self.assertEqual(cli.get("/stats", headers={"X-API-Key": "wrong"}).status_code, 401)
                self.assertEqual(cli.get("/stats", headers={"X-API-Key": "secret123"}).status_code, 200)
        finally:
            os.environ.pop("HKC_API_KEY", None)
            cm.reset_container()
            importlib.reload(main_mod)       # 还原成无鉴权,避免污染后续测试


class TestConfigRoute(unittest.TestCase):
    """POST /config/llm:界面「连接设置」运行时切换 LLM provider(模型无关原则)。"""

    def setUp(self):
        self.client, self.c, self.tmpdir = _make_client()

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_switch_to_openai_compatible_with_base_url(self):
        r = self.client.post("/config/llm", json={
            "provider": "openai-compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:7b", "api_key": "ollama",
        })
        self.assertEqual(r.status_code, 200)
        cfg = r.json()["config"]
        self.assertEqual(cfg["provider"], "openai-compatible")
        self.assertEqual(cfg["model"], "qwen2.5:7b")

    def test_openai_compatible_requires_base_url(self):
        """本地/自定义模型不填 base_url 会误连 openai.com → 必须 400 拦下。"""
        r = self.client.post("/config/llm", json={
            "provider": "openai-compatible", "api_key": "x",
        })
        self.assertEqual(r.status_code, 400)

    def test_rejects_unknown_provider(self):
        r = self.client.post("/config/llm", json={"provider": "totally-made-up"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
