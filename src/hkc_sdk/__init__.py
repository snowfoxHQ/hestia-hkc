"""
hkc_sdk
Hestia Knowledge Core — Python SDK

统一入口 connect()，两种后端无感切换：

    from hkc_sdk import connect

    # 远程 HTTP（HKC 独立部署）
    hkc = connect("http://localhost:8000")

    # 进程内直连（HAR 与 HKC 同进程）
    from hkc_api.container import HKCContainer
    hkc = connect(container=HKCContainer(data_dir="./data"))

    # 用法完全一样
    hkc.ingest_text("巴菲特认为价值投资长期跑赢市场", domain="Investment")
    hits = hkc.search("价值投资")
    ability = hkc.ensure_ability("quant_analyst")
"""
from .base import BaseClient
from .http_client import HTTPClient
from .direct_client import DirectClient
from .models import (
    KU, SearchHit, Ability, Skill, CoverageReport, Conflict, IngestResult,
)
from .exceptions import (
    HKCError, HKCNotFoundError, HKCInsufficientCoverage,
    HKCBadRequest, HKCServerError,
)

__version__ = "1.0.0"

__all__ = [
    "connect",
    "BaseClient", "HTTPClient", "DirectClient",
    "KU", "SearchHit", "Ability", "Skill",
    "CoverageReport", "Conflict", "IngestResult",
    "HKCError", "HKCNotFoundError", "HKCInsufficientCoverage",
    "HKCBadRequest", "HKCServerError",
]


def connect(
    base_url: str = None,
    *,
    container=None,
    timeout: int = 60,
) -> BaseClient:
    """
    创建 HKC 客户端。

    二选一：
      connect("http://localhost:8000")   → HTTPClient
      connect(container=my_container)     → DirectClient

    两者都返回 BaseClient，方法签名一致。
    """
    if container is not None:
        return DirectClient(container)
    if base_url is not None:
        return HTTPClient(base_url, timeout=timeout)
    raise ValueError("必须提供 base_url（HTTP）或 container（直连）之一")
