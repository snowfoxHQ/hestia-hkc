"""
tests / test_kde.py
KDE 模块测试。

测试覆盖：
1. ParsedDocument 模型
2. MarkdownLoader / TxtLoader
3. Chunker（分块策略）
4. Extractor（JSON 解析、容错）
5. KUPackager（KU 写入、去重、事件）
6. KDE 全链路（mock Extractor，不调用真实 API）
"""
import sys, os, json, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hkc_core.graph.sqlite_store import SQLiteGraphStore
from hkc_core.utils.id_gen import IDGenerator
from hkc_kep.event_bus import EventBus, KEPEvents
from hkc_kde.models import ParsedDocument, Chapter, Section, RawExtraction, ExtractionResult
from hkc_kde.loaders.text_loader import MarkdownLoader, TxtLoader
from hkc_kde.chunker import Chunker, MAX_CHARS, MIN_CHARS
from hkc_kde.extractor import Extractor
from hkc_kde.packager import KUPackager
from hkc_kde.kde import KnowledgeDigestEngine


# ── 测试用 Mock Extractor ────────────────────────────────────

class MockExtractor(Extractor):
    """不调用 API，直接返回预设的提取结果。"""

    def __init__(self, preset: dict = None):
        self._preset = preset or {
            "facts":    [{"statement": "巴菲特生于1930年", "source_hint": "传记"}],
            "claims":   [{"statement": "价值投资长期有效", "confidence": 0.85, "domain": "Investment"}],
            "entities": [{"name": "Warren Buffett", "type": "Person", "aliases": ["巴菲特"]}],
            "concepts": [{"name": "Value Investing", "domain": "Investment", "definition": "基于内在价值的投资方法"}],
        }

    def extract(self, chunks) -> ExtractionResult:
        items = [
            RawExtraction(
                facts    = self._preset.get("facts", []),
                claims   = self._preset.get("claims", []),
                entities = self._preset.get("entities", []),
                concepts = self._preset.get("concepts", []),
                source_hint = "test",
            )
        ]
        doc_id = chunks[0].doc_id if chunks else "DOC_TEST"
        return ExtractionResult(doc_id=doc_id, source=doc_id, items=items)


def _make_store():
    return SQLiteGraphStore(tempfile.mktemp(suffix=".db"))

def _make_bus():
    return EventBus(":memory:")

def _make_id_gen():
    return IDGenerator(tempfile.mktemp(suffix=".db"))


# ─────────────────────────────────────────────────────────────
# 1. ParsedDocument 模型
# ─────────────────────────────────────────────────────────────

class TestParsedDocument(unittest.TestCase):

    def _make_doc(self):
        return ParsedDocument(
            doc_id   = "DOC_001",
            title    = "Test",
            source   = "test.md",
            doc_type = "MARKDOWN",
            chapters = [
                Chapter("CH_0001", "第一章", [
                    Section("SEC_0001", "第一节内容"),
                    Section("SEC_0002", "第二节内容"),
                ]),
                Chapter("CH_0002", "第二章", [
                    Section("SEC_0003", "第三节内容"),
                ]),
            ]
        )

    def test_all_sections(self):
        doc = self._make_doc()
        secs = doc.all_sections()
        self.assertEqual(len(secs), 3)

    def test_full_text(self):
        doc = self._make_doc()
        text = doc.full_text()
        self.assertIn("第一章", text)
        self.assertIn("第一节内容", text)
        self.assertIn("第三节内容", text)

    def test_extraction_result_helpers(self):
        er = ExtractionResult(
            doc_id = "D1",
            source = "s1",
            items  = [
                RawExtraction(facts=[{"statement": "f1"}], claims=[{"statement": "c1", "confidence": 0.8, "domain": ""}]),
                RawExtraction(entities=[{"name": "E1", "type": "Person", "aliases": []}]),
            ]
        )
        self.assertEqual(len(er.all_facts()), 1)
        self.assertEqual(len(er.all_claims()), 1)
        self.assertEqual(len(er.all_entities()), 1)
        self.assertEqual(len(er.all_concepts()), 0)


