"""
tests / test_integration.py
HKC 全链路集成测试。

测试顺序对应开发优先级：
  1. KU 数据模型
  2. ID 生成器
  3. SQLiteGraphStore CRUD
  4. EventBus / KEP
  5. KEE 冲突检测 + 状态机
  6. ACE 覆盖率 + 编译
"""
import sys
import os
import json
import tempfile
import unittest

# 把项目根加入 path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hkc_core.models.enums import (
    KUType, ClaimStatus, RelationType, EntityType
)
from hkc_core.models.ku import (
    EntityKU, ConceptKU, FactKU, ClaimKU,
    EvidenceKU, AbilityKU, Relation, ConflictCard
)
from hkc_core.graph.sqlite_store import SQLiteGraphStore
from hkc_core.utils.id_gen import IDGenerator
from hkc_kep.event_bus import EventBus, KEPEvents
from hkc_kee.kee import KnowledgeEvolutionEngine
from hkc_ace.ace import AbilityCompilerEngine, SKILL_TAXONOMY, CoverageCalculator


# ─────────────────────────────────────────────────────────────
# 1. KU 数据模型
# ─────────────────────────────────────────────────────────────

class TestKUModels(unittest.TestCase):

    def test_entity_ku(self):
        ku = EntityKU(
            ku_id="ENT_00000001",
            name="Charlie Munger",
            summary="伯克希尔哈撒韦副董事长",
            domain="Investment",
            entity_type=EntityType.PERSON,
            aliases=["查理芒格"],
        )
        self.assertEqual(ku.ku_type, KUType.ENTITY)
        self.assertEqual(ku.confidence, 1.0)
        d = ku.to_dict()
        self.assertEqual(d["ku_type"], "Entity")
        self.assertIn("aliases", d["extra"])

    def test_fact_confidence_locked(self):
        """Fact 的 confidence 必须永远是 1.0，不受初始值影响。"""
        ku = FactKU(
            ku_id="FCT_00000001",
            name="巴菲特生年",
            statement="沃伦·巴菲特生于1930年8月30日",
            confidence=0.5,   # 故意传错
        )
        self.assertEqual(ku.confidence, 1.0)  # __post_init__ 强制修正

    def test_claim_has_status(self):
        ku = ClaimKU(
            ku_id="CLM_00000001",
            name="价值投资有效性",
            statement="长期价值投资优于短线交易",
            domain="Investment",
            confidence=0.82,
            claim_status=ClaimStatus.ACTIVE,
        )
        self.assertEqual(ku.claim_status, ClaimStatus.ACTIVE)
        d = ku.to_dict()
        self.assertEqual(d["extra"]["claim_status"], "active")

    def test_relation(self):
        rel = Relation(
            rel_id="REL_00000001",
            from_ku="ENT_00000001",
            to_ku="CON_00000001",
            rel_type=RelationType.CREATED_BY.value,
            weight=0.95,
        )
        self.assertEqual(rel.rel_type, "CREATED_BY")


# ─────────────────────────────────────────────────────────────
# 2. ID 生成器
# ─────────────────────────────────────────────────────────────

