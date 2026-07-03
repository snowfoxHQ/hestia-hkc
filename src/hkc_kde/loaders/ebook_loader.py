"""
hkc-kde / loaders / ebook_loader.py
电子书解析器：支持 .epub / .mobi / .azw3

- epub：标准格式(zip + xhtml),用 ebooklib + BeautifulSoup 提取文本
- mobi / azw3：Amazon 格式,用 mobi 库解包为 html 后提取
  (azw3 即 KF8,mobi 库可处理)

所有格式最终转成纯文本,按 ParsedDocument 返回,后续 pipeline 与其它格式一致。
"""
from __future__ import annotations

import os
import uuid
import logging
import tempfile
import shutil
from pathlib import Path

from ..models import ParsedDocument, Chapter, Section

logger = logging.getLogger(__name__)

# 软依赖检测
try:
    import ebooklib
    from ebooklib import epub
    HAS_EBOOKLIB = True
except ImportError:
    HAS_EBOOKLIB = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    import mobi
    HAS_MOBI = True
except ImportError:
    HAS_MOBI = False


_MIN_SECTION_CHARS = 12   # 太短的片段(导航/版权页碎片)忽略;阈值偏低以兼容中文短段落


def _html_to_text(html: str) -> str:
    """从 HTML 提取纯文本。"""
    if HAS_BS4:
        soup = BeautifulSoup(html, "html.parser")
        # 去掉脚本/样式
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text("\n", strip=True)
    # 没有 bs4 时的兜底:粗暴去标签
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


class EbookLoader:
    """epub / mobi / azw3 加载器。"""

    def load(self, path: str) -> ParsedDocument:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        suffix = path.suffix.lower()
        if suffix == ".epub":
            title, blocks = self._load_epub(path)
        elif suffix in (".mobi", ".azw3", ".azw"):
            title, blocks = self._load_mobi(path)
        else:
            raise ValueError(f"EbookLoader 不支持的格式: {suffix}")

        # blocks: list[str] 纯文本段落 → 组装成单一章节的多个 Section
        sections = []
        for i, blk in enumerate(blocks):
            blk = blk.strip()
            if len(blk) < _MIN_SECTION_CHARS:
                continue
            sections.append(Section(section_id=f"SEC_{i:04d}", content=blk))

        chapter = Chapter(chapter_id="CH_0001", title=title or path.stem)
        chapter.sections = sections

        return ParsedDocument(
            doc_id   = f"DOC_{uuid.uuid4().hex[:8].upper()}",
            title    = title or path.stem,
            source   = str(path),
            doc_type = "EBOOK",
            chapters = [chapter],
            metadata = {
                "format":     suffix.lstrip("."),
                "file_size":  path.stat().st_size,
                "section_count": len(sections),
            },
        )

    def _load_epub(self, path: Path):
        if not HAS_EBOOKLIB:
            raise ImportError("解析 epub 需要 ebooklib，请运行: pip install ebooklib beautifulsoup4")
        book = epub.read_epub(str(path))
        # 标题
        title = ""
        try:
            md = book.get_metadata("DC", "title")
            if md:
                title = md[0][0]
        except Exception:
            pass
        # 按文档顺序提取每个 html 文档的文本
        blocks = []
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            try:
                html = item.get_content().decode("utf-8", errors="ignore")
                text = _html_to_text(html)
                if text:
                    blocks.append(text)
            except Exception as e:
                logger.warning("epub 文档解析失败: %s", e)
        return title, blocks

    def _load_mobi(self, path: Path):
        if not HAS_MOBI:
            raise ImportError("解析 mobi/azw3 需要 mobi 库，请运行: pip install mobi")
        tmp_dir = None
        try:
            # mobi.extract 返回 (tmp_dir, 解包出的主文件路径)
            tmp_dir, out_path = mobi.extract(str(path))
            text = ""
            op = Path(out_path)
            if op.suffix.lower() in (".html", ".htm", ".xhtml"):
                html = op.read_text(encoding="utf-8", errors="ignore")
                text = _html_to_text(html)
            elif op.suffix.lower() in (".txt", ""):
                text = op.read_text(encoding="utf-8", errors="ignore")
            else:
                # 兜底:扫描解包目录里的 (x)html。
                # KF8/azw3(MOBI8)会被解包成 epub 结构,正文是 .xhtml(out_path 为 .epub),
                # 仅扫 *.html 会漏掉全部正文 → 必须同时覆盖 .xhtml/.htm。
                # 排序保证 part0000/part0001… 的段落顺序稳定。
                for f in sorted(Path(tmp_dir).rglob("*")):
                    if f.suffix.lower() in (".html", ".htm", ".xhtml"):
                        text += _html_to_text(f.read_text(encoding="utf-8", errors="ignore")) + "\n\n"
            # 按段落切分(双换行)
            blocks = [b for b in text.split("\n\n")] if "\n\n" in text else [text]
            title = path.stem
            return title, blocks
        finally:
            if tmp_dir and os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)
