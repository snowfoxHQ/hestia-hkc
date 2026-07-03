"""
hkc-kde / chunker.py
分块器：把 ParsedDocument 切成适合 LLM 提取的 Chunk。

规则（写死，不允许各 loader 自定义）：
- 优先在章节边界切割
- 单个 Section 超过 MAX_TOKENS → 滑动窗口切割
- 单个 Section 不足 MIN_TOKENS → 与下一个 Section 合并
- 相邻 Chunk 之间保留 OVERLAP_TOKENS 重叠，保持上下文连续
"""
from __future__ import annotations
from dataclasses import dataclass

from .models import ParsedDocument, Section


# ── 配置（统一常量，禁止在其他地方覆盖）────────────────────

MAX_TOKENS     = 512    # 单个 Chunk 最大 token 数
MIN_TOKENS     = 128    # 低于此长度不单独成块，与下一个合并
OVERLAP_TOKENS = 64     # 相邻 Chunk 重叠 token 数

# 粗略估算：1 token ≈ 4 个英文字符 / 1.5 个中文字符
# 用字符数近似，避免引入 tokenizer 依赖
CHARS_PER_TOKEN = 3.5


def _char_limit(tokens: int) -> int:
    return int(tokens * CHARS_PER_TOKEN)


MAX_CHARS     = _char_limit(MAX_TOKENS)
MIN_CHARS     = _char_limit(MIN_TOKENS)
OVERLAP_CHARS = _char_limit(OVERLAP_TOKENS)


@dataclass
class Chunk:
    chunk_id:     str
    content:      str
    source_hint:  str   # 章节标题，用于 Evidence 溯源
    doc_id:       str
    char_count:   int   = 0

    def __post_init__(self):
        self.char_count = len(self.content)


class Chunker:

    def chunk(self, doc: ParsedDocument) -> list[Chunk]:
        """
        主入口：把 ParsedDocument 切成 Chunk 列表。
        章节边界优先，章节内再按 MAX_CHARS 切割。
        """
        chunks:    list[Chunk]  = []
        chunk_idx: int          = 0

        for chapter in doc.chapters:
            chapter_chunks = self._chunk_chapter(
                chapter_title = chapter.title,
                sections      = chapter.sections,
                doc_id        = doc.doc_id,
                start_idx     = chunk_idx,
            )
            chunks.extend(chapter_chunks)
            chunk_idx += len(chapter_chunks)

        return chunks

    def _chunk_chapter(
        self,
        chapter_title: str,
        sections:      list[Section],
        doc_id:        str,
        start_idx:     int,
    ) -> list[Chunk]:
        """
        章节内部的分块逻辑：
        1. 先把 sections 合并成连续文本
        2. 如果总长 <= MAX_CHARS → 一个 Chunk
        3. 否则滑动窗口切割，相邻 Chunk 保留 OVERLAP_CHARS 重叠
        """
        # 合并 sections 文本（短 section 先合并）
        merged_text = self._merge_sections(sections)
        if not merged_text.strip():
            return []

        if len(merged_text) <= MAX_CHARS:
            # 整章一个 Chunk
            return [Chunk(
                chunk_id    = f"{doc_id}_C{start_idx:04d}",
                content     = merged_text,
                source_hint = chapter_title,
                doc_id      = doc_id,
            )]

        # 滑动窗口切割
        return self._sliding_window(
            text          = merged_text,
            source_hint   = chapter_title,
            doc_id        = doc_id,
            start_idx     = start_idx,
        )

    def _merge_sections(self, sections: list[Section]) -> str:
        """
        把 sections 合并成文本。
        短 Section（< MIN_CHARS）不单独成块，与相邻合并。
        """
        if not sections:
            return ""

        parts: list[str] = []
        buffer: str      = ""

        for sec in sections:
            text = sec.content.strip()
            if not text:
                continue
            if len(buffer) + len(text) < MIN_CHARS:
                # buffer 太短，继续累积
                buffer = (buffer + "\n\n" + text).strip()
            else:
                if buffer:
                    parts.append(buffer)
                buffer = text

        if buffer:
            parts.append(buffer)

        return "\n\n".join(parts)

    def _sliding_window(
        self,
        text:        str,
        source_hint: str,
        doc_id:      str,
        start_idx:   int,
    ) -> list[Chunk]:
        """
        固定步长滑动窗口，步长 = MAX_CHARS - OVERLAP_CHARS。
        在句子边界（。！？\n）对齐，避免在句中切断。
        """
        step   = MAX_CHARS - OVERLAP_CHARS
        chunks: list[Chunk] = []
        pos    = 0

        while pos < len(text):
            end = pos + MAX_CHARS
            if end < len(text):
                # 向后找最近的句子边界
                boundary = self._find_boundary(text, end)
                end      = boundary if boundary > pos else end

            fragment = text[pos:end].strip()
            if fragment:
                chunks.append(Chunk(
                    chunk_id    = f"{doc_id}_C{start_idx + len(chunks):04d}",
                    content     = fragment,
                    source_hint = source_hint,
                    doc_id      = doc_id,
                ))

            # 下一个窗口起点 = 当前终点 - 重叠
            pos = max(pos + step, end - OVERLAP_CHARS)
            if pos >= len(text):
                break

        return chunks

    def _find_boundary(self, text: str, pos: int) -> int:
        """
        从 pos 向前扫描，找最近的句子边界字符。
        优先级：\n\n > 。！？\n > 。！？ > 空格
        最多向前扫描 100 字符。
        """
        search_start = max(0, pos - 100)
        segment = text[search_start:pos]

        # 优先双换行（段落边界）
        idx = segment.rfind('\n\n')
        if idx != -1:
            return search_start + idx + 2

        # 中文句号等
        for ch in ('。', '！', '？', '!', '?', '\n'):
            idx = segment.rfind(ch)
            if idx != -1:
                return search_start + idx + 1

        # 空格
        idx = segment.rfind(' ')
        if idx != -1:
            return search_start + idx + 1

        return pos
