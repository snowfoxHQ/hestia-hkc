"""
hkc-api / routes / ability.py
能力编译（ACE）与冲突裁决（KEE）路由。
"""
from fastapi import APIRouter, HTTPException, Depends

from ..container import HKCContainer, get_container
from ..schemas import (
    AbilityCompileRequest, AbilityResponse, CoverageReportResponse,
    ConflictResponse, ConflictResolveRequest,
)

router = APIRouter(tags=["ability"])


# ── ACE ──────────────────────────────────────────────────────

@router.get("/abilities")
def list_abilities(c: HKCContainer = Depends(get_container)):
    """列出所有已定义的 Ability。"""
    return {"abilities": c.ace.list_available()}


@router.get("/abilities/{ability_key}/coverage",
            response_model=CoverageReportResponse)
def ability_coverage(
    ability_key: str,
    c: HKCContainer = Depends(get_container),
):
    """查看某 Ability 的当前知识覆盖情况（不编译）。"""
    report = c.ace.coverage_report(ability_key)
    if not report:
        raise HTTPException(status_code=404, detail=f"未知 Ability: {ability_key}")
    return CoverageReportResponse(**report)


@router.post("/abilities/{ability_key}/compile",
             response_model=AbilityResponse)
def compile_ability(
    ability_key: str,
    c: HKCContainer = Depends(get_container),
):
    """
    编译一个 Ability。
    覆盖度不足时返回 422（知识不够，无法编译）。
    """
    try:
        pkg = c.ace.compile(ability_key)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if pkg is None:
        report = c.ace.coverage_report(ability_key)
        raise HTTPException(
            status_code=422,
            detail={
                "message":        "知识覆盖度不足，无法编译此 Ability",
                "coverage":       report.get("coverage", {}),
                "missing_skills": report.get("missing_skills", []),
            }
        )

    return AbilityResponse(
        ability_key  = pkg.ability_key,
        display_name = pkg.display_name,
        domain       = pkg.domain,
        coverage     = pkg.coverage,
        skills       = [
            {"skill_key": s.skill_key, "display_name": s.display_name,
             "coverage": s.coverage, "concept_hits": s.concept_hits}
            for s in pkg.skills
        ],
        workflows    = [{"name": w.name, "steps": w.steps} for w in pkg.workflows],
        version      = pkg.version,
    )


@router.get("/abilities/{ability_key}", response_model=AbilityResponse)
def get_ability(
    ability_key: str,
    c: HKCContainer = Depends(get_container),
):
    """加载已编译的 Ability Package。"""
    pkg = c.ace.load_ability(ability_key)
    if not pkg:
        raise HTTPException(
            status_code=404,
            detail=f"Ability 尚未编译: {ability_key}，请先调用 /compile"
        )
    return AbilityResponse(
        ability_key  = pkg.ability_key,
        display_name = pkg.display_name,
        domain       = pkg.domain,
        coverage     = pkg.coverage,
        skills       = [
            {"skill_key": s.skill_key, "display_name": s.display_name,
             "coverage": s.coverage, "concept_hits": s.concept_hits}
            for s in pkg.skills
        ],
        workflows    = [{"name": w.name, "steps": w.steps} for w in pkg.workflows],
        version      = pkg.version,
    )


# ── KEE 冲突 ─────────────────────────────────────────────────

@router.get("/conflicts")
def list_conflicts(
    status: str = "open",
    domain: str = "",
    c: HKCContainer = Depends(get_container),
):
    """列出冲突卡。"""
    cards = c.graph_store.list_conflicts(status=status, domain=domain)
    return {
        "status": status,
        "count":  len(cards),
        "conflicts": [
            ConflictResponse(
                conflict_id         = card.conflict_id,
                claim_a_id          = card.claim_a_id,
                claim_b_id          = card.claim_b_id,
                domain              = card.domain,
                status              = card.status.value,
                resolution_strategy = card.resolution_strategy.value,
                resolution_note     = card.resolution_note,
            ).model_dump()
            for card in cards
        ],
    }


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict(
    conflict_id: str,
    req: ConflictResolveRequest,
    c: HKCContainer = Depends(get_container),
):
    """人工裁决一个冲突。"""
    ok = c.kee.manual_resolve(
        conflict_id = conflict_id,
        winner_id   = req.winner_id,
        note        = req.note,
        resolved_by = req.resolved_by,
    )
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=f"裁决失败：冲突 {conflict_id} 不存在、已解决，或 winner_id 无效"
        )
    return {"conflict_id": conflict_id, "resolved": True, "winner": req.winner_id}