# ─────────────────────────────────────────────────────────────
# 2. Loaders
# ─────────────────────────────────────────────────────────────

class TestMarkdownLoader(unittest.TestCase):

    def test_basic_parse(self):
        md = """# 价值投资手册

## 第一章 基本原则

价值投资的核心是寻找被低估的资产。

格雷厄姆认为市场先生是情绪化的。

## 第二章 估值方法

DCF 是常用的估值工具。
"""
        doc = MarkdownLoader().load_string(md, source="test")
        self.assertEqual(doc.doc_type, "MARKDOWN")
        self.assertGreaterEqual(len(doc.chapters), 2)
        self.assertEqual(doc.title, "价值投资手册")

    def test_strips_markdown_syntax(self):
        md = "## 章节\n\n**重要概念**：这是 `代码` 和 [链接](http://example.com)。"
        doc = MarkdownLoader().load_string(md)
        text = doc.full_text()
        self.assertNotIn("**", text)
        self.assertNotIn("`代码`", text)
        self.assertNotIn("http://example.com", text)

    def test_empty_content(self):
        doc = MarkdownLoader().load_string("", source="empty")
        # 不崩溃即可
        self.assertIsNotNone(doc)

    def test_load_file(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".md", mode="w",
                                          encoding="utf-8", delete=False)
        tmp.write("# 测试文档\n\n这是测试内容。\n")
        tmp.close()
        doc = MarkdownLoader().load(tmp.name)
        self.assertEqual(doc.doc_type, "MARKDOWN")
        self.assertEqual(doc.title, "测试文档")
        os.unlink(tmp.name)


class TestTxtLoader(unittest.TestCase):

    def test_paragraph_grouping(self):
        content = "\n\n".join([f"段落{i}的内容，包含一些文字。" for i in range(25)])
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", mode="w",
                                          encoding="utf-8", delete=False)
        tmp.write(content)
        tmp.close()
        doc = TxtLoader().load(tmp.name)
        # 25 段 / 10 段每章 = 3 章
        self.assertEqual(len(doc.chapters), 3)
        os.unlink(tmp.name)


# ─────────────────────────────────────────────────────────────
# 3. Chunker
# ─────────────────────────────────────────────────────────────

class TestChunker(unittest.TestCase):

    def _make_doc(self, chapter_count=2, section_content=""):
        if not section_content:
            section_content = "这是一段测试内容。" * 10
        chapters = [
            Chapter(f"CH_{i:04d}", f"第{i}章", [
                Section(f"SEC_{i:04d}_0001", section_content)
            ])
            for i in range(1, chapter_count + 1)
        ]
        return ParsedDocument("DOC_TEST", "Test", "test.md", "MARKDOWN", chapters)

    def test_short_doc_one_chunk_per_chapter(self):
        doc = self._make_doc(2, "短内容" * 5)
        chunks = Chunker().chunk(doc)
        self.assertEqual(len(chunks), 2)

    def test_long_section_gets_split(self):
        long_content = "这是一段很长的内容。" * 200   # 远超 MAX_CHARS
        doc = self._make_doc(1, long_content)
        chunks = Chunker().chunk(doc)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.content), MAX_CHARS + 100)

    def test_chunk_has_source_hint(self):
        doc = self._make_doc(1, "内容" * 20)
        chunks = Chunker().chunk(doc)
        self.assertTrue(all(hasattr(c, 'source_hint') for c in chunks))

    def test_overlap_in_adjacent_chunks(self):
        """相邻 Chunk 应有重叠内容。"""
        long_content = "ABCDE " * 500
        doc = self._make_doc(1, long_content)
        chunks = Chunker().chunk(doc)
        if len(chunks) >= 2:
            # 后一个 Chunk 的开头应出现在前一个 Chunk 的末尾附近
            end_of_first   = chunks[0].content[-100:]
            start_of_second = chunks[1].content[:100]
            # 有公共词（重叠）
            words_a = set(end_of_first.split())
            words_b = set(start_of_second.split())
            self.assertTrue(len(words_a & words_b) > 0)

    def test_empty_doc(self):
        doc = ParsedDocument("D", "T", "s", "MD", [])
        chunks = Chunker().chunk(doc)
        self.assertEqual(chunks, [])


