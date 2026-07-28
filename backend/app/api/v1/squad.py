"""Teams, players, lineups and XI optimisation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import CurrentTenant, Scope, require
from app.models.catalog import POSITION_ANCHOR, Player, Position, Team
from app.models.match import Lineup, LineupSlot
from app.schemas.entities import (
    LineupCreate,
    LineupOut,
    LineupSlotOut,
    PlayerCreate,
    PlayerOut,
    PlayerUpdate,
    TeamCreate,
    TeamOut,
)
from app.schemas.ops import BestXIRequest
from app.services.ml import lineup_optimizer

router = APIRouter(tags=["squad"])


def _player_out(p: Player) -> PlayerOut:
    return PlayerOut(
        **{k: getattr(p, k) for k in PlayerOut.model_fields if k not in
           {"display_name", "age", "line"}},
        display_name=p.display_name,
        age=round(p.age, 1) if p.age is not None else None,
        line=p.line,
    )


def _lineup_out(lineup: Lineup, scope: Scope) -> LineupOut:
    players = {p.id: p for p in scope.all(Player)}
    slots = []
    for s in lineup.slots:
        player = players.get(s.player_id)
        slots.append(LineupSlotOut(
            id=s.id, player_id=s.player_id, slot_index=s.slot_index,
            is_starter=s.is_starter, position=s.position, role=s.role, duty=s.duty,
            is_captain=s.is_captain, x=s.x, y=s.y,
            player_name=player.display_name if player else None,
            overall_rating=player.overall_rating if player else None,
        ))
    data = {k: getattr(lineup, k) for k in LineupOut.model_fields if k != "slots"}
    return LineupOut(**data, slots=slots)


# --- Teams -----------------------------------------------------------------

@router.post("/teams", response_model=TeamOut, status_code=201,
             dependencies=[Depends(require("squad:write"))])
def create_team(payload: TeamCreate, scope: Scope, tenant: CurrentTenant) -> TeamOut:
    if scope.first(Team, Team.slug == payload.slug):
        raise HTTPException(status_code=409, detail="A team with that slug already exists")
    limit = tenant.limits["max_teams"]
    if scope.count(Team) >= limit:
        raise HTTPException(status_code=402,
                            detail=f"Plan '{tenant.plan.value}' allows {limit} teams")
    team = Team(**payload.model_dump())
    scope.add(team)
    scope.commit()
    scope.refresh(team)
    return TeamOut.model_validate(team)


@router.get("/teams", response_model=list[TeamOut])
def list_teams(scope: Scope, kind: str | None = None) -> list[TeamOut]:
    criteria = [Team.kind == kind] if kind else []
    return [TeamOut.model_validate(t) for t in scope.all(Team, *criteria, order_by=Team.name)]


@router.get("/teams/{team_id}", response_model=TeamOut)
def get_team(team_id: str, scope: Scope) -> TeamOut:
    team = scope.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return TeamOut.model_validate(team)


# --- Players ---------------------------------------------------------------

@router.post("/players", response_model=PlayerOut, status_code=201,
             dependencies=[Depends(require("squad:write"))])
def create_player(payload: PlayerCreate, scope: Scope) -> PlayerOut:
    if payload.team_id and scope.get(Team, payload.team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")
    player = Player(**payload.model_dump())
    scope.add(player)
    scope.commit()
    scope.refresh(player)
    return _player_out(player)


@router.post("/players/bulk", response_model=list[PlayerOut], status_code=201,
             dependencies=[Depends(require("squad:write"))])
def create_players_bulk(payload: list[PlayerCreate], scope: Scope) -> list[PlayerOut]:
    """Squad import. Rejected wholesale if any row is invalid."""
    if len(payload) > 500:
        raise HTTPException(status_code=413, detail="Import at most 500 players per call")
    team_ids = {p.team_id for p in payload if p.team_id}
    known = {t.id for t in scope.all(Team)}
    if unknown := team_ids - known:
        raise HTTPException(status_code=404, detail=f"Unknown team ids: {sorted(unknown)}")

    players = [Player(**p.model_dump()) for p in payload]
    scope.add_all(players)
    scope.commit()
    return [_player_out(p) for p in players]


@router.get("/players", response_model=list[PlayerOut])
def list_players(
    scope: Scope,
    team_id: str | None = None,
    position: Position | None = None,
    available_only: bool = False,
    limit: int = Query(default=200, le=1000),
    offset: int = 0,
) -> list[PlayerOut]:
    criteria = []
    if team_id:
        criteria.append(Player.team_id == team_id)
    if position:
        criteria.append(Player.primary_position == position)
    if available_only:
        criteria.append(Player.is_available.is_(True))
    players = scope.all(Player, *criteria, limit=limit, offset=offset,
                        order_by=Player.overall_rating.desc())
    return [_player_out(p) for p in players]


@router.get("/players/{player_id}", response_model=PlayerOut)
def get_player(player_id: str, scope: Scope) -> PlayerOut:
    player = scope.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return _player_out(player)


@router.patch("/players/{player_id}", response_model=PlayerOut,
              dependencies=[Depends(require("squad:write"))])
def update_player(player_id: str, payload: PlayerUpdate, scope: Scope) -> PlayerOut:
    player = scope.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(player, field, value)
    scope.commit()
    scope.refresh(player)
    return _player_out(player)


@router.delete("/players/{player_id}", status_code=204,
               dependencies=[Depends(require("squad:write"))])
def delete_player(player_id: str, scope: Scope) -> None:
    player = scope.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    scope.delete(player)
    scope.commit()


# --- Lineups ---------------------------------------------------------------

@router.post("/lineups", response_model=LineupOut, status_code=201,
             dependencies=[Depends(require("squad:write"))])
def create_lineup(payload: LineupCreate, scope: Scope) -> LineupOut:
    if scope.get(Team, payload.team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")

    known = {p.id for p in scope.all(Player)}
    if unknown := {s.player_id for s in payload.slots} - known:
        raise HTTPException(status_code=404, detail=f"Unknown player ids: {sorted(unknown)}")

    starters = [s for s in payload.slots if s.is_starter]
    if len(starters) > 11:
        raise HTTPException(status_code=422, detail="A lineup cannot have more than 11 starters")
    if len({s.player_id for s in payload.slots}) != len(payload.slots):
        raise HTTPException(status_code=422, detail="A player appears twice in the lineup")

    data = payload.model_dump(exclude={"slots"})
    lineup = Lineup(**data)
    scope.add(lineup)
    scope.flush()

    for s in payload.slots:
        anchor = POSITION_ANCHOR.get(s.position, (52.5, 34.0))
        slot = LineupSlot(
            lineup_id=lineup.id,
            **s.model_dump(exclude={"x", "y"}),
            # Fall back to the canonical anchor when the client did not place it.
            x=s.x if s.x != 0.5 else anchor[0] / 105.0,
            y=s.y if s.y != 0.5 else anchor[1] / 68.0,
        )
        scope.add(slot)

    scope.commit()
    scope.refresh(lineup)
    return _lineup_out(lineup, scope)


@router.get("/lineups", response_model=list[LineupOut])
def list_lineups(
    scope: Scope, team_id: str | None = None, match_id: str | None = None,
    templates_only: bool = False,
) -> list[LineupOut]:
    criteria = []
    if team_id:
        criteria.append(Lineup.team_id == team_id)
    if match_id:
        criteria.append(Lineup.match_id == match_id)
    if templates_only:
        criteria.append(Lineup.is_template.is_(True))
    lineups = scope.all(Lineup, *criteria, order_by=Lineup.created_at.desc())
    return [_lineup_out(x, scope) for x in lineups]


@router.get("/lineups/{lineup_id}", response_model=LineupOut)
def get_lineup(lineup_id: str, scope: Scope) -> LineupOut:
    lineup = scope.get(Lineup, lineup_id)
    if lineup is None:
        raise HTTPException(status_code=404, detail="Lineup not found")
    return _lineup_out(lineup, scope)


@router.delete("/lineups/{lineup_id}", status_code=204,
               dependencies=[Depends(require("squad:write"))])
def delete_lineup(lineup_id: str, scope: Scope) -> None:
    lineup = scope.get(Lineup, lineup_id)
    if lineup is None:
        raise HTTPException(status_code=404, detail="Lineup not found")
    scope.delete(lineup)
    scope.commit()


# --- Optimisation ----------------------------------------------------------

@router.post("/lineups/best-xi")
def best_xi(payload: BestXIRequest, scope: Scope) -> dict:
    """Exact best XI for a formation, plus the bench it leaves behind."""
    if scope.get(Team, payload.team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")
    players = scope.all(Player, Player.team_id == payload.team_id)
    if not players:
        raise HTTPException(status_code=422, detail="This team has no players yet")

    try:
        result = lineup_optimizer.best_xi(
            players, payload.formation, minute=payload.minute,
            locked=payload.locked, bench_size=payload.bench_size,
            ignore_load=payload.ignore_load,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.to_dict()


@router.get("/lineups/formations/compare")
def compare_formations(
    team_id: str, scope: Scope,
    top_n: int = Query(default=5, ge=1, le=12),
    ignore_load: bool = False,
) -> dict:
    """Rank every known formation by how well this squad fills it."""
    players = scope.all(Player, Player.team_id == team_id)
    if not players:
        raise HTTPException(status_code=422, detail="This team has no players yet")
    return {
        "team_id": team_id,
        "known_formations": sorted(lineup_optimizer.FORMATION_SLOTS),
        "ranking": lineup_optimizer.compare_formations(
            players, top_n=top_n, ignore_load=ignore_load
        ),
    }
