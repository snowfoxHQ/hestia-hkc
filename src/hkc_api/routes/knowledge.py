"""
hkc-api / routes / knowledge.py
知识摄入与 KU 查询路由。
"""
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form

from ..container import HKCContainer, get_container
from ..schemas import (
    IngestTextRequest, IngestURLRequest, IngestResponse, KUResponse,
    GraphResponse, CrystallizeRequest,
)
import os
import tempfile

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# 单次摄入文本上限（约 2MB UTF-8），防止 OOM
MAX_INGEST_CHARS = 2_000_000


def _ku_to_response(ku, *, include_source_text: bool = True) -> KUResponse:
    extra = ku.to_dict().get("extra", {})
    # 星球全量拉取(/graph)时剔除 source_text:原文段落占整个图谱负载的 ~92%,
    # 而渲染节点根本用不到它,只有点开单个节点看详情才需要 → 那时按 /ku/{id} 单独拉全量。
    if not include_source_text and "source_text" in extra:
        extra = {k: v for k, v in extra.items() if k != "source_text"}
    return KUResponse(
        ku_id      = ku.ku_id,
        ku_type    = ku.ku_type.value,
        name       = ku.name,
        summary    = ku.summary,
        domain     = ku.domain,
        confidence = ku.confidence,
        status     = ku.status,
        tags       = ku.tags,
        extra      = extra,
    )


def _ingest_response(kus) -> IngestResponse:
    counts = {}
    for ku in kus:
        t = ku.ku_type.value
        counts[t] = counts.get(t, 0) + 1
    return IngestResponse(
        ku_count = len(kus),
        ku_ids   = [ku.ku_id for ku in kus],
        counts   = counts,
    )


@router.post("/ingest/text", response_model=IngestResponse)
def ingest_text(
    req: IngestTextRequest,
    c: HKCContainer = Depends(get_container),
):
    """摄入文本字符串，触发完整 KDE pipeline。"""
    # 请求体大小限制：防止超大文本 OOM
    if len(req.text) > MAX_INGEST_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"文本过大（{len(req.text)} 字符），上限 {MAX_INGEST_CHARS}"
        )
    try:
        kus = c.kde.ingest_text(
            text         = req.text,
            source       = req.source,
            source_title = req.source_title,
            source_year  = req.source_year,
            domain       = req.domain,
        )
        return _ingest_response(kus)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"摄入失败: {e}")


@router.post("/ingest/url", response_model=IngestResponse)
def ingest_url(
    req: IngestURLRequest,
    c: HKCContainer = Depends(get_container),
):
    """摄入网页 URL。"""
    try:
        kus = c.kde.ingest_url(
            url          = req.url,
            source_title = req.source_title,
            source_year  = req.source_year,
            domain       = req.domain,
        )
        return _ingest_response(kus)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"URL 摄入失败: {e}")


@router.post("/crystallize", response_model=IngestResponse)
def crystallize(req: CrystallizeRequest, c: HKCContainer = Depends(get_container)):
    """
    Crystallizer 集成层入口:外部系统推送「知识候选」,经边界转换 → KDE → KEE。
    守边界:候选只是事件视角的产物,知识身份/去重仍由 KEE 裁决(Principle 07)。
    """
    from hkc_crystallizer.candidate import KnowledgeCandidate, CandidateEvidence
    if not (req.content or "").strip():
        raise HTTPException(status_code=400, detail="content 不能为空")
    ev = [CandidateEvidence(
        evidence_type = req.evidence_type or "document",
        source_id     = req.source_id or "",
        agent         = req.agent or None,
    )]
    cand = KnowledgeCandidate(
        content=req.content, title=req.title, domain_hint=req.domain,
        evidence=ev, event_refs=req.event_refs,
    )
    try:
        kus = c.crystallizer.ingest(cand) or []
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"结晶失败: {e}")
    return _ingest_response(kus)


# 单次上传文件大小上限（约 20MB），防止 OOM
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_SUFFIXES = (".pdf", ".md", ".markdown", ".txt", ".epub", ".mobi", ".azw3", ".azw")


@router.post("/ingest/file", response_model=IngestResponse)
async def ingest_file(
    file: UploadFile = File(...),
    source_title: str = Form(""),
    domain: str = Form(""),
    c: HKCContainer = Depends(get_container),
):
    """
    摄入上传的单个文件(.pdf / .md / .txt)。
    前端的文件/文件夹上传会对每个文件分别调用本端点。
    """
    filename = file.filename or "uploaded"
    suffix = os.path.splitext(filename)[1].lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的格式 {suffix}，支持: .pdf .md .txt .epub .mobi .azw3",
        )

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(data)} 字节），上限 {MAX_UPLOAD_BYTES}",
        )

    # 存到临时文件供 KDE 按路径加载,完后清理
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tf:
            tf.write(data)
            tmp_path = tf.name
        kus = c.kde.ingest_file(
            path         = tmp_path,
            source_title = source_title or os.path.splitext(filename)[0],
            domain       = domain,
            source       = filename,   # 原始上传文件名作来源标识,而非临时盘路径
        )
        return _ingest_response(kus)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件摄入失败: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@router.post("/ingest/file/async")