# ─────────────────────────────────────────────────────────────
# 4. Extractor（不调用 API，只测试解析逻辑）
# ─────────────────────────────────────────────────────────────

class TestExtractor(unittest.TestCase):

    def setUp(self):
        self.extractor = Extractor.__new__(Extractor)
        self.extractor.model   = "claude-sonnet-4-6"
        self.extractor.api_key = None
        self.extractor._client = None

    def test_parse_valid_json(self):
        raw = json.dumps({
            "facts":    [{"statement": "巴菲特生于1930年"}],
            "claims":   [{"statement": "价值投资有效", "confidence": 0.85, "domain": "Investment"}],
            "entities": [{"name": "巴菲特", "type": "Person", "aliases": ["Warren Buffett"]}],
            "concepts": [{"name": "价值投资", "domain": "Investment", "definition": "基于内在价值"}],
        })
        result = self.extractor._parse_response(raw, "test")
        self.assertIsNotNone(result)
        self.assertEqual(len(result.facts), 1)
        self.assertEqual(len(result.claims), 1)
        self.assertEqual(result.claims[0]["confidence"], 0.85)

    def test_strips_markdown_code_block(self):
        raw = "```json\n{\"facts\":[],\"claims\":[],\"entities\":[],\"concepts\":[]}\n```"
        result = self.extractor._parse_response(raw, "test")
        # 空结果返回 None
        self.assertIsNone(result)

    def test_invalid_json_returns_none(self):
        result = self.extractor._parse_response("this is not json", "test")
        self.assertIsNone(result)

    def test_confidence_clamped(self):
        raw = json.dumps({
            "facts": [], "entities": [], "concepts": [],
            "claims": [{"statement": "test claim", "confidence": 1.5, "domain": ""}],
        })
        result = self.extractor._parse_response(raw, "test")
        self.assertIsNotNone(result)
        self.assertLessEqual(result.claims[0]["confidence"], 1.0)

    def test_max_5_items_per_type(self):
        raw = json.dumps({
            "facts":    [{"statement": f"fact {i}"} for i in range(10)],
            "claims":   [],
            "entities": [],
            "concepts": [],
        })
        result = self.extractor._parse_response(raw, "test")
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result.facts), 5)

    def test_invalid_entity_type_defaults_to_person(self):
        raw = json.dumps({
            "facts": [], "claims": [], "concepts": [],
            "entities": [{"name": "GPT-4", "type": "InvalidType", "aliases": []}],
        })
        result = self.extractor._parse_response(raw, "test")
        self.assertIsNotNone(result)
        self.assertEqual(result.entities[0]["type"], "Person")


# ─────────────────────────────────────────────────────────────
# 5. KUPackager
# ─────────────────────────────────────────────────────────────

