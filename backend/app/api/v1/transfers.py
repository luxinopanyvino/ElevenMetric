"""Transfer market: pool management and the recommendation engine."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import CurrentTenant, Scope, require
from app.models.analysis import AnalysisReport
from app.models.catalog import Player, Position, Team
from app.models.transfer import MarketPlayer, TransferShortlist, TransferTarget
from app.schemas.entities import MarketPlayerIn, MarketPlayerOut
from app.schemas.ops import TransferScanRequest, TransferScanResponse
from app.services.ml import transfer as transfer_engine

router = APIRouter(tags=["transfers"])


def _market_out(mp: MarketPlayer) -> MarketPlayerOut:
    return MarketPlayerOut(
        **{k: getattr(mp, k) for k in MarketPlayerOut.model_fields
           if k not in {"age", "total_cost_eur"}},
        age=round(mp.age, 1) if mp.age is not None else None,
        total_cost_eur=mp.total_cost_eur,
    )


# --- Market pool -----------------------------------------------------------

@router.post("/transfers/market", response_model=list[MarketPlayerOut], status_code=201,
             dependencies=[Depends(require("transfer:write"))])
def import_market(payload: list[MarketPlayerIn], scope: Scope) -> list[MarketPlayerOut]:
    """Import scouted players into the tenant's market pool."""
    if len(payload) > 2000:
        raise HTTPException(status_code=413, detail="Import at most 2000 players per call")
    rows = [MarketPlayer(**p.model_dump()) for p in payload]
    scope.add_all(rows)
    scope.commit()
    return [_market_out(m) for m in rows]


@router.get("/transfers/market", response_model=list[MarketPlayerOut])
def list_market(
    scope: Scope,
    position: Position | None = None,
    max_price_eur: int | None = None,
    max_age: float | None = None,
    min_rating: float | None = None,
    limit: int = Query(default=100, le=1000),
    offset: int = 0,
) -> list[MarketPlayerOut]:
    criteria = []
    if position:
        criteria.append(MarketPlayer.primary_position == position)
    if max_price_eur is not None:
        criteria.append(MarketPlayer.asking_price_eur <= max_price_eur)
    if min_rating is not None:
        criteria.append(MarketPlayer.overall_rating >= min_rating)

    rows = scope.all(MarketPlayer, *criteria, limit=limit, offset=offset,
                     order_by=MarketPlayer.overall_rating.desc())
    if max_age is not None:
        rows = [m for m in rows if m.age is not None and m.age <= max_age]
    return [_market_out(m) for m in rows]


@router.delete("/transfers/market/{market_player_id}", status_code=204,
               dependencies=[Depends(require("transfer:write"))])
def delete_market_player(market_player_id: str, scope: Scope) -> None:
    mp = scope.get(MarketPlayer, market_player_id)
    if mp is None:
        raise HTTPException(status_code=404, detail="Market player not found")
    scope.delete(mp)
    scope.commit()


# --- Needs -----------------------------------------------------------------

def _scan_positions(team: Team, extra_formations: list[str] | None = None) -> set[Position]:
    """Positions worth scanning: the team's default shape plus any alternatives
    the caller named."""
    formations = [team.default_formation] + list(extra_formations or [])
    return transfer_engine.positions_for_formations(formations)


