"""
hkc-search / embedding_backends.py
可插拔的 embedding 后端。

设计目标：把"如何生成向量"和"如何检索向量"解耦。
VectorIndex 只依赖 EmbeddingBackend 接口，不关心底层是本地模型还是远程服务。

三种后端：
  LocalSTBackend  : 进程内 sentence-transformers（需要模型权重在本地）
  TEIBackend      : 远程 HTTP 调用 Text Embeddings Inference 服务
  StubBackend     : 离线/测试用，返回确定性伪向量，不依赖任何模型

切换方式：
  backend = TEIBackend("http://localhost:8080")
  index   = VectorIndex(backend=backend)
"""
from __future__ import annotations
import hashlib
import logging
from abc import ABC, abstractmethod

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingBackend(ABC):
    """
    所有 embedding 后端的统一接口。
    encode() 必须返回 L2 归一化后的 float32 向量（shape: [n, dim]）。
    """

    @property
    @abstractmethod
    def dim(self) -> int:
        """向量维度。"""
        ...

    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray:
        """把文本列表编码为归一化向量矩阵。"""
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__


def _l2_normalize(vecs: np.ndarray) -> np.ndarray:
    """L2 归一化，使内积等价于余弦相似度。"""
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0   # 防止除零
    return (vecs / norms).astype(np.float32)


# ─────────────────────────────────────────────────────────────
# 1. 本地 sentence-transformers
# ─────────────────────────────────────────────────────────────

class LocalSTBackend(EmbeddingBackend):
    """
    进程内 sentence-transformers。
    需要模型权重已经在本地（首次会尝试从 HuggingFace 下载）。
    """

    def __init__(
        self,
        model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
        dim: int = 384,
    ):
        self._model_name = model_name
        self._dim        = dim
        self._model      = None   # 懒加载

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return f"LocalST({self._model_name})"

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ImportError(
                    "sentence-transformers 未安装: pip install sentence-transformers"
                )
            logger.info("加载本地 embedding 模型: %s", self._model_name)
            self._model = SentenceTransformer(self._model_name)
            # 用实际模型维度校正 dim
            actual_dim = self._model.get_sentence_embedding_dimension()
            if actual_dim and actual_dim != self._dim:
                logger.info("模型维度修正: %d → %d", self._dim, actual_dim)
                self._dim = actual_dim
        return self._model

    def encode(self, texts: list[str]) -> np.ndarray:
        model = self._get_model()
        vecs  = model.encode(
            texts,
            normalize_embeddings=True,   # ST 自己归一化
            show_progress_bar=False,
        )
        return vecs.astype(np.float32)


# ─────────────────────────────────────────────────────────────
# 2. TEI 远程服务
# ─────────────────────────────────────────────────────────────

class TEIBackend(EmbeddingBackend):
    """
    通过 HTTP 调用 Hugging Face Text Embeddings Inference 服务。

    TEI 启动方式（在有 GPU/网络的机器上）：
      docker run -p 8080:80 --gpus all \\
        ghcr.io/huggingface/text-embeddings-inference:1.x \\
        --model-id <model>

    API：POST /embed  body={"inputs": "text" | ["t1","t2"]}
         返回：[[float,...], ...]
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        dim: int = 384,
        timeout: int = 30,
        batch_size: int = 32,
    ):
        self._base_url   = base_url.rstrip("/")
        self._dim        = dim
        self._timeout    = timeout
        self._batch_size = batch_size
        self._dim_checked = False

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return f"TEI({self._base_url})"

    def health_check(self) -> bool:
        """检查 TEI 服务是否在线。"""
        try:
            import requests
            resp = requests.get(f"{self._base_url}/health", timeout=5)
            return resp.status_code == 200
        except Exception as e:
            logger.warning("TEI health check 失败: %s", e)
            return False

    def encode(self, texts: list[str]) -> np.ndarray:
        try:
            import requests
        except ImportError:
            raise ImportError("requests 未安装: pip install requests")

        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)

        all_vecs: list[list[float]] = []

        # 分批请求，避免单次 payload 过大
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i:i + self._batch_size]
            resp  = requests.post(
                f"{self._base_url}/embed",
                json={"inputs": batch},
                timeout=self._timeout,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            vecs = resp.json()   # 期望 [[...], [...]]

            # 校验返回格式：必须是 list，且元素是 list（向量）
            if not isinstance(vecs, list):
                raise ValueError(
                    f"TEI 返回非预期格式（{type(vecs).__name__}），"
                    f"可能是错误响应: {str(vecs)[:200]}"
                )
            # 单条输入若被 TEI 扁平化为 1D，包装回 2D
            if vecs and not isinstance(vecs[0], list):
                vecs = [vecs]
            all_vecs.extend(vecs)

        if not all_vecs:
            return np.zeros((0, self._dim), dtype=np.float32)

        arr = np.array(all_vecs, dtype=np.float32)

        # 保证二维（即使只有一条）
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)

        # 首次校正维度
        if not self._dim_checked and arr.ndim == 2:
            if arr.shape[1] != self._dim:
                logger.info("TEI 维度修正: %d → %d", self._dim, arr.shape[1])
                self._dim = arr.shape[1]
            self._dim_checked = True

        # TEI 通常已归一化，但保险起见再归一化一次
        return _l2_normalize(arr)


# ─────────────────────────────────────────────────────────────
# 3. Stub（离线 / 测试）
# ─────────────────────────────────────────────────────────────

class StubBackend(EmbeddingBackend):
    """
    确定性伪向量，不依赖任何模型或网络。
    用 hash 把文本映射到固定向量，相同文本得到相同向量。

    用途：
    - 离线测试索引、检索、持久化逻辑
    - CI 环境无法下载模型时的占位
    注意：伪向量没有语义，只能验证流程不能验证检索质量。
    """

    def __init__(self, dim: int = 384):
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return f"Stub(dim={self._dim})"

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = np.zeros((len(texts), self._dim), dtype=np.float32)
        for i, text in enumerate(texts):
            # 用文本 hash 作为随机种子，生成确定性向量
            seed = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)
            rng  = np.random.default_rng(seed)
            vecs[i] = rng.standard_normal(self._dim)
        return _l2_normalize(vecs)


# ─────────────────────────────────────────────────────────────
# 工厂函数
# ─────────────────────────────────────────────────────────────

def make_backend(
    kind: str = "auto",
    **kwargs,
) -> EmbeddingBackend:
    """
    根据 kind 创建后端。

    kind:
      "local" → LocalSTBackend
      "tei"   → TEIBackend
      "stub"  → StubBackend
      "auto"  → 优先 local，失败回退 stub
    """
    if kind == "local":
        return LocalSTBackend(**kwargs)
    if kind == "tei":
        return TEIBackend(**kwargs)
    if kind == "stub":
        return StubBackend(**kwargs)

    # auto：尝试 local，不可用则 stub
    backend = LocalSTBackend(**{k: v for k, v in kwargs.items()
                                if k in ("model_name", "dim")})
    try:
        backend._get_model()
        return backend
    except Exception as e:
        logger.warning("本地模型不可用（%s），回退到 StubBackend", e)
        return StubBackend(dim=kwargs.get("dim", 384))