class TestKUPackager(unittest.TestCase):

    def setUp(self):
        self.store  = _make_store()
        self.bus    = _make_bus()
        self.id_gen = _make_id_gen()
        self.pkg    = KUPackager(self.store, self.bus, self.id_gen)

    def _make_result(self, facts=None, claims=None, entities=None, concepts=None):
        return ExtractionResult(
            doc_id = "DOC_001",
            source = "test.md",
            items  = [RawExtraction(
                facts    = facts    or [],
                claims   = claims   or [],
                entities = entities or [],
                concepts = concepts or [],
                source_hint = "test chapter",
            )]
        )

    def test_entities_written(self):
        result = self._make_result(
            entities=[{"name": "Charlie Munger", "type": "Person", "aliases": ["查理芒格"]}]
        )
        kus = self.pkg.package(result)
        entity_kus = [k for k in kus if k.ku_type.value == "Entity"]
        self.assertEqual(len(entity_kus), 1)
        self.assertEqual(entity_kus[0].name, "Charlie Munger")

    def test_claims_written_with_confidence(self):
        result = self._make_result(
            claims=[{"statement": "价值投资长期有效", "confidence": 0.85, "domain": "Investment"}]
        )
        kus = self.pkg.package(result)
        claim_kus = [k for k in kus if k.ku_type.value == "Claim"]
        self.assertEqual(len(claim_kus), 1)
        self.assertAlmostEqual(claim_kus[0].confidence, 0.85)

    def test_fact_confidence_always_1(self):
        result = self._make_result(
            facts=[{"statement": "巴菲特生于1930年", "source_hint": "传记"}]
        )
        kus = self.pkg.package(result)
        fact_kus = [k for k in kus if k.ku_type.value == "Fact"]
        self.assertEqual(len(fact_kus), 1)
        self.assertEqual(fact_kus[0].confidence, 1.0)

    def test_stores_source_text(self):
        """原文段落:抽取 dict 带 _src 时,Concept/Fact/Claim 应存下 source_text,
        出现在 to_dict.extra,且经 GraphStore 写入→重建后保真(供前端"看原文")。"""
        result = self._make_result(
            concepts=[{"name": "价值投资", "domain": "Investment",
                       "definition": "低于内在价值买入", "_src": "原文段落A:价值投资是一种…"}],
            facts=[{"statement": "巴菲特生于1930年", "_src": "原文段落B:巴菲特…"}],
            claims=[{"statement": "价值投资长期有效", "confidence": 0.8,
                     "domain": "Investment", "_src": "原文段落C:有观点认为…"}],
        )
        kus = self.pkg.package(result)
        by = {k.ku_type.value: k for k in kus}
        self.assertEqual(by["Concept"].source_text, "原文段落A:价值投资是一种…")
        self.assertEqual(by["Fact"].source_text,    "原文段落B:巴菲特…")
        self.assertEqual(by["Claim"].source_text,   "原文段落C:有观点认为…")
        self.assertIn("source_text", by["Concept"].to_dict()["extra"])
        # GraphStore 写入→重建保真
        got = self.store.get(by["Concept"].ku_id)
        self.assertEqual(got.source_text, "原文段落A:价值投资是一种…")

    def test_one_evidence_per_document(self):
        """一份文档(多个抽取 item/chunk)只产 1 个 Evidence 来源节点。
        回归:此前每 chunk 建一个 Evidence,一本书会切出成百上千个同名 Evidence,
        依赖 KEE 去重兜底,漏兜就满屏同名标签 + 线条爆炸。现在 packager 层面即只产 1 个。"""
        result = ExtractionResult(doc_id="DOC_X", source="书.md", items=[
            RawExtraction(facts=[{"statement": f"事实{i}"}], source_hint=f"章{i}")
            for i in range(6)
        ])
        kus = self.pkg.package(result, source_title="某本书")
        ev = [k for k in kus if k.ku_type.value == "Evidence"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].name, "某本书")

    def test_packager_does_not_dedup(self):
        """Principle 07:KDE 是纯 Producer,不做知识身份判定/去重。
        同名 Entity 第二次打包会再产出一个 KU —— 去重由 KEE 接管。
        全链路(KDE+KEE)去重见 tests/test_kee_dedup.py::TestKnowledgeDedup。"""
        result = self._make_result(
            entities=[{"name": "Charlie Munger", "type": "Person", "aliases": []}]
        )
        self.pkg.package(result)
        self.pkg.package(result)
        entity_count = len(self.store.query_by_type(
            __import__('hkc_core.models.enums', fromlist=['KUType']).KUType.ENTITY
        ))
        # packager 不旁路 KEE 自行去重;两次打包各产 1 个 Entity
        self.assertEqual(entity_count, 2)

    def test_knowledge_created_event_fired(self):
        events = []
        self.bus.subscribe(KEPEvents.KNOWLEDGE_CREATED, lambda e: events.append(e))
        result = self._make_result(
            entities=[{"name": "Test Entity", "type": "Person", "aliases": []}]
        )
        self.pkg.package(result)
        self.assertEqual(len(events), 1)
        self.assertIn("ku_ids", events[0]["payload"])

    def test_empty_extraction_no_kus(self):
        result = self._make_result()
        kus = self.pkg.package(result)
        self.assertEqual(kus, [])

    def test_stats_after_package(self):
        result = self._make_result(
            entities=[{"name": "E1", "type": "Person", "aliases": []}],
            concepts=[{"name": "C1", "domain": "Test", "definition": "d1"}],
            claims  =[{"statement": "Claim1", "confidence": 0.7, "domain": "Test"}],
            facts   =[{"statement": "Fact1", "source_hint": ""}],
        )
        self.pkg.package(result)
        stats = self.store.stats()
        self.assertGreaterEqual(stats["total_kus"], 4)


