"""
hkc-api / synthesis.py
综合页「第 2 档 · LLM 综述」——把一个节点在图里的邻域，用 LLM 组织成连贯的中文词条。

★ Principle 07 边界（务必守住）：
  综述是**派生只读视图**，不是新知识。它——
    - 不建 KU、不写知识图、不走 KEE；
    - 缓存存在**独立的 synthesis.db**（物理上与 hkc.db 知识库分开，表明"这不是知识"）；
    - 只依据图里已有的知识材料重新组织表达，Prompt 明确禁止杜撰/引入外部知识。
  它与 3D 星球一样，只是知识图的一种渲染视图。
"""
from __future__ import annotations

import hashlib
import sqlite3
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


_SYS_PROMPT = """你是严谨的知识编辑。下面给你的是某个知识节点及其在知识库里的关联材料。
请把它们组织成一篇连贯、准确、可读的中文 wiki 式词条。

严格规则：
- 只依据给定材料，绝不杜撰、绝不引入材料之外的知识；材料没提到的就不写。
- 这是对**已有知识的重新组织表达**，不是创造新知识，也不要下与材料相悖的结论。
- 用简短小标题 + 段落；开头一句话定义/概述该节点，然后展开关联事实、观点与背景。
- 若材料稀少，就如实写简短几句，不要凑字数。
- 直接输出词条正文，不要说"根据材料"之类的元话术。"""


class SynthesisStore:
    """综述缓存（独立 SQLite，与知识库物理隔离）。"""

    def __init__(self, path: str):
        self.path = path
        # check_same_thread=False + 显式锁：FastAPI 同步路由跑在线程池里，
        # 并发请求会从多个线程共用这一个连接；不加锁会 "recursive use of cursors"/
        # "database is locked"。与 SQLiteGraphStore 的既有模式保持一致。
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._lock:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS synthesis (
                    ku_id        TEXT PRIMARY KEY,
                    content      TEXT NOT NULL,
                    model        TEXT,
                    generated_at TEXT,
                    context_hash TEXT
                )
            """)
            self._db.commit()

    def get(self, ku_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT ku_id, content, model, generated_at, context_hash "
                "FROM synthesis WHERE ku_id=?", (ku_id,)
            ).fetchone()
        if not row:
            return None
        return {"ku_id": row[0], "content": row[1], "model": row[2],
                "generated_at": row[3], "context_hash": row[4]}

    def put(self, ku_id: str, content: str, model: str, context_hash: str):
        with self._lock:
            self._db.execute(
                "INSERT INTO synthesis(ku_id, content, model, generated_at, context_hash) "
                "VALUES(?,?,?,?,?) ON CONFLICT(ku_id) DO UPDATE SET "
                "content=excluded.content, model=excluded.model, "
                "generated_at=excluded.generated_at, context_hash=excluded.context_hash",
                (ku_id, content, model,
                 datetime.now(timezone.utc).isoformat(), context_hash),
            )
            self._db.commit()

    def delete(self, ku_id: str):
        with self._lock:
            self._db.execute("DELETE FROM synthesis WHERE ku_id=?", (ku_id,))
            self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()


class SynthesisService:
    """按需生成 + 缓存节点综述。生成走已配置的任意 LLM（模型无关）。"""

    def __init__(self, graph_store, search, kde, cache_path: str):
        self.store  = graph_store
        self.search = search
        self.kde    = kde          # 动态取 kde.extractor,尊重运行时换/重配 LLM
        self.cache  = SynthesisStore(cache_path)

    @property
    def extractor(self):
        return self.kde.extractor

    # ── 邻域材料收集（复用图邻居 + 检索"提及"，均有界）──────────
    def _gather_context(self, ku) -> tuple[str, str]:
        """返回 (拼好的材料文本, 材料指纹)。材料只读，不改动任何知识。"""
        ex = ku.to_dict().get("extra", {})
        lines: list[str] = []
        head = f"节点：{ku.name}（{ku.ku_type.value}）"
        if ku.domain:
            head += f"　领域：{ku.domain}"
        lines.append(head)
        stmt = ex.get("statement")
        if stmt:
            lines.append(f"陈述：{stmt}")
        if ku.summary and ku.summary != stmt:
            lines.append(f"概述：{ku.summary}")
        src = (ex.get("source_text") or "").strip()
        if src:
            lines.append(f"原文片段：{src[:800]}")

        seen = {ku.ku_id}
        related: list[str] = []
        # 1) 图谱邻居（有直接关系的节点）
        try:
            for h in self.search.search_neighbors(ku.ku_id, max_depth=2, top_k=15):
                if h.ku_id in seen:
                    continue
                seen.add(h.ku_id)
                related.append(f"- {h.name}：{(h.summary or '').strip()[:160]}")
        except Exception as e:
            logger.warning("综述邻居收集失败 %s: %s", ku.ku_id, e)
        # 2) 检索"提及本节点名字"的事实/观点（图里常只连到书、不直接连实体）
        try:
            for h in self.search.search(ku.name, top_k=12):
                if h.ku_id in seen:
                    continue
                seen.add(h.ku_id)
                related.append(f"- {h.name}：{(h.summary or '').strip()[:160]}")
        except Exception as e:
            logger.warning("综述检索收集失败 %s: %s", ku.ku_id, e)

        if related:
            lines.append("\n关联材料：")
            lines.extend(related[:24])          # 上界，控 prompt 体积

        text = "\n".join(lines)
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        return text, digest

    def get_cached(self, ku_id: str) -> dict | None:
        return self.cache.get(ku_id)

    def generate(self, ku_id: str, force: bool = False) -> dict:
        """
        返回 {ku_id, content, model, generated_at, cached, stale}。
        - 有缓存且非 force：直接返回缓存（stale 标记材料是否已变，供前端提示"可重新生成"）。
        - force 或无缓存：调 LLM 生成并写缓存。
        LLM 未配置/调用失败会抛异常，由路由转成明确错误。
        """
        ku = self.store.get(ku_id)
        if not ku:
            raise KeyError(ku_id)

        context, digest = self._gather_context(ku)
        cached = self.cache.get(ku_id)
        if cached and not force:
            cached["cached"] = True
            cached["stale"]  = (cached.get("context_hash") != digest)
            return cached

        content = self.extractor.complete(
            system=_SYS_PROMPT,
            user=context,
            max_tokens=1500,
        )
        model = getattr(self.extractor, "model", "")
        self.cache.put(ku_id, content, model, digest)
        return {"ku_id": ku_id, "content": content, "model": model,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "context_hash": digest, "cached": False, "stale": False}
