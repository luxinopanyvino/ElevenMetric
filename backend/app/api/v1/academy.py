"""Academy: youth roster, assessments and readiness projections."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import Scope, require
from app.models.academy import AcademyAssessment, AcademyPlayer, AgeGroup, Pathway
from app.models.catalog import Player, Position, Team
from app.schemas.entities import (
    AcademyAssessmentIn,
    AcademyAssessmentOut,
    AcademyPlayerCreate,
    AcademyPlayerOut,
)
from app.schemas.ops import AcademyReviewRequest, AcademyReviewResponse
from app.services.ml import academy as academy_engine

router = APIRouter(tags=["academy"])


def _out(p: AcademyPlayer) -> AcademyPlayerOut:
    return AcademyPlayerOut(
        **{k: getattr(p, k) for k in AcademyPlayerOut.model_fields
           if k not in {"age", "assessments"}},
        age=round(p.age, 1) if p.age is not None else None,
        assessments=[AcademyAssessmentOut.model_validate(a) for a in p.assessments],
    )


# --- Roster ----------------------------------------------------------------

@router.post("/academy/players", response_model=AcademyPlayerOut, status_code=201,
             dependencies=[Depends(require("academy:write"))])
def create_academy_player(payload: AcademyPlayerCreate, scope: Scope) -> AcademyPlayerOut:
    if payload.team_id and scope.get(Team, payload.team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")
    player = AcademyPlayer(**payload.model_dump())
    scope.add(player)
    scope.commit()
    scope.refresh(player)
    return _out(player)


@router.get("/academy/players", response_model=list[AcademyPlayerOut])
def list_academy_players(
    scope: Scope,
    age_group: AgeGroup | None = None,
    position: Position | None = None,
    pathway: Pathway | None = None,
    ready_within_months: float | None = Query(
        default=None, description="Only players projected ready inside this horizon."
    ),
    limit: int = Query(default=200, le=1000),
) -> list[AcademyPlayerOut]:
    criteria = []
    if age_group:
        criteria.append(AcademyPlayer.age_group == age_group)
    if position:
        criteria.append(AcademyPlayer.primary_position == position)
    if pathway:
        criteria.append(AcademyPlayer.pathway == pathway)

    players = scope.all(AcademyPlayer, *criteria, limit=limit,
                        order_by=AcademyPlayer.readiness_score.desc())
    if ready_within_months is not None:
        players = [
            p for p in players
            if p.months_to_first_team is not None
            and p.months_to_first_team <= ready_within_months
        ]
    return [_out(p) for p in players]


@router.get("/academy/players/{player_id}", response_model=AcademyPlayerOut)
def get_academy_player(player_id: str, scope: Scope) -> AcademyPlayerOut:
    player = scope.get(AcademyPlayer, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Academy player not found")
    return _out(player)


@router.delete("/academy/players/{player_id}", status_code=204,
               dependencies=[Depends(require("academy:write"))])
def delete_academy_player(player_id: str, scope: Scope) -> None:
    player = scope.get(AcademyPlayer, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Academy player not found")
    scope.delete(player)
    scope.commit()


# --- Assessments -----------------------------------------------------------

@router.post("/academy/players/{player_id}/assessments",
             response_model=AcademyAssessmentOut, status_code=201,
             dependencies=[Depends(require("academy:write"))])
def add_assessment(
    player_id: str, payload: AcademyAssessmentIn, scope: Scope
) -> AcademyAssessmentOut:
    player = scope.get(AcademyPlayer, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Academy player not found")
    if payload.assessed_on > date.today():
        raise HTTPException(status_code=422, detail="Assessment date is in the future")

    assessment = AcademyAssessment(academy_player_id=player_id, **payload.model_dump())
    scope.add(assessment)
    # The latest assessment is the player's current ability.
    if not player.assessments or payload.assessed_on >= max(a.assessed_on for a in player.assessments):
        player.current_ability = payload.ability
    scope.commit()
    scope.refresh(assessment)
    return AcademyAssessmentOut.model_validate(assessment)


@router.get("/academy/players/{player_id}/assessments",
            response_model=list[AcademyAssessmentOut])
def list_assessments(player_id: str, scope: Scope) -> list[AcademyAssessmentOut]:
    player = scope.get(AcademyPlayer, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Academy player not found")
    return [AcademyAssessmentOut.model_validate(a) for a in player.assessments]


# --- Projections -----------------------------------------------------------

@router.post("/academy/review", response_model=AcademyReviewResponse,
             dependencies=[Depends(require("academy:read"))])
def review(payload: AcademyReviewRequest, scope: Scope) -> AcademyReviewResponse:
    """Project every academy player's route to the first team."""
    academy = scope.all(AcademyPlayer)
    if not academy:
        raise HTTPException(status_code=422, detail="No academy players on file")

    criteria = [Player.team_id == payload.team_id] if payload.team_id else []
    senior = scope.all(Player, *criteria)

    result = academy_engine.review_squad(academy, senior)

    if payload.persist:
        by_id = {p.id: p for p in academy}
        for proj in result["projections"]:
            player = by_id.get(proj["academy_player_id"])
            if player is None:
                continue
            player.readiness_score = proj["readiness_score"]
            player.months_to_first_team = proj["months_to_first_team"]
            player.projected_ready_on = (
                date.fromisoformat(proj["projected_ready_on"])
                if proj["projected_ready_on"] else None
            )
            player.pathway = Pathway(proj["pathway"])
            player.projection = proj
        scope.commit()

    return AcademyReviewResponse(**result)


