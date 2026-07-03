"""
tests / test_crystallizer.py
KnowledgeCrystallizer 测试(重构后架构)。

验证:
  - 事件 → KnowledgeCandidate → KnowledgeIngress → HKC 全链路
  - 事件级去重、二次筛选、空内容跳过
  - 结构化 Evidence、light fingerprint
  - 协议隔离(KnowledgeEventSource / KnowledgeIngress 可注入)
  - 职责边界:Crystallizer 不查 KU、不做合并决策
  - 可扩展性:新增事件类型只需注册翻译器
"""
import sys, os, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["HF_HUB_OFFLINE"] = "1"

from hkc_kde.models import RawExtraction, ExtractionResult
from hkc_kde.extractor import Extractor

from hkc_crystallizer import (
    KnowledgeCrystallizer, HKCIngress, SystemBusEventSource,
    KnowledgeCandidate, CandidateEvidence,
    KnowledgeEventSource, KnowledgeIngress,
)
from hkc_crystallizer.adapters import translate_memory_matured


# ── 极简总线 ──
class FakeBus:
    def __init__(self): self._subs = {}
    def subscribe(self, channel, handler): self._subs.setdefault(channel, []).append(handler)
    def publish(self, channel, payload):
        class Msg:
            def __init__(s, p): s.payload = p
        for h in self._subs.get(channel, []): h(Msg(payload))


class MockExtractor(Extractor):
    def extract(self, chunks):
        doc_id = chunks[0].doc_id if chunks else "DOC_T"
        return ExtractionResult(doc_id=doc_id, source=doc_id, items=[
            RawExtraction(concepts=[{"name": "结晶概念", "domain": "Investment",
                                     "definition": "from candidate"}],
                          claims=[], entities=[], facts=[])])


def _make_hkc_ingress():
    import hkc_api.container as cm
    cm.reset_container()
    c = cm.init_container(data_dir=tempfile.mkdtemp(), embedding_kind="stub")
    c.kde.extractor = MockExtractor()
    from hkc_sdk import connect
    return HKCIngress(connect(container=c)), c


def _payload(mid="mem_001", mtype="reflection", title="过度交易反思",
             content="本季度过度交易导致回撤", agent="finance", conf=0.9):
    return {"memory_id": mid, "memory_type": mtype, "title": title,
            "content": content, "confidence": conf, "temporal_weight": 0.9,
            "agent_id": agent, "shared": False, "tags": [f"agent:{agent}"],
            "reason": "matured(test)"}


# ── 记录型 Ingress(捕获送入的 candidate)──
class CapturingIngress:
    def __init__(self): self.candidates = []
    def ingest(self, candidate):
        self.candidates.append(candidate)
        return None


class TestCrystallizerCore(unittest.TestCase):
    def setUp(self):
        self.ingress, self.container = _make_hkc_ingress()
        self.bus = FakeBus()
        self.cz = KnowledgeCrystallizer(
            ingress=self.ingress, source=SystemBusEventSource(self.bus))

    def tearDown(self):
        import hkc_api.container as cm
        cm.reset_container()

    def test_event_crystallizes(self):
        self.bus.publish("memory.matured", _payload())
        s = self.cz.get_stats()
        self.assertEqual(s["received"], 1)
        self.assertEqual(s["crystallized"], 1)

    def test_event_level_dedup(self):
        p = _payload(mid="mem_dup")
        self.bus.publish("memory.matured", p)
        self.bus.publish("memory.matured", p)
        s = self.cz.get_stats()
        self.assertEqual(s["crystallized"], 1)
        self.assertEqual(s["skipped_dup"], 1)

    def test_empty_content_skipped(self):
        self.bus.publish("memory.matured", _payload(mid="e", title="", content=""))
        self.assertEqual(self.cz.get_stats()["skipped_empty"], 1)

    def test_unknown_event_skipped(self):
        self.bus.publish("memory.matured", _payload())  # known
        # 直接喂未知事件给 handler
        self.cz._on_event("meeting.finished", {"x": 1})
        s = self.cz.get_stats()
        self.assertEqual(s["skipped_filter"], 1)  # 无翻译器


