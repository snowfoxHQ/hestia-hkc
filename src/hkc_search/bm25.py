"""
hkc-search / bm25.py
关键词检索（Cold 层）。

使用 rank-bm25 的 BM25Okapi。
索引在内存中，GraphStore 里的全量 KU 启动时加载，
新 KU 写入后可增量追加（append_ku）。
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class BM25Result:
    ku_id:  str
    score:  float
    name:   str
    summary: str


def _tokenize(text: str) -> list[str]:
    """
    简单分词：英文按空格切，中文按字切，去停用词。
    v2 可替换为 jieba 等分词器。
    """
    # 英文小写、去标点
    text = text.lower()
    text = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', text)

    tokens: list[str] = []
    for word in text.split():
        # 英文词
        if re.match(r'^[a-z0-9_-]+$', word):
            if len(word) >= 2:
                tokens.append(word)
        else:
            # 中文：按字切（unigram），2 字滑动窗口（bigram）
            chars = [c for c in word if '\u4e00' <= c <= '\u9fff']
            tokens.extend(chars)
            for i in range(len(chars) - 1):
                tokens.append(chars[i] + chars[i + 1])

    return tokens


class BM25Index:
    """
    可增量更新的 BM25 索引。
    底层用 rank-bm25 的 BM25Okapi。
    """

    def __init__(self):
        self._ku_ids:   list[str]       = []
        self._names:    list[str]       = []
        self._summaries: list[str]      = []
        self._corpus:   list[list[str]] = []  # tokenized docs
        self._bm25 = None                     # 延迟初始化

    def build(self, kus: list) -> None:
        """从 KU 列表重建整个索引。"""
        self._ku_ids    = []
        self._names     = []
        self._summaries = []
        self._corpus    = []

        for ku in kus:
            self._add_to_corpus(ku)

        self._rebuild_bm25()
        logger.info("BM25 索引构建完成，共 %d 条", len(self._ku_ids))

    def append_ku(self, ku) -> None:
        """增量追加单个 KU，重建 BM25 对象（O(n) 但 v1 可接受）。"""
        self._add_to_corpus(ku)
        self._rebuild_bm25()

    def search(self, query: str, top_k: int = 10) -> list[BM25Result]:
        if not self._bm25 or not self._ku_ids:
            return []

        tokens = _tokenize(query)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)

        # 排序取 top_k
        indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        results = []
        for idx, score in indexed[:top_k]:
            if score == 0:
                continue
            results.append(BM25Result(
                ku_id   = self._ku_ids[idx],
                score   = float(score),
                name    = self._names[idx],
                summary = self._summaries[idx],
            ))
        return results

    def size(self) -> int:
        return len(self._ku_ids)

    # ── 内部方法 ─────────────────────────────────────────────

    def _add_to_corpus(self, ku) -> None:
        text   = f"{ku.name} {ku.summary} {' '.join(ku.tags)}"
        tokens = _tokenize(text)
        self._ku_ids.append(ku.ku_id)
        self._names.append(ku.name)
        self._summaries.append(ku.summary[:100])
        self._corpus.append(tokens)

    def _rebuild_bm25(self) -> None:
        try:
            from rank_bm25 import BM25Okapi
            self._bm25 = BM25Okapi(self._corpus)
        except ImportError:
            logger.error("rank-bm25 未安装，请运行: pip install rank-bm25")
            self._bm25 = None