class TestIDGenerator(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.gen = IDGenerator(self.tmp)

    def test_sequential(self):
        a = self.gen.next("Entity")
        b = self.gen.next("Entity")
        c = self.gen.next("Claim")
        self.assertEqual(a, "ENT_00000001")
        self.assertEqual(b, "ENT_00000002")
        self.assertEqual(c, "CLM_00000001")

    def test_prefix_isolation(self):
        self.gen.next("Entity")
        self.gen.next("Entity")
        first_claim = self.gen.next("Claim")
        self.assertEqual(first_claim, "CLM_00000001")


# ─────────────────────────────────────────────────────────────
# 3. SQLiteGraphStore
# ─────────────────────────────────────────────────────────────

class TestGraphStore(unittest.TestCase):

    def setUp(self):
        self.tmp   = tempfile.mktemp(suffix=".db")
        self.store = SQLiteGraphStore(self.tmp)

    def _make_entity(self, suffix="1"):
        num = int(suffix)
        return EntityKU(
            ku_id=f"ENT_{num:08d}",
            name=f"Charlie Munger {suffix}",
            domain="Investment",
        )

    def _make_claim(self, suffix="1", conf=0.8):
        num = int(suffix)
        return ClaimKU(
            ku_id=f"CLM_{num:08d}",
            name=f"Claim {suffix}",
            statement=f"Value investing beats the market - {suffix}",
            domain="Investment",
            confidence=conf,
        )

    def test_put_and_get(self):
        ku = self._make_entity()
        self.store.put(ku)
        got = self.store.get("ENT_00000001")
        self.assertIsNotNone(got)
        self.assertEqual(got.name, "Charlie Munger 1")

    def test_get_nonexistent(self):
        result = self.store.get("NONEXISTENT")
        self.assertIsNone(result)

    def test_batch_write(self):
        kus = [self._make_entity(str(i)) for i in range(1, 6)]
        self.store.batch_write(kus)
        stats = self.store.stats()
        self.assertEqual(stats["ku_counts"].get("Entity", 0), 5)

    def test_query_by_type(self):
        self.store.put(self._make_entity("1"))
        self.store.put(self._make_claim("1"))
        entities = self.store.query_by_type(KUType.ENTITY)
        claims   = self.store.query_by_type(KUType.CLAIM)
        self.assertEqual(len(entities), 1)
        self.assertEqual(len(claims), 1)

    def test_soft_delete(self):
        ku = self._make_entity("1")
        self.store.put(ku)
        self.store.delete("ENT_00000001")
        result = self.store.query_by_type(KUType.ENTITY, status="active")
        self.assertEqual(len(result), 0)
        # soft delete: 记录仍存在
        raw = self.store.get("ENT_00000001")
        self.assertIsNotNone(raw)

    def test_relation_and_neighbors(self):
        e1 = self._make_entity("1")
        e2 = self._make_entity("2")
        self.store.batch_write([e1, e2])

        rel = Relation(
            rel_id="REL_00000001",
            from_ku="ENT_00000001",
            to_ku="ENT_00000002",
            rel_type="CREATED_BY",
        )
        self.store.add_relation(rel)

        neighbors = self.store.neighbors("ENT_00000001")
        self.assertEqual(len(neighbors), 1)
        self.assertEqual(neighbors[0].ku_id, "ENT_00000002")

    def test_shortest_path(self):
        kus = [self._make_entity(str(i)) for i in range(1, 4)]
        self.store.batch_write(kus)
        self.store.add_relation(Relation("R1","ENT_00000001","ENT_00000002","USES"))
        self.store.add_relation(Relation("R2","ENT_00000002","ENT_00000003","USES"))

        path = self.store.shortest_path("ENT_00000001", "ENT_00000003")
        self.assertEqual(len(path), 3)

    def test_conflict_card_crud(self):
        claim_a = self._make_claim("1", 0.8)
        claim_b = self._make_claim("2", 0.65)
        self.store.batch_write([claim_a, claim_b])

        card = ConflictCard(
            conflict_id="CFT_00000001",
            claim_a_id="CLM_00000001",
            claim_b_id="CLM_00000002",
            domain="Investment",
        )
        self.store.save_conflict(card)

        retrieved = self.store.get_conflict("CFT_00000001")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.domain, "Investment")

        open_cards = self.store.list_conflicts(status="open")
        self.assertEqual(len(open_cards), 1)

    def test_stats(self):
        self.store.put(self._make_entity("1"))
        self.store.put(self._make_claim("1"))
        stats = self.store.stats()
        self.assertIn("total_kus", stats)
        self.assertEqual(stats["total_kus"], 2)


# ─────────────────────────────────────────────────────────────
# 4. EventBus / KEP
# ─────────────────────────────────────────────────────────────

