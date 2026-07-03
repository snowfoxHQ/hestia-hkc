"""
hkc-api / routes / search.py
搜索路由：关键词 / 向量 / 图谱 / 混合检索。
"""
from fastapi import APIRouter, HTTPException, Depends

from ..container import HKCContainer, get_container
from ..schemas import (
    SearchRequest, SearchResponse, SearchHit, NeighborsRequest,
)

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResponse)
def search(
    req: SearchRequest,
    c: HKCContainer = Depends(get_container),
):
    """
    统一搜索接口。
    mode: bm25 | vector | graph | hybrid（默认）
    """
    if req.mode not in ("bm25", "vector", "graph", "hybrid"):
        raise HTTPException(
            status_code=400,
            detail=f"无效 mode: {req.mode}，支持 bm25/vector/graph/hybrid"
        )

    results = c.search.search(
        query    = req.query,
        mode     = req.mode,
        top_k    = req.top_k,
        domain   = req.domain,
        ku_types = req.ku_types,
    )
    return SearchResponse(
        query = req.query,
        mode  = req.mode,
        hits  = [
            SearchHit(
                ku_id=r.ku_id, score=r.score, mode=r.mode,
                name=r.name, summary=r.summary, ku_type=r.ku_type,
            )
            for r in results
        ],
    )


@router.post("/neighbors")
def search_neighbors(
    req: NeighborsRequest,
    c: HKCContainer = Depends(get_container),
):
    """从指定 KU 出发做图谱邻居展开。"""
    ku = c.graph_store.get(req.ku_id)
    if not ku:
        raise HTTPException(status_code=404, detail=f"KU 不存在: {req.ku_id}")

    results = c.search.search_neighbors(
        req.ku_id, max_depth=req.max_depth,
        top_k=req.top_k, rel_types=req.rel_types,
    )
    return {
        "ku_id": req.ku_id,
        "hits": [
            {"ku_id": r.ku_id, "score": r.score,
             "name": r.name, "summary": r.summary}
            for r in results
        ],
    }


@router.get("/path")
def find_path(
    from_id: str,
    to_id:   str,
    c: HKCContainer = Depends(get_container),
):
    """查找两个 KU 之间的最短路径。"""
    path = c.search.find_path(from_id, to_id)
    return {
        "from":      from_id,
        "to":        to_id,
        "path":      path,
        "reachable": len(path) > 0,
        "hops":      max(0, len(path) - 1),
    }
