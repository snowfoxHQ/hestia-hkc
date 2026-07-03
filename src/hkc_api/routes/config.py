"""
运行时 LLM 配置路由。
让前端「连接设置」里能选择 LLM 提供商、填 key/model/base_url,
无需改环境变量或重启后端。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from ..container import HKCContainer, get_container

router = APIRouter(prefix="/config", tags=["config"])


class LLMConfigRequest(BaseModel):
    provider: str                      # anthropic | deepseek | openai | openai-compatible
    api_key:  Optional[str] = None
    model:    Optional[str] = None     # 留空用 provider 默认
    base_url: Optional[str] = None     # 自定义/本地模型时


@router.get("/llm")
def get_llm_config(c: HKCContainer = Depends(get_container)):
    """读取当前 LLM 配置(key 脱敏)。"""
    try:
        return c.kde.extractor.config_summary()
    except Exception as e:
        raise HTTPException(500, f"读取配置失败: {e}")


@router.post("/llm")
def set_llm_config(req: LLMConfigRequest, c: HKCContainer = Depends(get_container)):
    """运行时设置 LLM 配置。立即生效,无需重启。"""
    provider = (req.provider or "").lower()
    valid = {"anthropic", "deepseek", "openai", "openai-compatible"}
    if provider not in valid:
        raise HTTPException(400, f"不支持的 provider: {req.provider}(可选: {', '.join(sorted(valid))})")
    # openai-compatible(本地/自定义)必须提供 base_url,否则会误连 openai.com
    if provider == "openai-compatible" and not (req.base_url or "").strip():
        raise HTTPException(400, "自定义/本地模型(openai-compatible)必须填写 API 地址(base_url)")
    try:
        c.kde.extractor.reconfigure(
            provider=provider,
            api_key=req.api_key,
            model=req.model,
            base_url=req.base_url,
        )
        return {"ok": True, "config": c.kde.extractor.config_summary()}
    except Exception as e:
        raise HTTPException(500, f"设置配置失败: {e}")
