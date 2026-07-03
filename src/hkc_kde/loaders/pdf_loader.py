"""
hkc-kde / loaders / pdf_loader.py
PDF 解析器，使用 PyMuPDF（fitz）。

策略：
- 按页提取文字
- 检测标题（字体大小 > 阈值）作为章节边界
- 表格识别（简单规则：连续短行 + 数字占比高）
"""
import re
import uuid
from pathlib import Path

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

from ..models import ParsedDocument, Chapter, Section


# 标题字体大小阈值（相对于正文字体）
_TITLE_SIZE_RATIO = 1.2


class PDFLoader:

    def load(self, path: str) -> ParsedDocument:
        if not HAS_FITZ:
            raise ImportError("PyMuPDF 未安装，请运行: pip install pymupdf")

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        doc = fitz.open(str(path))
        # try/finally:解析(坏 PDF)抛异常时也要 close,否则泄漏 PyMuPDF 原生句柄。
        try:
            doc_id  = f"DOC_{uuid.uuid4().hex[:8].upper()}"
            title   = self._extract_title(doc, path.stem)
            chapters = self._parse_chapters(doc)
            page_count = doc.page_count if hasattr(doc, 'page_count') else 0  # 必须在 close 前取
        finally:
            doc.close()

        return ParsedDocument(
            doc_id   = doc_id,
            title    = title,
            source   = str(path),
            doc_type = "PDF",
            chapters = chapters,
            metadata = {
                "page_count": page_count,
                "file_size":  path.stat().st_size,
            },
        )

    def _extract_title(self, doc: "fitz.Document", fallback: str) -> str:
        """尝试从第一页提取标题，失败则用文件名。"""
        try:
            meta = doc.metadata
            if meta.get("title"):
                return meta["title"].strip()
            # 取第一页最大字体的文字
            page = doc[0]
            blocks = page.get_text("dict")["blocks"]
            max_size = 0
            title_text = fallback
            for block in blocks:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span["size"] > max_size and len(span["text"].strip()) > 3:
                            max_size = span["size"]
                            title_text = span["text"].strip()
            return title_text
        except Exception:
            return fallback

    def _parse_chapters(self, doc: "fitz.Document") -> list[Chapter]:
        """
        按页遍历，检测标题行作为章节边界。
        同一页内的段落归入当前章节。
        """
        chapters: list[Chapter] = []
        current_chapter: Chapter | None = None
        current_sections: list[Section] = []
        body_font_size = self._detect_body_font(doc)

        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("dict")["blocks"]

            for block in blocks:
                block_text = ""
                is_title   = False
                lines = block.get("lines", [])

                for line in lines:
                    line_text = ""
                    for span in line.get("spans", []):
                        text = span["text"].strip()
                        if not text:
                            continue
                        # 字体大于正文 1.2 倍 → 判定为标题
                        if span["size"] >= body_font_size * _TITLE_SIZE_RATIO:
                            is_title = True
                        line_text += text + " "
                    block_text += line_text.strip() + "\n"

                block_text = block_text.strip()
                if not block_text:
                    continue

                if is_title and len(block_text) < 120:
                    # 保存上一章节
                    if current_chapter is not None:
                        current_chapter.sections = current_sections
                        chapters.append(current_chapter)
                    ch_id = f"CH_{len(chapters)+1:04d}"
                    current_chapter  = Chapter(chapter_id=ch_id, title=block_text)
                    current_sections = []
                else:
                    # 普通段落
                    if current_chapter is None:
                        current_chapter  = Chapter(chapter_id="CH_0000", title="")
                        current_sections = []
                    sec_id = f"SEC_{page_num:04d}_{len(current_sections):04d}"
                    current_sections.append(Section(
                        section_id = sec_id,
                        content    = block_text,
                    ))

        # 收尾
        if current_chapter is not None:
            current_chapter.sections = current_sections
            chapters.append(current_chapter)

        return chapters if chapters else [
            Chapter(
                chapter_id = "CH_0001",
                title      = "",
                sections   = [Section("SEC_0000", doc[0].get_text() if len(doc) else "")]
            )
        ]

    def _detect_body_font(self, doc: "fitz.Document") -> float:
        """
        统计全文字体大小分布，取众数作为正文字体。
        取前3页采样，速度快且足够准确。
        """
        from collections import Counter
        sizes: list[float] = []
        sample_pages = min(3, len(doc))
        for i in range(sample_pages):
            for block in doc[i].get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span["text"].strip():
                            sizes.append(round(span["size"], 1))
        if not sizes:
            return 12.0
        return Counter(sizes).most_common(1)[0][0]