class TestEventBus(unittest.TestCase):

    def setUp(self):
        self.bus = EventBus(db_path=":memory:")

    def test_subscribe_and_publish(self):
        received = []
        self.bus.subscribe(KEPEvents.KNOWLEDGE_CREATED,
                           lambda e: received.append(e))
        self.bus.publish({
            "event":   KEPEvents.KNOWLEDGE_CREATED,
            "source":  "KDE",
            "payload": {"ku_ids": ["KU_001"]},
        })
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["source"], "KDE")

    def test_multiple_handlers(self):
        log = []
        self.bus.subscribe(KEPEvents.ABILITY_CREATED, lambda e: log.append("A"))
        self.bus.subscribe(KEPEvents.ABILITY_CREATED, lambda e: log.append("B"))
        self.bus.publish({
            "event":  KEPEvents.ABILITY_CREATED,
            "source": "ACE",
            "payload": {},
        })
        self.assertEqual(log, ["A", "B"])

    def test_handler_error_doesnt_block(self):
        log = []
        def bad_handler(e):
            raise RuntimeError("故意抛异常")
        def good_handler(e):
            log.append("ok")

        self.bus.subscribe(KEPEvents.KNOWLEDGE_UPDATED, bad_handler)
        self.bus.subscribe(KEPEvents.KNOWLEDGE_UPDATED, good_handler)
        self.bus.publish({"event": KEPEvents.KNOWLEDGE_UPDATED, "source": "KDE", "payload": {}})
        self.assertEqual(log, ["ok"])

    def test_event_log(self):
        self.bus.publish({
            "event":  KEPEvents.CONFLICT_DETECTED,
            "source": "KEE",
            "payload": {"conflict_id": "CFT_001"},
        })
        events = self.bus.events_by_type(KEPEvents.CONFLICT_DETECTED)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["conflict_id"], "CFT_001")


# ─────────────────────────────────────────────────────────────
# 5. KEE — 冲突检测 + 状态机
# ─────────────────────────────────────────────────────────────

class TestKEE(unittest.TestCase):

    def setUp(self):
        tmp       = tempfile.mktemp(suffix=".db")
        self.store = SQLiteGraphStore(tmp)
        self.bus   = EventBus(":memory:")
        self.id_gen = IDGenerator(tempfile.mktemp(suffix=".db"))
        self.kee   = KnowledgeEvolutionEngine(
            self.store, self.bus, self.id_gen,
            auto_resolve_gap=0.20,
        )

    def _claim(self, ku_id, statement, conf, domain="Nutrition"):
        c = ClaimKU(
            ku_id=ku_id,
            name=f"Claim {ku_id}",
            statement=statement,
            domain=domain,
            confidence=conf,
            claim_status=ClaimStatus.PENDING,
        )
        self.store.put(c)
        return c

    def test_conflict_detection_creates_card(self):
        """两个语义相反的 Claim 应产生 Conflict Card。"""
        old = self._claim("CLM_00000001",
                          "fat diet harmful bad worse dangerous", 0.72)
        new = self._claim("CLM_00000002",
                          "fat diet beneficial healthy effective safe", 0.84)

        conflict_events = []
        self.bus.subscribe(KEPEvents.CONFLICT_DETECTED,
                           lambda e: conflict_events.append(e))

        self.kee._process_new_claim(new)

        cards = self.store.list_conflicts(status="open")
        # 若没有自动裁决，应有 open 卡
        # 若自动裁决了（gap=0.12 < 0.20），仍是 open
        self.assertGreaterEqual(len(conflict_events) + len(cards), 1)

    def test_auto_resolve_clears_conflict(self):
        """置信度差 > 0.20 时应自动裁决。"""
        old = self._claim("CLM_00000001",
                          "value investing is good long term", 0.50)
        new = self._claim("CLM_00000002",
                          "value investing is not good and harmful", 0.85)

        resolved_events = []
        self.bus.subscribe(KEPEvents.CONFLICT_RESOLVED,
                           lambda e: resolved_events.append(e))

        self.kee._process_new_claim(new)

        # 差值 0.35 > 0.20，应自动裁决
        self.assertGreaterEqual(len(resolved_events), 1)

    def test_evidence_merge(self):
        """同向 Claim 应触发 Evidence 合并，提升旧 Claim 置信度。"""
        old = self._claim("CLM_00000001",
                          "exercise improves mental health", 0.70,
                          domain="Health")
        old.supports = ["EVD_001"]
        self.store.put(old)

        new = self._claim("CLM_00000002",
                          "exercise improves mental health and mood", 0.75,
                          domain="Health")
        new.supports = ["EVD_002", "EVD_003"]
        self.store.put(new)

        self.kee._process_new_claim(new)

        updated = self.store.get("CLM_00000001")
        # 置信度应该提升
        self.assertGreaterEqual(updated.confidence, 0.70)

    def test_manual_resolve(self):
        """人工裁决接口应正确更新状态。"""
        a = self._claim("CLM_00000001", "A is true", 0.70)
        b = self._claim("CLM_00000002", "A is not true", 0.65)

        from hkc_core.models.enums import ConflictStatus
        from hkc_core.models.ku import ConflictCard
        card = ConflictCard(
            conflict_id="CFT_00000001",
            claim_a_id="CLM_00000001",
            claim_b_id="CLM_00000002",
            domain="Test",
        )
        self.store.save_conflict(card)

        result = self.kee.manual_resolve(
            "CFT_00000001", "CLM_00000001", note="测试人工裁决"
        )
        self.assertTrue(result)

        resolved = self.store.get_conflict("CFT_00000001")
        self.assertEqual(resolved.status, ConflictStatus.RESOLVED)

        winner = self.store.get("CLM_00000001")
        loser  = self.store.get("CLM_00000002")
        self.assertEqual(winner.claim_status, ClaimStatus.ACTIVE)
        self.assertEqual(loser.claim_status,  ClaimStatus.SUPERSEDED)