# ─────────────────────────────────────────────────────────────
# 6. KDE 全链路（Mock Extractor）
# ─────────────────────────────────────────────────────────────

class TestKDEPipeline(unittest.TestCase):

    def _make_kde(self, preset=None):
        store  = _make_store()
        bus    = _make_bus()
        id_gen = _make_id_gen()
        kde    = KnowledgeDigestEngine(store, bus, id_gen)
        kde.extractor = MockExtractor(preset)
        return kde, store, bus

    def test_ingest_text_produces_kus(self):
        kde, store, _ = self._make_kde()
        kus = kde.ingest_text(
            "这是关于价值投资的内容。巴菲特认为长期持有优质资产是最佳策略。",
            source       = "test",
            source_title = "价值投资笔记",
            domain       = "Investment",
        )
        self.assertGreater(len(kus), 0)

    def test_ingest_markdown_file(self):
        tmp = tempfile.NamedTemporaryFile(
            suffix=".md", mode="w", encoding="utf-8", delete=False
        )
        tmp.write("# 投资原则\n\n## 第一章\n\n巴菲特的核心理念是长期价值投资。\n")
        tmp.close()
        kde, store, _ = self._make_kde()
        try:
            kus = kde.ingest_file(tmp.name, domain="Investment")
            self.assertGreater(len(kus), 0)
        finally:
            os.unlink(tmp.name)

    def test_knowledge_created_event_on_ingest(self):
        kde, store, bus = self._make_kde()
        events = []
        bus.subscribe(KEPEvents.KNOWLEDGE_CREATED, lambda e: events.append(e))
        kde.ingest_text("测试内容", source="test")
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "KDE")

    def test_claim_domain_injected(self):
        """ingest 时指定 domain，应注入到 Claim 的 domain 字段。"""
        kde, store, _ = self._make_kde(preset={
            "facts":    [],
            "claims":   [{"statement": "测试观点", "confidence": 0.7, "domain": ""}],
            "entities": [],
            "concepts": [],
        })
        kus = kde.ingest_text("内容", domain="Psychology")
        from hkc_core.models.enums import KUType
        claims = [k for k in kus if k.ku_type == KUType.CLAIM]
        if claims:
            self.assertEqual(claims[0].domain, "Psychology")

    def test_kde_alone_does_not_dedup(self):
        """Principle 07 边界:单独的 KDE(无 KEE 订阅)不去重。
        同一知识摄入两次,KDE 各产 1 个 Entity;接上 KEE 后才会合并去重
        (端到端「摄入两次不重复」见 tests/test_kee_dedup.py::test_dedup_no_duplicate_ku)。"""
        kde, store, _ = self._make_kde(preset={
            "facts":    [],
            "claims":   [],
            "entities": [{"name": "UniqueEntity", "type": "Person", "aliases": []}],
            "concepts": [],
        })
        kde.ingest_text("内容一")
        kde.ingest_text("内容二")
        from hkc_core.models.enums import KUType
        entities = store.query_by_type(KUType.ENTITY)
        unique_names = [e.name for e in entities]
        # KDE 无 KEE 时不去重:两次摄入产出 2 个同名 Entity(去重是 KEE 的职责)
        self.assertEqual(unique_names.count("UniqueEntity"), 2)

    def test_unsupported_format_raises(self):
        kde, _, _ = self._make_kde()
        with self.assertRaises(ValueError):
            kde.ingest_file("/tmp/test.docx")