class TestCandidateConstruction(unittest.TestCase):
    def test_translate_produces_structured_evidence(self):
        cand = translate_memory_matured(_payload(mid="mem_x", agent="finance"))
        self.assertIsInstance(cand, KnowledgeCandidate)
        self.assertEqual(len(cand.evidence), 1)
        ev = cand.evidence[0]
        self.assertEqual(ev.evidence_type, "memory")
        self.assertEqual(ev.source_id, "mem_x")
        self.assertEqual(ev.agent, "finance")
        # 结构化 extra 保留 memory 元信息
        self.assertIn("memory_type", ev.extra)
        self.assertIn("matured_reason", ev.extra)

    def test_light_fingerprint_computed(self):
        cand = translate_memory_matured(_payload())
        self.assertTrue(cand.light_fingerprint)
        self.assertEqual(len(cand.light_fingerprint), 16)

    def test_fingerprint_normalizes(self):
        a = KnowledgeCandidate.compute_light_fingerprint("Hello  World")
        b = KnowledgeCandidate.compute_light_fingerprint("hello world")
        self.assertEqual(a, b)  # 归一化后相同

    def test_translate_empty_returns_none(self):
        self.assertIsNone(translate_memory_matured(
            {"memory_id": "x", "title": "", "content": ""}))

    def test_event_refs_set(self):
        cand = translate_memory_matured(_payload(mid="mem_ref"))
        self.assertEqual(cand.event_refs, ["mem_ref"])


class TestProvenanceAndProtocols(unittest.TestCase):
    def test_ingress_carries_provenance(self):
        cap = CapturingIngress()
        bus = FakeBus()
        cz = KnowledgeCrystallizer(ingress=cap, source=SystemBusEventSource(bus))
        bus.publish("memory.matured", _payload(mid="mem_p", agent="research"))
        self.assertEqual(len(cap.candidates), 1)
        cand = cap.candidates[0]
        self.assertEqual(cand.evidence[0].source_id, "mem_p")
        self.assertEqual(cand.evidence[0].agent, "research")

    def test_hkcingress_translates_to_kde(self):
        captured = {}
        def fake_ingest(text, source="", source_title="", domain=""):
            captured.update(text=text, source=source, domain=domain)
            return None
        ing = HKCIngress(fake_ingest)
        cand = translate_memory_matured(_payload(mid="mem_t", agent="finance"))
        ing.ingest(cand)
        self.assertIn("memory", captured["source"])
        self.assertIn("agent:finance", captured["source"])
        self.assertIn("mem_t", captured["source"])

    def test_protocols_runtime_checkable(self):
        self.assertIsInstance(SystemBusEventSource(FakeBus()), KnowledgeEventSource)
        self.assertIsInstance(HKCIngress(lambda *a, **k: None), KnowledgeIngress)

    def test_second_filter(self):
        cap = CapturingIngress()
        bus = FakeBus()
        cz = KnowledgeCrystallizer(
            ingress=cap, source=SystemBusEventSource(bus),
            candidate_filter=lambda c: c.evidence[0].confidence >= 0.95)
        bus.publish("memory.matured", _payload(conf=0.9))  # < 0.95
        self.assertEqual(cz.get_stats()["skipped_filter"], 1)
        self.assertEqual(len(cap.candidates), 0)

    def test_ingest_failure_counted(self):
        class FailIngress:
            def ingest(self, c): raise RuntimeError("HKC down")
        bus = FakeBus()
        cz = KnowledgeCrystallizer(ingress=FailIngress(), source=SystemBusEventSource(bus))
        bus.publish("memory.matured", _payload())  # 不应抛
        self.assertEqual(cz.get_stats()["failed"], 1)


class TestExtensibility(unittest.TestCase):
    """新增事件类型只需注册翻译器,不改 Crystallizer。"""

    def test_custom_event_translator(self):
        def translate_meeting(payload):
            return KnowledgeCandidate(
                content=payload.get("summary", ""),
                title=payload.get("topic", ""),
                evidence=[CandidateEvidence(
                    evidence_type="meeting", source_id=payload.get("meeting_id", ""))],
                event_refs=[payload.get("meeting_id", "")])

        cap = CapturingIngress()
        bus = FakeBus()
        cz = KnowledgeCrystallizer(
            ingress=cap, source=SystemBusEventSource(bus),
            events=["memory.matured", "meeting.finished"],
            translators={"meeting.finished": translate_meeting})
        bus.publish("meeting.finished",
                    {"meeting_id": "mtg_1", "topic": "架构评审",
                     "summary": "决定采用事件驱动架构"})
        self.assertEqual(len(cap.candidates), 1)
        self.assertEqual(cap.candidates[0].evidence[0].evidence_type, "meeting")


if __name__ == "__main__":
    unittest.main(verbosity=2)