@router.get("/transfers/needs")
def squad_needs(
    team_id: str, scope: Scope, report_id: str | None = None,
    all_positions: bool = Query(
        default=False,
        description="Scan every position instead of only those the team's formation uses.",
    ),
    min_severity: float = Query(default=0.18, ge=0.0, le=1.0),
) -> dict:
    """Where this squad is weak — the input to any scan, exposed on its own so
    a sporting director can sanity-check it before targets are attached."""
    team = scope.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    players = scope.all(Player, Player.team_id == team_id)
    if not players:
        raise HTTPException(status_code=422, detail="This team has no players yet")

    vulnerabilities = []
    if report_id:
        report = scope.get(AnalysisReport, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")
        vulnerabilities = (report.tactics or {}).get("vulnerabilities", [])

    needs = transfer_engine.detect_needs(
        players, vulnerabilities=vulnerabilities,
        relevant_positions=None if all_positions else _scan_positions(team),
        min_severity=min_severity,
    )
    return {
        "team_id": team_id,
        "squad_size": len(players),
        "formation": team.default_formation,
        "scanned_positions": sorted(
            p.value for p in (set(Position) if all_positions else _scan_positions(team))
        ),
        "min_severity": min_severity,
        "needs": [n.to_dict() for n in needs],
        "used_match_analysis": bool(vulnerabilities),
    }


# --- Scan ------------------------------------------------------------------

@router.post("/transfers/scan", response_model=TransferScanResponse,
             dependencies=[Depends(require("transfer:read"))])
def scan(payload: TransferScanRequest, scope: Scope, tenant: CurrentTenant) -> TransferScanResponse:
    """Detect needs, score the market, and build an affordable signing plan."""
    team = scope.get(Team, payload.team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    players = scope.all(Player, Player.team_id == payload.team_id)
    if not players:
        raise HTTPException(status_code=422, detail="This team has no players yet")

    market = scope.all(MarketPlayer)
    if not market:
        raise HTTPException(
            status_code=422,
            detail="The market pool is empty — import targets via POST /transfers/market",
        )

    budget = payload.budget_eur if payload.budget_eur is not None else tenant.transfer_budget_eur
    wage_budget = (
        payload.wage_budget_eur_per_year
        if payload.wage_budget_eur_per_year is not None
        else tenant.wage_budget_eur_per_year
    )
    if budget <= 0:
        raise HTTPException(
            status_code=422,
            detail="No transfer budget. Set it on the club or pass budget_eur.",
        )

    vulnerabilities = []
    if payload.report_id:
        report = scope.get(AnalysisReport, payload.report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")
        vulnerabilities = (report.tactics or {}).get("vulnerabilities", [])

    needs = transfer_engine.detect_needs(
        players, vulnerabilities=vulnerabilities,
        relevant_positions=set(payload.positions) if payload.positions
        else _scan_positions(team, payload.alternative_formations),
        min_severity=payload.min_severity,
    )
    if not needs:
        # A squad with no gaps is a valid answer, not an error.
        shortlist = TransferShortlist(
            name=payload.shortlist_name, team_id=payload.team_id,
            budget_eur=budget, wage_budget_eur_per_year=wage_budget, needs={},
        )
        scope.add(shortlist)
        scope.commit()
        return TransferScanResponse(
            shortlist_id=shortlist.id, needs=[], targets=[], bundle=[],
            budget={
                "reason": (
                    "No position clears the need threshold of "
                    f"{payload.min_severity:.2f} for the formations scanned. "
                    "Lower min_severity, add alternative_formations, or pass "
                    "positions explicitly to scan anyway."
                ),
                "budget_eur": budget,
                "wage_budget_eur_per_year": wage_budget,
            },
        )

    pool = market
    if payload.max_age is not None:
        pool = [m for m in pool if m.age is None or m.age <= payload.max_age]

    if payload.style_weights:
        total = sum(payload.style_weights.values())
        if not 0.95 <= total <= 1.05:
            raise HTTPException(
                status_code=422,
                detail=f"style_weights must sum to ~1.0 (got {total:.2f})",
            )

    targets = transfer_engine.score_targets(
        pool, needs, squad_players=players, style_weights=payload.style_weights
    )
    bundle, budget_info = transfer_engine.select_bundle(
        targets, budget_eur=budget, wage_budget_eur=wage_budget,
        max_signings=payload.max_signings,
    )

    shortlist = TransferShortlist(
        name=payload.shortlist_name, team_id=payload.team_id,
        budget_eur=budget, wage_budget_eur_per_year=wage_budget,
        needs={n.position.value: round(n.severity, 3) for n in needs},
    )
    scope.add(shortlist)
    scope.flush()

    for t in targets[:60]:
        scope.add(TransferTarget(
            shortlist_id=shortlist.id, market_player_id=t.market_player.id,
            target_position=t.position, fit_score=t.fit, quality_score=t.quality,
            value_score=t.value, risk_score=t.risk, composite_score=t.composite,
            projected_upgrade=t.projected_upgrade, effective_cost_eur=t.effective_cost,
            selected=t.selected, rationale=t.rationale,
        ))
    scope.commit()

    return TransferScanResponse(
        shortlist_id=shortlist.id,
        needs=[n.to_dict() for n in needs],
        targets=[t.to_dict() for t in targets[:60]],
        bundle=[t.to_dict() for t in bundle],
        budget=budget_info,
    )


@router.get("/transfers/shortlists")
def list_shortlists(scope: Scope, team_id: str | None = None) -> list[dict]:
    criteria = [TransferShortlist.team_id == team_id] if team_id else []
    lists_ = scope.all(TransferShortlist, *criteria,
                       order_by=TransferShortlist.created_at.desc())
    return [
        {
            "id": s.id, "name": s.name, "team_id": s.team_id, "window": s.window,
            "budget_eur": s.budget_eur, "wage_budget_eur_per_year": s.wage_budget_eur_per_year,
            "needs": s.needs, "target_count": len(s.targets),
            "created_at": s.created_at.isoformat(),
        }
        for s in lists_
    ]


@router.get("/transfers/shortlists/{shortlist_id}")
def get_shortlist(shortlist_id: str, scope: Scope) -> dict:
    shortlist = scope.get(TransferShortlist, shortlist_id)
    if shortlist is None:
        raise HTTPException(status_code=404, detail="Shortlist not found")

    market = {m.id: m for m in scope.all(MarketPlayer)}
    targets = sorted(shortlist.targets, key=lambda t: -t.composite_score)
    return {
        "id": shortlist.id, "name": shortlist.name, "needs": shortlist.needs,
        "budget_eur": shortlist.budget_eur,
        "wage_budget_eur_per_year": shortlist.wage_budget_eur_per_year,
        "targets": [
            {
                "id": t.id,
                "market_player_id": t.market_player_id,
                "name": market[t.market_player_id].name if t.market_player_id in market else "?",
                "target_position": t.target_position.value,
                "composite_score": round(t.composite_score, 1),
                "quality_score": round(t.quality_score, 1),
                "fit_score": round(t.fit_score, 1),
                "value_score": round(t.value_score, 1),
                "risk_score": round(t.risk_score, 1),
                "projected_upgrade": round(t.projected_upgrade, 2),
                "effective_cost_eur": t.effective_cost_eur,
                "selected": t.selected,
                "rationale": t.rationale,
            }
            for t in targets
        ],
    }