# ─────────────────────────────────────────────────────────────

class TestPDFLoader(unittest.TestCase):
    """PDF 加载器测试(回归:防止 load 后访问已关闭 doc 的 page_count)。"""

    def setUp(self):
        try:
            import fitz  # noqa
            self.fitz = fitz
        except ImportError:
            self.skipTest("PyMuPDF 未安装,跳过 PDF 测试")

    def _make_pdf(self, text):
        doc = self.fitz.open()
        doc.new_page().insert_text((72, 72), text)
        data = doc.tobytes()
        doc.close()
        path = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        path.write(data); path.close()
        return path.name

    def test_load_pdf_and_access_metadata(self):
        """load() 返回的 ParsedDocument 含 page_count(回归: 曾因 doc 提前 close 抛 document closed)。"""
        from hkc_kde.loaders.pdf_loader import PDFLoader
        path = self._make_pdf("Value Investing by Graham. Margin of safety is core.")
        parsed = PDFLoader().load(path)
        self.assertEqual(parsed.doc_type, "PDF")
        # 关键:访问 metadata 不应抛 "document closed"
        self.assertGreaterEqual(parsed.metadata["page_count"], 1)
        self.assertGreater(parsed.metadata["file_size"], 0)
        # 文本被提取出来
        all_text = " ".join(s.content for ch in parsed.chapters for s in ch.sections)
        self.assertIn("Graham", all_text)
        os.unlink(path)


