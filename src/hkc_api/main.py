"""
hkc-api / main.py
HKC REST API 主应用。

启动（激活 venv 后统一用 python -m uvicorn）：
  python -m uvicorn hkc_api.main:app --host 127.0.0.1 --port 8000

环境变量：
  HKC_DATA_DIR       数据目录（默认 ./hkc_data）
  HKC_EMBEDDING      stub | local | tei（默认 stub）
  HKC_EMBEDDING_URL  TEI 服务地址（embedding=tei 时用）
  HKC_LLM_PROVIDER   anthropic | deepseek | openai | openai-compatible（默认 anthropic）
  HKC_LLM_API_KEY    通用 key；未设则按 provider 回退读 DEEPSEEK_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY
  HKC_LLM_MODEL / HKC_LLM_BASE_URL   模型名 / 自定义 API 地址（本地模型时）
  HKC_API_KEY        设了则请求需带 X-API-Key（可选鉴权）
"""
# TODO: Replace with proper package install before v1 release
import sys
import os

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .container import init_container, get_container, reset_container
from .schemas import StatsResponse
from .routes import knowledge, search, ability, config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hkc_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时装配容器，关闭时清理。"""
    # LLM provider:anthropic(默认)| deepseek | openai
    _provider = os.environ.get("HKC_LLM_PROVIDER", "anthropic").lower()
    # API key:优先 HKC_LLM_API_KEY,其次按 provider 读对应的 key
    _llm_key = (os.environ.get("HKC_LLM_API_KEY")
                or os.environ.get("DEEPSEEK_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or os.environ.get("ANTHROPIC_API_KEY"))
    init_container(
        data_dir       = os.environ.get("HKC_DATA_DIR", "./hkc_data"),
        embedding_kind = os.environ.get("HKC_EMBEDDING", "stub"),
        embedding_url  = os.environ.get("HKC_EMBEDDING_URL", "http://localhost:8080"),
        llm_api_key    = _llm_key,
        llm_model      = os.environ.get("HKC_LLM_MODEL"),     # None → 按 provider 默认
        llm_provider   = _provider,
        llm_base_url   = os.environ.get("HKC_LLM_BASE_URL"),  # None → provider 默认
    )
    logger.info("HKC API 启动完成")
    yield
    reset_container()
    logger.info("HKC API 已关闭")


app = FastAPI(
    title="Hestia Knowledge Core API",
    description="知识从存储进化成能力。KDE 摄入 → KEE 演化 → ACE 编译 → Search 检索。",
    version="1.0.0",
    lifespan=lifespan,
)

# 可选 API 鉴权:设了环境变量 HKC_API_KEY 才启用;未设=开放(本地默认,行为不变)。
# 在 CORS 之前注册 → CORS 处于更外层,即便鉴权返回 401 也带上 CORS 头,浏览器能读到。
_api_key = os.environ.get("HKC_API_KEY", "").strip()
if _api_key:
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    _OPEN_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc"}

    @app.middleware("http")
    async def _require_api_key(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in _OPEN_PATHS:
            return await call_next(request)
        if request.headers.get("x-api-key", "") != _api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "缺少或错误的 API Key（请求头需带 X-API-Key）"},
            )
        return await call_next(request)

# CORS:允许前端(hkc-ui)从浏览器调用。HKC 是本地知识工具,默认放开所有来源;
# 部署到受控网络时可用 HKC_CORS_ORIGINS(逗号分隔)收紧。
from fastapi.middleware.cors import CORSMiddleware
_origins = os.environ.get("HKC_CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins.split(",")] if _origins != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(knowledge.router)
app.include_router(search.router)
app.include_router(ability.router)
app.include_router(config.router)


@app.get("/")
def root():
    return {
        "name":    "Hestia Knowledge Core",
        "version": "1.0.0",
        "docs":    "/docs",
        "modules": ["KDE", "KEE", "ACE", "Search"],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats", response_model=StatsResponse, tags=["system"])
def stats():
    c = get_container()
    return StatsResponse(**c.stats())


@app.get("/events")
def recent_events(limit: int = 20):
    """查看最近的 KEP 事件（调试用）。"""
    c = get_container()
    return {"events": c.event_bus.recent_events(limit=limit)}


def main():
    import uvicorn
    uvicorn.run(
        app,
        host=os.environ.get("HKC_HOST", "127.0.0.1"),
        port=int(os.environ.get("HKC_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
