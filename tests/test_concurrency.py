"""
tests / test_concurrency.py
系统级并发回归:异步摄入(后台线程 + 2 worker)会让 event_bus / KEE 去重 / 搜索索引
被多线程并发读写。这些测试在**未加锁**时会报 sqlite 错误、faiss 崩溃或漏去重;
加锁后应全部通过。守护本轮全系统并发审核的修复。
"""
import os, sys, tempfile, threading, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["HF_HUB_OFFLINE"] = "1"

from hkc_kep.event_bus import EventBus
from hkc_core.graph.sqlite_store import SQLiteGraphStore
from hkc_core.models.ku import ConceptKU
from hkc_search.hybrid import HybridSearch
from hkc_search.vector_search import VectorIndex
from hkc_search.embedding_backends import make_backend
from hkc_kee.dedup import KnowledgeDeduplicator


def _run(fn, n_threads):
    errors = []
    def wrap(i):
        try: fn(i)
        except Exception as e: errors.append(repr(e))
    ts = [threading.Thread(target=wrap, args=(i,)) for i in range(n_threads)]
    for t in ts: t.start()
    for t in ts: t.join()
    return errors


class TestEventBusConcurrency(unittest.TestCase):
    def test_concurrent_publish_no_error_no_loss(self):
        bus = EventBus(":memory:")
        def w(i):
            for j in range(30):
                bus.publish({"event": "knowledge.created", "source": "T",
                             "payload": {"i": i, "j": j}})
        errors = _run(w, 8)
        self.assertEqual(errors, [], f"并发 publish 出错: {errors[:2]}")
        # 8*30=240 条事件,event_id 唯一,一条不丢(验证审计日志写入未因并发丢失)
        self.assertEqual(len(bus.recent_events(limit=1000)), 240)


class TestSearchIndexConcurrency(unittest.TestCase):
    def test_concurrent_append_and_search(self):
        store = SQLiteGraphStore(tempfile.mktemp(suffix=".db"))
        search = HybridSearch(store, vector_index=VectorIndex(backend=make_backend("stub")))
        for i in range(10):
            k = ConceptKU(ku_id=f"CON_{i:08d}", name=f"概念{i}", summary="语义 内容 " * 3, domain="d")
            store.put(k); search.append_ku(k)
        stop = threading.Event()
        def appender(i):
            for j in range(15):
                if stop.is_set(): break
                k = ConceptKU(ku_id=f"NEW_{i}_{j:04d}", name=f"新概念{i}{j}",
                              summary="语义 内容", domain="d")
                store.put(k); search.append_ku(k)      # 摄入线程改索引
        def searcher(i):
            for _ in range(20):
                search.search("概念 语义", top_k=5)      # HTTP 线程读索引(faiss add/search 并发)
        errors = _run(lambda i: appender(i) if i % 2 == 0 else searcher(i), 8)
        stop.set()
        self.assertEqual(errors, [], f"并发 append+search 出错/崩溃: {errors[:2]}")


class TestDedupConcurrency(unittest.TestCase):
    def test_concurrent_same_fingerprint_dedups_to_one(self):
        store = SQLiteGraphStore(tempfile.mktemp(suffix=".db"))
        dd = KnowledgeDeduplicator(store)
        dd.rebuild_index()
        results = []
        rlock = threading.Lock()
        def w(i):
            # 全部同名同域 → 同一 canonical fingerprint
            k = ConceptKU(ku_id=f"CON_{i:08d}", name="同一概念", summary="x", domain="d")
            store.put(k)
            r = dd.check_and_merge(k)
            with rlock: results.append(r)
        errors = _run(w, 12)
        self.assertEqual(errors, [], f"并发去重出错: {errors[:2]}")
        # 加锁保证"查重+登记"原子:12 个同指纹里恰好 1 个被当新知识(None),其余都并入它
        nones = [r for r in results if r is None]
        self.assertEqual(len(nones), 1, f"应恰好 1 个新知识,实际 {len(nones)}(未加锁会漏去重)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
