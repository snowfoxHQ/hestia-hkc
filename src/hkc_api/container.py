"""
hkc-api / container.py
应用容器：按依赖顺序装配 HKC 所有组件。

装配顺序（依赖从下到上）：
  1. IDGenerator       ← 无依赖
  2. GraphStore        ← 无依赖
  3. EventBus          ← 无依赖
  4. KEE               ← GraphStore + EventBus + IDGen（订阅 knowledge.created）
  5. ACE               ← GraphStore + EventBus + IDGen
  6. KDE               ← GraphStore + EventBus + IDGen
  7. HybridSearch      ← GraphStore（+ 可选向量后端）

容器是单例，整个 API 进程共享一套组件。
"""
# TODO: Replace with proper package install before v1 release

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class HKCContainer:
    """HKC 组件容器。一次装配，全程共享。"""

    def __init__(
        self,
        data_dir:        str = "./hkc_data",
        embedding_kind:  str = "stub",   # stub | local | tei
        embedding_url:   str = "http://localhost:8080",
        llm_api_key:     Optional[str] = None,
        llm_model:       Optional[str] = None,
        llm_provider:    str = "anthropic",
        llm_base_url:    Optional[str] = None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._embedding_kind = embedding_kind
        self._embedding_url  = embedding_url
        self._llm_api_key    = llm_api_key
        self._llm_model      = llm_model
        self._llm_provider   = llm_provider
        self._llm_base_url   = llm_base_url

        # 组件占位
        self.id_gen      = None
        self.graph_store = None
        self.event_bus   = None
        self.kee         = None
        self.ace         = None
        self.kde         = None
        self.search      = None
        self.ingest_jobs = None      # 异步摄入任务注册表(方案A)
        self.synthesis   = None      # 综合页 LLM 综述(派生只读视图,不进知识库)
        self.crystallizer = None     # Crystallizer 摄入适配器(集成层入口:外部推知识候选)

        self._assembled = False

    def assemble(self) -> "HKCContainer":
        """按依赖顺序装配所有组件。幂等。"""
        if self._assembled:
            return self

        from hkc_core.utils.id_gen import IDGenerator
        from hkc_core.graph.sqlite_store import SQLiteGraphStore
        from hkc_kep.event_bus import EventBus
        from hkc_kee.kee import KnowledgeEvolutionEngine
        from hkc_ace.ace import AbilityCompilerEngine
        from hkc_kde.kde import KnowledgeDigestEngine
        from hkc_search.hybrid import HybridSearch
        from hkc_search.vector_search import VectorIndex
        from hkc_search.embedding_backends import make_backend

        # 1. ID 生成器
        self.id_gen = IDGenerator(str(self.data_dir / "id_counters.db"))

        # 2. GraphStore
        self.graph_store = SQLiteGraphStore(str(self.data_dir / "hkc.db"))

        # 3. EventBus
        self.event_bus = EventBus(str(self.data_dir / "events.db"))

        # 4. KEE（构造时自动订阅 knowledge.created）
        self.kee = KnowledgeEvolutionEngine(
            self.graph_store, self.event_bus, self.id_gen
        )

        # 5. ACE
        self.ace = AbilityCompilerEngine(
            self.graph_store, self.event_bus, self.id_gen,
            output_dir=str(self.data_dir / "abilities"),
        )

        # 6. KDE
        self.kde = KnowledgeDigestEngine(
            self.graph_store, self.event_bus, self.id_gen,
            api_key=self._llm_api_key, model=self._llm_model,
            provider=self._llm_provider, base_url=self._llm_base_url,
        )

        # 7. HybridSearch（向量后端可配置）
        backend_kwargs = {}
        if self._embedding_kind == "tei":
            backend_kwargs["base_url"] = self._embedding_url
        backend = make_backend(self._embedding_kind, **backend_kwargs)
        vector_index = VectorIndex(backend=backend)
        self.search  = HybridSearch(self.graph_store, vector_index=vector_index)

        # 启动时构建索引（如果库里已有数据）
        try:
            self.search.build_index()
        except Exception as e:
            logger.warning("启动构建索引失败（可能库为空）: %s", e)

        # 新知识写入后自动更新搜索索引
        self._wire_search_index_updates()

        # 8. 异步摄入任务注册表(后台线程池执行文件摄入)
        from hkc_api.ingest_jobs import IngestJobRegistry
        self.ingest_jobs = IngestJobRegistry(max_workers=2)

        # 9. 综合页 LLM 综述服务(派生只读视图;缓存独立 synthesis.db,不进知识库,守 Principle 07)
        from hkc_api.synthesis import SynthesisService
        self.synthesis = SynthesisService(
            self.graph_store, self.search, self.kde,
            str(self.data_dir / "synthesis.db"),
        )

        # 10. Crystallizer 摄入适配器(集成层入口):外部系统经 POST /knowledge/crystallize
        #     推知识候选,走 HKCIngress → KDE → KEE。守边界:只产候选,不做知识身份判定。
        from hkc_crystallizer.ingress import HKCIngress
        self.crystallizer = HKCIngress(self.kde)

        self._embedding_name = backend.name   # 实际生效的向量后端名(供 /stats 展示)
        self._assembled = True
        logger.info("HKC 容器装配完成: data_dir=%s embedding=%s",
                    self.data_dir, backend.name)
        return self

    def _wire_search_index_updates(self):
        """订阅 knowledge.created，新 KU 写入后增量更新搜索索引。"""
        from hkc_kep.event_bus import KEPEvents

        def on_created(event: dict):
            ku_ids = event.get("payload", {}).get("ku_ids", [])
            for ku_id in ku_ids:
                ku = self.graph_store.get(ku_id)
                if ku:
                    try:
                        self.search.append_ku(ku)
                    except Exception as e:
                        logger.warning("索引增量更新失败 (%s): %s", ku_id, e)

        self.event_bus.subscribe(KEPEvents.KNOWLEDGE_CREATED, on_created)

    def stats(self) -> dict:
        """返回系统整体统计。"""
        if not self._assembled:
            return {"assembled": False}
        s = self.graph_store.stats()
        s["assembled"]      = True
        s["data_dir"]       = str(self.data_dir.resolve())   # 绝对路径,前端显示 + 排查"连错目录"
        s["embedding"]      = getattr(self, "_embedding_name", "")  # 生效的向量后端(stub 无语义)
        s["bm25_size"]      = self.search.bm25.size()
        s["vector_size"]    = self.search.vector.size()
        s["abilities"]      = self.ace.list_available()
        return s

    def close(self):
        """关闭资源。每次 reset_container(测试 tearDown / 服务热重载)都会调,
        必须把线程池和额外的 SQLite 连接一并释放,否则会累积线程/文件描述符泄漏。"""
        if self.ingest_jobs:
            try: self.ingest_jobs.shutdown()
            except Exception: pass
        if self.synthesis:
            try: self.synthesis.cache.close()
            except Exception: pass
        if self.graph_store:
            self.graph_store.close()


# ── 全局单例 ─────────────────────────────────────────────────

_container: Optional[HKCContainer] = None


def get_container() -> HKCContainer:
    """获取全局容器（FastAPI 依赖注入用）。"""
    global _container
    if _container is None:
        raise RuntimeError("容器未初始化，请先调用 init_container()")
    return _container


def init_container(**kwargs) -> HKCContainer:
    """初始化全局容器。"""
    global _container
    _container = HKCContainer(**kwargs).assemble()
    return _container


def reset_container():
    """重置全局容器（测试用）。"""
    global _container
    if _container:
        _container.close()
    _container = None