async def ingest_file_async(
    file: UploadFile = File(...),
    source_title: str = Form(""),
    domain: str = Form(""),
    c: HKCContainer = Depends(get_container),
):
    """
    异步摄入:立即返回 job_id,真正的解析+逐 chunk 抽取在后台线程池执行。
    前端轮询 GET /knowledge/ingest/jobs/{job_id} 拿真进度,期间可继续上传下一个。
    """
    filename = file.filename or "uploaded"
    suffix = os.path.splitext(filename)[1].lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的格式 {suffix}，支持: .pdf .md .txt .epub .mobi .azw3",
        )
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(data)} 字节），上限 {MAX_UPLOAD_BYTES}",
        )

    # 存临时文件供后台线程按路径加载(完成/失败后由 work() 清理)
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tf:
        tf.write(data)
        tmp_path = tf.name

    job = c.ingest_jobs.create(filename)

    def work(progress):
        try:
            return c.kde.ingest_file(
                path         = tmp_path,
                source_title = source_title or os.path.splitext(filename)[0],
                domain       = domain,
                source       = filename,     # 原始文件名作来源标识
                progress_cb  = progress,
            )
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    c.ingest_jobs.submit(job, work)
    return {"job_id": job.job_id, "status": job.status, "filename": filename}


@router.get("/ingest/jobs/{job_id}")
def get_ingest_job(job_id: str, c: HKCContainer = Depends(get_container)):
    """查询异步摄入任务的状态与进度。"""
    job = c.ingest_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"摄入任务不存在: {job_id}")
    return job


@router.post("/reset")
def reset_knowledge(c: HKCContainer = Depends(get_container)):
    """清空整个知识库(所有 KU / 关系 / 冲突卡),用于"清库重来"。**不可逆**。"""
    n = c.graph_store.clear()
    try:
        c.kee.dedup.rebuild_index()   # 重置去重指纹索引,否则残留旧指纹影响后续摄入
    except Exception:
        pass
    try:
        c.search.build_index()        # 重建(空)搜索索引
    except Exception:
        pass
    return {"cleared_kus": n, "status": "ok"}


# 全量图护栏：单次最多返回的 KU 数，防止超大库 OOM
MAX_GRAPH_KUS = 5000


@router.get("/graph", response_model=GraphResponse)
def get_graph(
    limit: int = 2000,
    c: HKCContainer = Depends(get_container),
):
    """
    一次性返回整个知识图谱：所有非删除 KU + 所有关系 + 统计。
    供前端 3D 星球一次拉取全量（取代逐领域 + 逐节点拼图的 N+1 方式）。
    """
    limit = max(1, min(limit, MAX_GRAPH_KUS))
    kus = c.graph_store.query_all(limit=limit)
    ku_ids = {ku.ku_id for ku in kus}

    # 只保留两端都在已返回 KU 集合里的关系，避免悬空边
    rels = c.graph_store.get_all_relations(limit=MAX_GRAPH_KUS * 4)
    relations = [
        {
            "from_ku":  r.from_ku,
            "to_ku":    r.to_ku,
            "rel_type": r.rel_type,
            "weight":   r.weight,
        }
        for r in rels
        if r.from_ku in ku_ids and r.to_ku in ku_ids
    ]

    return GraphResponse(
        # 剔除 source_text 让星球负载瘦身 ~92%(点节点看详情时再按 /ku/{id} 拉全量)
        kus       = [_ku_to_response(ku, include_source_text=False) for ku in kus],
        relations = relations,
        stats     = c.graph_store.stats(),
    )


@router.get("/ku/{ku_id}", response_model=KUResponse)
def get_ku(
    ku_id: str,
    c: HKCContainer = Depends(get_container),
):
    """按 ID 获取单个 KU。"""
    ku = c.graph_store.get(ku_id)
    if not ku:
        raise HTTPException(status_code=404, detail=f"KU 不存在: {ku_id}")
    return _ku_to_response(ku)


@router.get("/ku/{ku_id}/neighbors")
def get_ku_neighbors(
    ku_id:     str,
    max_depth: int = 2,
    top_k:     int = 20,
    c: HKCContainer = Depends(get_container),
):
    """获取 KU 的图谱邻居。"""
    ku = c.graph_store.get(ku_id)
    if not ku:
        raise HTTPException(status_code=404, detail=f"KU 不存在: {ku_id}")

    hits = c.search.search_neighbors(ku_id, max_depth=max_depth, top_k=top_k)
    return {
        "ku_id":     ku_id,
        "neighbors": [
            {
                "ku_id":    h.ku_id,
                "score":    h.score,
                "name":     h.name,
                "summary":  h.summary,
                "rel_type": getattr(h, "rel_type", ""),
            }
            for h in hits
        ],
    }


@router.get("/ku/{ku_id}/synthesis")
def get_synthesis(ku_id: str, c: HKCContainer = Depends(get_container)):
    """读取已缓存的综述（不触发生成/不调 LLM）。无缓存时 has_cache=false。"""
    cached = c.synthesis.get_cached(ku_id)
    if not cached:
        return {"ku_id": ku_id, "has_cache": False}
    return {"ku_id": ku_id, "has_cache": True, **cached}


@router.post("/ku/{ku_id}/synthesis")
def make_synthesis(
    ku_id: str,
    force: bool = False,
    c: HKCContainer = Depends(get_container),
):
    """
    生成（或返回缓存）某节点的综述。force=true 强制重新生成。
    ★ 派生只读视图：只重组已有知识，不建 KU、不写知识图（Principle 07）。
    """
    try:
        return c.synthesis.generate(ku_id, force=force)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"KU 不存在: {ku_id}")
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"综述生成失败（请检查 LLM 配置/网络）: {e}",
        )


@router.get("/domain/{domain}")
def list_by_domain(
    domain: str,
    limit:  int = 50,
    c: HKCContainer = Depends(get_container),
):
    """列出某领域的所有 KU。"""
    kus = c.graph_store.query_by_domain(domain, limit=limit)
    return {
        "domain": domain,
        "count":  len(kus),
        "kus":    [_ku_to_response(ku).model_dump() for ku in kus],
    }