class TestEbookLoader(unittest.TestCase):
    """电子书加载器测试(epub)。mobi/azw3 需真实文件,此处测 epub。"""

    def setUp(self):
        try:
            from ebooklib import epub  # noqa
            self.epub = epub
        except ImportError:
            self.skipTest("ebooklib 未安装,跳过 epub 测试")

    def test_load_epub(self):
        from hkc_kde.loaders.ebook_loader import EbookLoader
        book = self.epub.EpubBook()
        book.set_identifier("t1"); book.set_title("测试书"); book.set_language("zh")
        ch = self.epub.EpubHtml(title="第一章", file_name="c1.xhtml")
        ch.content = "<html><body><p>价值投资由格雷厄姆提出，核心是安全边际，巴菲特是实践者。</p></body></html>"
        book.add_item(ch); book.spine = ["nav", ch]
        book.add_item(self.epub.EpubNcx()); book.add_item(self.epub.EpubNav())
        path = tempfile.NamedTemporaryFile(suffix=".epub", delete=False).name
        self.epub.write_epub(path, book)

        parsed = EbookLoader().load(path)
        self.assertEqual(parsed.doc_type, "EBOOK")
        self.assertEqual(parsed.title, "测试书")
        text = " ".join(s.content for c in parsed.chapters for s in c.sections)
        self.assertIn("格雷厄姆", text)
        os.unlink(path)

    def test_load_azw3_kf8_xhtml_fallback(self):
        """KF8/azw3 变体回归:mobi.extract 把 azw3 解包成 epub 结构,正文是 .xhtml。
        _load_mobi 兜底分支必须扫描 .xhtml(此前只扫 *.html,导致 azw3 正文全空)。
        用 mock 模拟 KF8 解包目录,不依赖真实 azw3/Calibre。"""
        from unittest import mock
        from hkc_kde.loaders.ebook_loader import EbookLoader, HAS_MOBI
        if not HAS_MOBI:
            self.skipTest("mobi 库未安装")
        # 模拟 KF8(mobi8)解包:out_path 是 .epub,正文在 OEBPS/Text/*.xhtml
        extract_dir = tempfile.mkdtemp()
        text_dir = os.path.join(extract_dir, "mobi8", "OEBPS", "Text")
        os.makedirs(text_dir)
        with open(os.path.join(text_dir, "part0000.xhtml"), "w", encoding="utf-8") as f:
            f.write("<html><body><p>价值投资由格雷厄姆提出，强调以低于内在价值的价格买入。</p></body></html>")
        with open(os.path.join(text_dir, "part0001.xhtml"), "w", encoding="utf-8") as f:
            f.write("<html><body><p>安全边际是价值投资的核心原则，为判断失误留出缓冲空间。</p></body></html>")
        epub_out = os.path.join(extract_dir, "mobi8", "book.epub")
        open(epub_out, "w").close()

        azw3_path = tempfile.NamedTemporaryFile(suffix=".azw3", delete=False).name
        with mock.patch("hkc_kde.loaders.ebook_loader.mobi.extract",
                        return_value=(extract_dir, epub_out)):
            parsed = EbookLoader().load(azw3_path)
        text = " ".join(s.content for c in parsed.chapters for s in c.sections)
        self.assertIn("格雷厄姆", text)
        self.assertIn("安全边际", text)
        os.unlink(azw3_path)


class TestExtractorConcurrency(unittest.TestCase):
    """真 Extractor.extract 的并发路径:全部 chunk 都处理,进度回调跑满。"""

    def _chunks(self, n):
        return [type("C", (), {"doc_id": "D", "content": f"c{i}",
                               "source_hint": "", "chunk_id": f"C{i}"})()
                for i in range(n)]

    def test_concurrent_extract_processes_all_and_progress(self):
        from hkc_kde.extractor import Extractor
        ex = Extractor(provider="deepseek", model="x", api_key="x")
        calls = []
        def fake(ch):
            calls.append(ch.content)
            return RawExtraction(facts=[{"statement": ch.content}], source_hint="")
        ex._extract_chunk = fake      # 不真调 LLM
        seen = []
        res = ex.extract(self._chunks(20), progress_cb=lambda p, t: seen.append((p, t)))
        self.assertEqual(len(res.items), 20)   # 并发下全部 chunk 都处理
        self.assertEqual(len(calls), 20)
        self.assertEqual(len(seen), 20)        # 每块一次进度
        self.assertEqual(seen[-1], (20, 20))   # 进度跑满


