"""
hkc-kde / loaders / text_loader.py
Markdown 和纯文本 Loader。

Markdown：
- ## 标题 → 章节边界
- 段落（空行分隔）→ Section

纯文本：
- 空行分段，每 N 段合并为一个 Chapter
"""
import re
import uuid
from pathlib import Path

from ..models import ParsedDocument, Chapter, Section


class MarkdownLoader:

    def load(self, path: str) -> ParsedDocument:
        path = Path(path)
        content = path.read_text(encoding="utf-8", errors="replace")
        doc_id  = f"DOC_{uuid.uuid4().hex[:8].upper()}"
        chapters = self._parse_markdown(content)

        return ParsedDocument(
            doc_id   = doc_id,
            title    = self._extract_title(content, path.stem),
            source   = str(path),
            doc_type = "MARKDOWN",
            chapters = chapters,
        )

    def load_string(self, content: str, source: str = "inline") -> ParsedDocument:
        """直接从字符串加载，用于测试和管道传入。"""
        doc_id   = f"DOC_{uuid.uuid4().hex[:8].upper()}"
        chapters = self._parse_markdown(content)
        return ParsedDocument(
            doc_id   = doc_id,
            title    = self._extract_title(content, source),
            source   = source,
            doc_type = "MARKDOWN",
            chapters = chapters,
        )

    def _extract_title(self, content: str, fallback: str) -> str:
        """取第一个 # 标题，或文件名。"""
        m = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        return m.group(1).strip() if m else fallback

    def _parse_markdown(self, content: str) -> list[Chapter]:
        """
        按 ## 及以上标题切割章节，## 以下的段落归入当前章节。
        """
        chapters: list[Chapter] = []
        current_title    = ""
        current_paras:   list[str] = []
        ch_counter       = 0

        def _flush():
            nonlocal ch_counter
            if not current_paras:
                return
            ch_counter += 1
            secs = [
                Section(f"SEC_{ch_counter:04d}_{i:04d}", p.strip())
                for i, p in enumerate(current_paras)
                if p.strip()
            ]
            chapters.append(Chapter(
                chapter_id = f"CH_{ch_counter:04d}",
                title      = current_title,
                sections   = secs,
            ))

        # 按空行分段
        raw_paras = re.split(r'\n{2,}', content)

        for para in raw_paras:
            para = para.strip()
            if not para:
                continue
            # 检测是否是标题行（## 或更高级）
            m = re.match(r'^#{1,4}\s+(.+)$', para)
            if m:
                _flush()
                current_title = m.group(1).strip()
                current_paras = []
            else:
                # 去掉 Markdown 语法标记，保留纯文字
                clean = self._strip_md(para)
                if clean:
                    current_paras.append(clean)

        _flush()

        if not chapters:
            chapters = [Chapter("CH_0001", "", [Section("SEC_0001", content)])]

        return chapters

    def _strip_md(self, text: str) -> str:
        """去掉常见 Markdown 标记，保留纯文字内容。"""
        # 代码块
        text = re.sub(r'```[\s\S]*?```', '', text)
        text = re.sub(r'`[^`]+`', lambda m: m.group(0)[1:-1], text)
        # 链接 [text](url) → text
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        # 图片
        text = re.sub(r'!\[[^\]]*\]\([^\)]+\)', '', text)
        # 加粗 / 斜体
        text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
        text = re.sub(r'_{1,3}([^_]+)_{1,3}',   r'\1', text)
        # 引用块
        text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
        # 列表符号
        text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
        return text.strip()


class TxtLoader:

    PARAS_PER_CHAPTER = 10   # 每 10 个段落合并为一个虚拟章节

    def load(self, path: str) -> ParsedDocument:
        path    = Path(path)
        content = path.read_text(encoding="utf-8", errors="replace")
        doc_id  = f"DOC_{uuid.uuid4().hex[:8].upper()}"

        return ParsedDocument(
            doc_id   = doc_id,
            title    = path.stem,
            source   = str(path),
            doc_type = "TXT",
            chapters = self._parse_txt(content),
        )

    def _parse_txt(self, content: str) -> list[Chapter]:
        paras = [p.strip() for p in re.split(r'\n{2,}', content) if p.strip()]
        chapters: list[Chapter] = []

        for batch_start in range(0, len(paras), self.PARAS_PER_CHAPTER):
            batch = paras[batch_start: batch_start + self.PARAS_PER_CHAPTER]
            ch_num = len(chapters) + 1
            secs = [
                Section(f"SEC_{ch_num:04d}_{i:04d}", p)
                for i, p in enumerate(batch)
            ]
            chapters.append(Chapter(
                chapter_id = f"CH_{ch_num:04d}",
                title      = f"段落组 {ch_num}",
                sections   = secs,
            ))

        return chapters or [Chapter("CH_0001", "", [Section("SEC_0001", content)])]
