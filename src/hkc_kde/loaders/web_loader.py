"""
hkc-kde / loaders / web_loader.py
网页 Loader，使用 trafilatura 提取正文。

策略：
- trafilatura 提取正文（自动去广告、导航等噪音）
- 提取到的文本交给 MarkdownLoader 的段落解析逻辑
- 保留原始 URL 作为 source
"""
import uuid
from urllib.parse import urlparse

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from ..models import ParsedDocument, Chapter, Section
from .text_loader import MarkdownLoader


class WebLoader:

    TIMEOUT = 15  # 秒

    def load(self, url: str) -> ParsedDocument:
        if not HAS_TRAFILATURA:
            raise ImportError("trafilatura 未安装，请运行: pip install trafilatura")
        if not HAS_REQUESTS:
            raise ImportError("requests 未安装，请运行: pip install requests")

        html    = self._fetch(url)
        text    = self._extract(html, url)
        doc_id  = f"DOC_{uuid.uuid4().hex[:8].upper()}"
        title   = self._extract_title(html, url)
        md_loader = MarkdownLoader()
        parsed    = md_loader.load_string(text, source=url)

        return ParsedDocument(
            doc_id   = doc_id,
            title    = title,
            source   = url,
            doc_type = "WEB",
            chapters = parsed.chapters,
            metadata = {"url": url, "domain": urlparse(url).netloc},
        )

    def load_html(self, html: str, url: str = "local") -> ParsedDocument:
        """直接从 HTML 字符串加载，用于测试。"""
        text    = self._extract(html, url)
        doc_id  = f"DOC_{uuid.uuid4().hex[:8].upper()}"
        title   = self._extract_title(html, url)
        parsed  = MarkdownLoader().load_string(text, source=url)
        return ParsedDocument(
            doc_id   = doc_id,
            title    = title,
            source   = url,
            doc_type = "WEB",
            chapters = parsed.chapters,
        )

    def _fetch(self, url: str) -> str:
        resp = _requests.get(url, timeout=self.TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (compatible; HKC-KDE/1.0)"
        })
        resp.raise_for_status()
        return resp.text

    def _extract(self, html: str, url: str) -> str:
        text = trafilatura.extract(
            html,
            url              = url,
            include_comments = False,
            include_tables   = True,
            no_fallback      = False,
        )
        return text or ""

    def _extract_title(self, html: str, fallback: str) -> str:
        import re
        m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return urlparse(fallback).path.split("/")[-1] or fallback