class TestModelAgnostic(unittest.TestCase):
    """模型无关原则(CLAUDE.md §核心):任意 LLM 都能接入,具体 model 名只是可覆盖的 fallback。"""

    def test_default_model_per_provider_is_overridable_fallback(self):
        """未指定 model 时各 provider 有默认值,但这只是 fallback,不是硬绑定。"""
        from hkc_kde.extractor import Extractor
        self.assertEqual(Extractor(provider="deepseek").model, "deepseek-chat")
        self.assertEqual(Extractor(provider="openai").model, "gpt-4o-mini")
        self.assertEqual(Extractor(provider="openai-compatible").model, "gpt-4o-mini")
        self.assertEqual(Extractor(provider="anthropic").model, "claude-sonnet-4-6")

    def test_explicit_model_overrides_default(self):
        """自由文本 model 名(如本地 Ollama 模型)必须能覆盖默认。"""
        from hkc_kde.extractor import Extractor
        ex = Extractor(provider="openai-compatible", model="qwen2.5:7b",
                       base_url="http://localhost:11434/v1", api_key="x")
        self.assertEqual(ex.model, "qwen2.5:7b")

    def test_openai_compatible_client_points_at_custom_base_url(self):
        """openai-compatible + 自定义 base_url → 客户端指向本地/自定义端点,不连 openai.com。"""
        from hkc_kde.extractor import Extractor
        ex = Extractor(provider="openai-compatible", model="llama3",
                       base_url="http://localhost:11434/v1", api_key="ollama")
        self.assertIn("localhost:11434", str(ex._get_client().base_url))

    def test_reconfigure_switches_provider_and_endpoint_at_runtime(self):
        """运行时从一个 provider 切到另一个(界面「连接设置」走的路径),无需重启。"""
        from hkc_kde.extractor import Extractor
        ex = Extractor(provider="deepseek", api_key="x")
        ex.reconfigure(provider="openai-compatible", base_url="http://host:1234/v1",
                       model="mymodel", api_key="k")
        self.assertEqual(ex.provider, "openai-compatible")
        self.assertEqual(ex.model, "mymodel")
        self.assertIn("host:1234", str(ex._get_client().base_url))

    def test_openai_compatible_extract_parses_response_without_network(self):
        """端到端冒烟(不连网):mock 一个 OpenAI 兼容响应,确认抽取流程能正确解析出 KU。"""
        from hkc_kde.extractor import Extractor
        ex = Extractor(provider="openai-compatible", base_url="http://x/v1",
                       model="m", api_key="k")
        payload = ('{"facts":[{"statement":"地球绕太阳转"}],'
                   '"claims":[],"entities":[],"concepts":[]}')
        class _Msg:  content = payload
        class _Choice: message = _Msg()
        class _Resp: choices = [_Choice()]
        class _Completions:
            def create(self, **kw): return _Resp()
        class _Chat: completions = _Completions()
        class _Client: chat = _Chat()
        ex._client = _Client()          # 注入假客户端,不触发真实网络
        chunk = type("C", (), {"doc_id": "D", "content": "文本",
                               "source_hint": "", "chunk_id": "C1"})()
        raw = ex._extract_chunk(chunk)
        self.assertIsNotNone(raw)
        self.assertEqual(raw.facts[0]["statement"], "地球绕太阳转")


class TestChunkCap(unittest.TestCase):
    """超大文档 chunk 上限保护(防止整本书海量 LLM 调用)。"""

    def test_huge_doc_capped(self):
        from hkc_core.graph.sqlite_store import SQLiteGraphStore
        from hkc_core.utils.id_gen import IDGenerator
        from hkc_kep.event_bus import EventBus
        from hkc_kde.kde import KnowledgeDigestEngine, MAX_CHUNKS_PER_DOC
        from hkc_kde.extractor import Extractor

        seen = {"n": 0}
        def mock(self, chunks):
            seen["n"] = len(chunks)
            return ExtractionResult(doc_id="BIG", source="b", items=[])
        Extractor.extract = mock

        d = tempfile.mkdtemp()
        store = SQLiteGraphStore(os.path.join(d, "t.db"))
        idgen = IDGenerator(os.path.join(d, "ids.db"))
        kde = KnowledgeDigestEngine(store, EventBus(), idgen)

        doc = ParsedDocument(doc_id="BIG", title="big", source="big.epub", doc_type="EBOOK")
        ch = Chapter(chapter_id="C1", title="ch")
        ch.sections = [Section(section_id=f"S{i}", content="测试内容。" * 200) for i in range(200)]
        doc.chapters = [ch]
        kde._run_pipeline(doc, "big", 0, "X")
        # 处理的 chunk 数不应超过上限
        self.assertLessEqual(seen["n"], MAX_CHUNKS_PER_DOC)
        self.assertGreater(seen["n"], 0)


# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