@router.get("/academy/players/{player_id}/projection")
def project_one(player_id: str, scope: Scope, team_id: str | None = None) -> dict:
    """Project a single player, without persisting."""
    player = scope.get(AcademyPlayer, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Academy player not found")
    criteria = [Player.team_id == team_id] if team_id else []
    senior = scope.all(Player, *criteria)
    return academy_engine.project(player, senior_players=senior).to_dict()


@router.get("/academy/pipeline")
def pipeline(scope: Scope, team_id: str | None = None, horizon_months: int = 24) -> dict:
    """Calendar view: who arrives when, and which positions the academy does
    not cover — the handoff to the transfer engine."""
    academy = scope.all(AcademyPlayer)
    if not academy:
        return {"windows": [], "uncovered_positions": [p.value for p in Position],
                "note": "No academy players on file."}

    criteria = [Player.team_id == team_id] if team_id else []
    senior = scope.all(Player, *criteria)
    result = academy_engine.review_squad(academy, senior)

    today = date.today()
    windows: dict[str, list[dict]] = {}
    for proj in result["projections"]:
        m = proj["months_to_first_team"]
        if m is None or m > horizon_months:
            continue
        target = today + timedelta(days=int(m * 30.44))
        key = f"{target.year}-{'H1' if target.month <= 6 else 'H2'}"
        windows.setdefault(key, []).append({
            "name": proj["name"], "position": proj["position"],
            "months": proj["months_to_first_team"], "pathway": proj["pathway"],
            "readiness_score": proj["readiness_score"],
            "confidence": proj["confidence"],
        })

    return {
        "horizon_months": horizon_months,
        "windows": [
            {"window": k, "arrivals": sorted(v, key=lambda d: d["months"])}
            for k, v in sorted(windows.items())
        ],
        "uncovered_positions": result["summary"]["uncovered_positions"],
        "first_team_bar": result["summary"]["first_team_bar"],
        "by_pathway": result["summary"]["by_pathway"],
    }


@router.post("/academy/players/{player_id}/promote", status_code=201,
             dependencies=[Depends(require("academy:write"))])
def promote(player_id: str, team_id: str, scope: Scope) -> dict:
    """Move an academy player into the senior squad, keeping the link."""
    academy_player = scope.get(AcademyPlayer, player_id)
    if academy_player is None:
        raise HTTPException(status_code=404, detail="Academy player not found")
    if academy_player.senior_player_id:
        raise HTTPException(status_code=409, detail="This player has already been promoted")
    if scope.get(Team, team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")

    senior = Player(
        team_id=team_id, name=academy_player.name, birth_date=academy_player.birth_date,
        nationality=academy_player.nationality,
        primary_position=academy_player.primary_position,
        secondary_positions=academy_player.secondary_positions,
        preferred_foot=academy_player.preferred_foot,
        height_cm=academy_player.height_cm,
        overall_rating=academy_player.current_ability,
        potential_rating=academy_player.potential_ability,
        contract_until=academy_player.contract_until,
    )
    scope.add(senior)
    scope.flush()
    academy_player.senior_player_id = senior.id
    academy_player.pathway = Pathway.promote_now
    scope.commit()

    return {
        "academy_player_id": player_id,
        "senior_player_id": senior.id,
        "team_id": team_id,
        "message": f"{academy_player.name} promoted to the senior squad.",
    }