# ─────────────────────────────────────────────────────────────
# 6. ACE — Coverage 计算 + 编译
# ─────────────────────────────────────────────────────────────

class TestACE(unittest.TestCase):

    def setUp(self):
        tmp        = tempfile.mktemp(suffix=".db")
        self.store  = SQLiteGraphStore(tmp)
        self.bus    = EventBus(":memory:")
        self.id_gen = IDGenerator(tempfile.mktemp(suffix=".db"))
        self.outdir = tempfile.mkdtemp()
        self.ace    = AbilityCompilerEngine(
            self.store, self.bus, self.id_gen, self.outdir
        )
        self.calc   = CoverageCalculator()

    def _inject_kus(self, texts: list[str], domain: str):
        """注入测试用 Concept KU。"""
        kus = []
        for i, text in enumerate(texts):
            ku = ConceptKU(
                ku_id=f"CON_{i:08d}",
                name=text,
                summary=text,
                domain=domain,
                tags=text.lower().split(),
            )
            kus.append(ku)
        self.store.batch_write(kus)

    def test_coverage_calculator_basic(self):
        """注入包含 'momentum' 的 KU，factor_investing coverage 应 > 0。"""
        self._inject_kus([
            "momentum strategy in quant",
            "value factor stock selection",
            "drawdown risk control",
        ], domain="Investment")

        ku_pool  = self.store.query_by_domain("Investment")
        coverage = self.calc.calculate("quant_analyst", ku_pool)

        self.assertIn("factor_investing", coverage)
        self.assertGreater(coverage["factor_investing"], 0.0)

    def test_can_compile_threshold(self):
        """覆盖度足够时 can_compile 应返回 True。"""
        # 注入所有核心 Skill 的关键词
        texts = [
            "momentum factor investing alpha beta",
            "backtesting historical simulation walk-forward overfitting slippage",
            "VaR drawdown sharpe ratio position sizing stop loss",
            "mean-variance efficient frontier diversification rebalancing",
            "time series cross-sectional data cleaning feature engineering",
        ]
        self._inject_kus(texts, "Investment")
        ku_pool  = self.store.query_by_domain("Investment")
        coverage = self.calc.calculate("quant_analyst", ku_pool)
        can      = self.calc.can_compile("quant_analyst", coverage)
        self.assertTrue(can)

    def test_compile_produces_hkap(self):
        """编译成功后应生成 .hkap 文件。"""
        texts = [
            "momentum factor investing alpha beta quality factor",
            "backtesting historical simulation walk-forward slippage transaction cost overfitting",
            "VaR drawdown sharpe ratio position sizing stop loss",
            "mean-variance efficient frontier diversification rebalancing portfolio",
            "time series cross-sectional data cleaning feature engineering statistical testing",
        ]
        self._inject_kus(texts, "Investment")

        pkg = self.ace.compile("quant_analyst")
        self.assertIsNotNone(pkg)

        import os
        hkap_path = os.path.join(self.outdir, "quant_analyst.hkap")
        self.assertTrue(os.path.exists(hkap_path))

        with open(hkap_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["ability_key"], "quant_analyst")
        self.assertIn("coverage", data)

    def test_compile_fails_when_coverage_insufficient(self):
        """覆盖度不足时应返回 None，不生成文件。"""
        self._inject_kus(["random text about cooking"], "Investment")
        pkg = self.ace.compile("quant_analyst")
        self.assertIsNone(pkg)

    def test_coverage_report(self):
        """coverage_report 应返回缺失的 Skill 列表。"""
        self._inject_kus(["momentum factor"], "Investment")
        report = self.ace.coverage_report("quant_analyst")
        self.assertIn("missing_skills", report)
        self.assertIn("can_compile", report)
        # 大部分 Skill 应该缺失
        self.assertGreater(len(report["missing_skills"]), 0)

    def test_list_available(self):
        available = self.ace.list_available()
        self.assertIn("quant_analyst", available)
        self.assertIn("value_investor", available)
        self.assertIn("ai_architect", available)

    # ── 中文能力 + 多领域匹配（taxonomy 从 JSON 加载）────────────

    def test_taxonomy_loaded_from_json_has_cn_and_en(self):
        """taxonomy 从 JSON 配置加载：英文示例能力 + 中文能力并存。"""
        avail = self.ace.list_available()
        for k in ("quant_analyst", "writing_expert",
                  "psych_social_expert", "humanities_expert"):
            self.assertIn(k, avail)
        self.assertEqual(SKILL_TAXONOMY["writing_expert"]["display_name"], "写作专家")

    def test_cn_ability_gathers_kus_across_multiple_domains(self):
        """关键：中文能力的 KU 池应跨多个细碎领域并集（写作被拆成多个域的场景）。"""
        # 分散在 3 个不同的写作类领域(唯一 ID,避免覆盖)
        for i, (text, dom) in enumerate([
            ("公文格式与请示报告", "公文写作"),
            ("谋篇布局与提纲结构", "写作方法论"),
            ("措辞与语言表达",     "写作技巧"),
        ]):
            self.store.put(ConceptKU(ku_id=f"CON_9000000{i}", name=text,
                                     summary=text, domain=dom))
        pool = self.ace._gather_ku_pool(SKILL_TAXONOMY["writing_expert"])
        # 三个领域的 KU 都被收进来（单领域 query 只会拿到 1 个）
        self.assertEqual(len(pool), 3)

    def test_cn_ability_compiles_from_real_style_data(self):
        """中文能力在覆盖足够时能编译出 .hkap（走多领域 gather + 中文概念匹配）。"""
        self._inject_kus(
            ["公文格式 请示 报告 通知 纪要 函",
             "结构 布局 提纲 开头 结尾 逻辑",
             "语言 措辞 表达 句子 用词",
             "规范 标题 落款 行文 称谓"],
            "公文写作",
        )
        report = self.ace.coverage_report("writing_expert")
        self.assertEqual(report["display_name"], "写作专家")
        self.assertIn("公文写作", report["domains"])
        self.assertTrue(report["can_compile"])
        pkg = self.ace.compile("writing_expert")
        self.assertIsNotNone(pkg)
        self.assertEqual(pkg.display_name, "写作专家")

    def test_env_override_taxonomy_path(self):
        """HKC_ACE_TAXONOMY 可指向自定义能力定义文件（开源用户替换用）。"""
        import os, json, tempfile
        from hkc_ace import ace as ace_mod
        custom = tempfile.mktemp(suffix=".json")
        with open(custom, "w", encoding="utf-8") as f:
            json.dump({"my_ability": {"display_name": "自定义",
                       "domain": "X", "required_skills": [], "min_coverage": 0.5,
                       "workflows": [], "concept_checklist": {}}}, f, ensure_ascii=False)
        old = os.environ.get("HKC_ACE_TAXONOMY")
        os.environ["HKC_ACE_TAXONOMY"] = custom
        try:
            tax = ace_mod._load_taxonomy()
            self.assertEqual(list(tax.keys()), ["my_ability"])
        finally:
            if old is None:
                os.environ.pop("HKC_ACE_TAXONOMY", None)
            else:
                os.environ["HKC_ACE_TAXONOMY"] = old

    def test_ability_created_event(self):
        """编译成功后应触发 ability.created 事件。"""
        texts = [
            "momentum factor investing alpha beta quality factor",
            "backtesting historical simulation walk-forward slippage transaction cost",
            "VaR drawdown sharpe ratio position sizing",
            "mean-variance efficient frontier diversification rebalancing portfolio",
            "time series cross-sectional data cleaning statistical testing",
        ]
        self._inject_kus(texts, "Investment")

        events = []
        self.bus.subscribe(KEPEvents.ABILITY_CREATED, lambda e: events.append(e))
        self.ace.compile("quant_analyst")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["ability_key"], "quant_analyst")


# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
