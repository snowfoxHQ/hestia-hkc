"""
hkc_sdk / exceptions.py
SDK 异常体系。

让用户能用一致的方式捕获错误，不管底层是 HTTP 还是直连：
  HKCError                  基类
  ├── HKCNotFoundError      资源不存在（404）
  ├── HKCInsufficientCoverage  能力覆盖度不足（422）
  ├── HKCBadRequest         请求参数错误（400 / 413）
  └── HKCServerError        服务内部错误（500 / 连接失败）
"""


class HKCError(Exception):
    """所有 SDK 异常的基类。"""
    def __init__(self, message: str, detail=None):
        super().__init__(message)
        self.message = message
        self.detail  = detail


class HKCNotFoundError(HKCError):
    """请求的资源不存在（KU / Ability / Conflict）。"""


class HKCInsufficientCoverage(HKCError):
    """编译能力时知识覆盖度不足。detail 含 missing_skills。"""
    def __init__(self, message: str, detail=None):
        super().__init__(message, detail)
        self.missing_skills = (detail or {}).get("missing_skills", [])
        self.coverage       = (detail or {}).get("coverage", {})


class HKCBadRequest(HKCError):
    """请求参数错误或请求体过大。"""


class HKCServerError(HKCError):
    """服务内部错误或连接失败。"""
