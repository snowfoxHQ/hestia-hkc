"""
hkc-kde / kde.py
Knowledge Digest Engine 主引擎。

完整 Pipeline：
文件 / URL
  → Stage 1: Loader（解析原始格式）
  → Stage 2: Chunker（分块）+ Extractor（LLM 提取）
  → Stage 3: RelationshipDiscovery（关系发现）
  → Stage 4: Packager（写入 GraphStore，发布事件）
"""
# TODO: Replace with proper package install before v1 release

import logging
from pathlib import Path
from typing import Optional

from hkc_core.graph.base import GraphStore
from hkc_core.utils.id_gen import IDGenerator
from hkc_kep.event_bus import EventBus

from .models import ParsedDocument, ExtractionResult
from .chunker import Chunker, MAX_CHARS

# 单文档最多处理的 chunk 数(防止整本书切出上千 chunk 导致海量 LLM 调用)。
# 500 个 chunk 约覆盖 ~90 万字符,可较完整摄入整本书;超大书仍会截断并警告。
# 摄入已异步化(后台线程池),chunk 多只是后台跑得久,不卡 UI;主要代价是 LLM 调用费用。
MAX_CHUNKS_PER_DOC = 500
from .extractor import Extractor
from .packager import KUPackager
from .loaders.text_loader import MarkdownLoader, TxtLoader
from .loaders.web_loader import WebLoader

logger = logging.getLogger(__name__)


class KnowledgeDigestEngine:

    def __init__(
        self,
        graph_store: GraphStore,
        event_bus:   EventBus,
        id_gen:      IDGenerator,
        api_key:     Optional[str] = None,
        model:       Optional[str] = None,
        provider:    str           = "anthropic",
        base_url:    Optional[str] = None,
    ):
        self.store     = graph_store
        self.bus       = event_bus
        self.id_gen    = id_gen
        self.chunker   = Chunker()
        self.extractor = Extractor(api_key=api_key, model=model, provider=provider, base_url=base_url)
        self.packager  = KUPackager(graph_store, event_bus, id_gen)

    # ── 公开接口 ─────────────────────────────────────────────

    def ingest_file(
        self,
        path:         str,
        source_title: str = "",
        source_year:  int = 0,
        domain:       str = "",
        source:       str = "",
        progress_cb=None,
    ) -> list:
        """
        摄入本地文件。
        支持：.pdf / .md / .markdown / .txt
        返回写入的 KU 列表。

        source: 有意义的来源标识(如原始上传文件名)。留空时回退到文件路径。
                上传场景下 path 是临时盘路径,必须传 source 覆盖,否则
                Evidence 的来源会变成临时路径(可追溯链 KU←Evidence 失真)。
        progress_cb: 可选 (processed,total)->None,逐 chunk 上报抽取进度(异步摄入用)。
        """
        path   = Path(path)
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            doc = self._load_pdf(str(path))
        elif suffix in (".md", ".markdown"):
            doc = MarkdownLoader().load(str(path))
        elif suffix == ".txt":
            doc = TxtLoader().load(str(path))
        elif suffix in (".epub", ".mobi", ".azw3", ".azw"):
            from .loaders.ebook_loader import EbookLoader
            doc = EbookLoader().load(str(path))
        else:
            raise ValueError(f"不支持的文件格式: {suffix}，支持: .pdf .md .txt .epub .mobi .azw3")

        if source:
            doc.source = source   # 用调用方提供的来源标识覆盖临时盘路径

        return self._run_pipeline(
            doc,
            source_title = source_title or path.stem,
            source_year  = source_year,
            domain       = domain,
            progress_cb  = progress_cb,
        )

    def ingest_url(
        self,
        url:          str,
        source_title: str = "",
        source_year:  int = 0,
        domain:       str = "",
    ) -> list:
        """摄入网页 URL。"""
        doc = WebLoader().load(url)
        return self._run_pipeline(
            doc,
            source_title = source_title or url,
            source_year  = source_year,
            domain       = domain,
        )

    def ingest_text(
        self,
        text:         str,
        source:       str = "inline",
        source_title: str = "",
        source_year:  int = 0,
        domain:       str = "",
    ) -> list:
        """
        直接摄入文本字符串。
        适用于：已有文本内容、测试、管道传入。
        """
        doc = MarkdownLoader().load_string(text, source=source)
        return self._run_pipeline(
            doc,
            source_title = source_title or source,
            source_year  = source_year,
            domain       = domain,
        )

    # ── 内部流程 ─────────────────────────────────────────────

    def _run_pipeline(
        self,
        doc:          ParsedDocument,
        source_title: str,
        source_year:  int,
        domain:       str,
        progress_cb=None,
    ) -> list:
        logger.info(
            "KDE pipeline 开始: doc_id=%s title=%s sections=%d",
            doc.doc_id, doc.title,
            sum(len(ch.sections) for ch in doc.chapters),
        )

        # Stage 2a: Chunk
        chunks = self.chunker.chunk(doc)
        logger.info("Chunker: %d chunks", len(chunks))

        if not chunks:
            logger.warning("文档无有效内容: %s", doc.source)
            return []

        # 保护:超大文档(如整本电子书)会切出成百上千 chunk,每个都调一次 LLM,
        # 极慢且烧费用。超过上限时截断,只处理前 MAX_CHUNKS_PER_DOC 个,并警告。
        if len(chunks) > MAX_CHUNKS_PER_DOC:
            logger.warning(
                "文档过大:切出 %d 个 chunk,超过单文档上限 %d。仅处理前 %d 个"
                "(约 %d 字符)。如需完整摄入,请拆分文档。",
                len(chunks), MAX_CHUNKS_PER_DOC, MAX_CHUNKS_PER_DOC,
                MAX_CHUNKS_PER_DOC * MAX_CHARS,
            )
            chunks = chunks[:MAX_CHUNKS_PER_DOC]

        # Stage 2b: LLM 提取（有进度回调则带上；无则按旧签名调用,兼容 MockExtractor）
        if progress_cb is not None:
            result = self.extractor.extract(chunks, progress_cb=progress_cb)
        else:
            result = self.extractor.extract(chunks)
        logger.info(
            "Extractor: facts=%d claims=%d entities=%d concepts=%d",
            len(result.all_facts()), len(result.all_claims()),
            len(result.all_entities()), len(result.all_concepts()),
        )

        # 把 domain 注入 Claim（如果 LLM 没有填写 domain）
        if domain:
            for item in result.all_claims():
                if not item.get("domain"):
                    item["domain"] = domain

        # Stage 3 + 4: 关系发现 + 打包写入
        new_kus = self.packager.package(
            result,
            source_title = source_title,
            source_year  = source_year,
            source_id    = doc.source,   # 精确来源标识(如 memory:agent:finance:mem_001)
        )

        logger.info("KDE pipeline 完成: 写入 %d KU", len(new_kus))
        return new_kus

    def _load_pdf(self, path: str) -> ParsedDocument:
        try:
            from .loaders.pdf_loader import PDFLoader
            return PDFLoader().load(path)
        except ImportError:
            # PyMuPDF 未安装，降级为纯文本提取
            logger.warning("PyMuPDF 未安装，PDF 降级为文本提取")
            import subprocess, shlex
            safe_path = shlex.quote(path)
            result = subprocess.run(
                ["python3", "-c",
                 f"import fitz; d=fitz.open({safe_path}); "
                 "print(chr(10).join(p.get_text() for p in d))"],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                return MarkdownLoader().load_string(result.stdout, source=path)
            raise
