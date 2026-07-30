"""Match simulation: pick two sides, play the fixture, watch it back."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import Scope, require
from app.core.tenancy import TenantScope
from app.models.catalog import Player, Position, Team
from app.models.match import InputSource, Lineup, LineupSlot, Match, MatchEvent, MatchState, TrackingFrame
from app.schemas.ops import SimulationRequest, SimulationResponse
from app.services.analytics.pitch import Pitch
from app.services.ml.features import attribute
from app.services.ml.lineup_optimizer import FORMATION_SLOTS, best_xi
from app.services.simulation.engine import SimPlayer, TeamSetup, simulate

router = APIRouter(tags=["simulation"])

MAX_MINUTES = 120


def _sim_player(player: Player) -> SimPlayer:
    return SimPlayer(
        id=player.id,
        name=player.display_name,
        position=player.primary_position,
        rating=float(player.overall_rating),
        attributes=dict(player.attributes or {}),
        age=player.age,
        start_fatigue=float(player.fatigue or 0.0),
        minutes_last_7d=int(player.minutes_last_7d or 0),
    )


def _setup_from_team(scope: TenantScope, team: Team, formation: str,
                     press_height: float) -> TeamSetup:
    """Build a side from a real squad, using the optimiser to pick the XI."""
    players = scope.all(Player, Player.team_id == team.id)
    available = [p for p in players if p.is_available]
    if len(available) < 11:
        raise HTTPException(
            status_code=422,
            detail=f"{team.name} has {len(available)} available players; 11 are needed.")

    xi = best_xi(available, formation, bench_size=9)
    by_id = {p.id: p for p in available}

    starters: list[SimPlayer] = []
    for slot in xi.slots:
        player = by_id.get(slot.get("player_id") or "")
        if player is None:
            raise HTTPException(status_code=422,
                                detail=f"Could not fill the {slot['position']} slot for {team.name}")
        sim = _sim_player(player)
        # The engine plays the slot, not the player's natural position.
        sim.position = Position(slot["position"])
        starters.append(sim)

    picked = {s.id for s in starters}
    bench = [_sim_player(p) for p in available if p.id not in picked][:9]
    for b in bench:
        b.on_pitch = False

    return TeamSetup(
        name=team.name, formation=formation, starters=starters, bench=bench,
        team_id=team.id, colour=team.primary_color, press_height=press_height,
    )


def _synthetic_opponent(name: str, formation: str, strength: float,
                        press_height: float, seed: int) -> TeamSetup:
    """Build a stand-in opponent at a chosen level.

    Most clubs have no opposition squads on file, so rather than refusing to
    simulate, the engine faces a side generated at the level you name — and the
    response says plainly that is what happened.
    """
    import random

    rng = random.Random(seed)
    slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-3-3"])

    def make(position: Position, index: int, bench: bool) -> SimPlayer:
        rating = max(40.0, min(99.0, strength + rng.uniform(-4, 4) - (5 if bench else 0)))
        return SimPlayer(
            id=f"opp_{index}", name=f"{name} {position.value}{index}",
            position=position, rating=rating,
            attributes={"pace": rating, "shooting": rating, "passing": rating,
                        "dribbling": rating, "defending": rating, "physical": rating,
                        "stamina": rating, "short_passing": rating,
                        "long_passing": rating - 4, "finishing": rating,
                        "defensive_awareness": rating},
            age=rng.uniform(22, 31), start_fatigue=rng.uniform(0, 25),
        )

    starters = [make(pos, i, False) for i, pos in enumerate(slots)]
    bench = [make(pos, 20 + i, True) for i, pos in
             enumerate([Position.GK, Position.CB, Position.LB, Position.CM,
                        Position.CM, Position.LW, Position.ST])]
    for b in bench:
        b.on_pitch = False

    return TeamSetup(name=name, formation=formation, starters=starters, bench=bench,
                     colour="#eb6834", press_height=press_height)


def _persist(scope: TenantScope, result, home_team: Team, req: SimulationRequest) -> str:
    """Store the fixture as an ordinary match, so the analysis pipeline can
    consume it exactly like a real one."""
    match = Match(
        team_id=home_team.id,
        opponent_name=result.away.name,
        competition="Simulation",
        season="2025/26",
        kickoff_at=datetime.now(timezone.utc),
        venue="home",
        state=MatchState.finished,
        goals_for=result.score[0],
        goals_against=result.score[1],
        source=InputSource.tracking,
        provider="elevenmetric-sim",
        notes=f"Simulated fixture · seed {result.seed} · {result.minutes} minutes. "
              "Synthetic data, not a record of a real match.",
    )
    scope.add(match)
    scope.flush()

    scope.add_all([
        MatchEvent(
            match_id=match.id, period=e.period, minute=e.minute, second=e.second,
            type=e.type, outcome=e.outcome, is_own_team=e.is_own_team,
            player_id=e.player_id if e.is_own_team else None,
            x=e.x, y=e.y, end_x=e.end_x, end_y=e.end_y, qualifiers=e.qualifiers,
        )
        for e in result.events
    ])
    scope.add_all([
        TrackingFrame(
            match_id=match.id, period=f.period, timestamp_ms=f.timestamp_ms,
            home_positions=f.home_positions, away_positions=f.away_positions,
            ball=f.ball, possession_team=f.possession_team,
        )
        for f in result.frames
    ])

    lineup = Lineup(team_id=home_team.id, match_id=match.id, name="Simulated XI",
                    formation=result.home.formation)
    scope.add(lineup)
    scope.flush()
    anchors = FORMATION_SLOTS.get(result.home.formation, FORMATION_SLOTS["4-3-3"])
    for index, player in enumerate(result.home.starters):
        scope.add(LineupSlot(
            lineup_id=lineup.id, player_id=player.id, slot_index=index,
            is_starter=True, position=anchors[index] if index < len(anchors) else player.position,
        ))
    for index, player in enumerate(result.home.bench):
        scope.add(LineupSlot(
            lineup_id=lineup.id, player_id=player.id, slot_index=11 + index,
            is_starter=False, position=player.position,
        ))

    scope.commit()
    del req
    return match.id


@router.get("/simulation/options")
def options(scope: Scope) -> dict:
    """Everything the setup form needs: which teams can play, and how."""
    teams = scope.all(Team, order_by=Team.name)
    ready = []
    for team in teams:
        available = len([p for p in scope.all(Player, Player.team_id == team.id)
                         if p.is_available])
        ready.append({
            "id": team.id, "name": team.name, "kind": team.kind,
            "colour": team.primary_color,
            "default_formation": team.default_formation,
            "available_players": available,
            "can_play": available >= 11,
        })
    return {
        "teams": ready,
        "formations": sorted(FORMATION_SLOTS),
        "modes": [
            {"key": "instant", "label": "Instant",
             "description": "Play it out and jump straight to the result."},
            {"key": "fast", "label": "Fast (30 s)",
             "description": "Watch it back compressed into half a minute."},
            {"key": "realtime", "label": "Real time (4 min)",
             "description": "Watch it back over four minutes."},
        ],
        "max_minutes": MAX_MINUTES,
        "note": "Opposition squads are rarely on file, so a stand-in side can be "
                "generated at a level you choose. The response always says which "
                "of the two happened.",
    }


@router.post("/simulation/run", response_model=SimulationResponse,
             dependencies=[Depends(require("analysis:run"))])
def run(payload: SimulationRequest, scope: Scope) -> SimulationResponse:
    """Play a fixture between two sides.

    The heavy work is done here in one pass; playback speed is entirely a
    client-side decision, so 'instant' and 'real time' return the same data.
    """
    home_team = scope.get(Team, payload.home_team_id)
    if home_team is None:
        raise HTTPException(status_code=404, detail="Home team not found")
    if payload.minutes > MAX_MINUTES:
        raise HTTPException(status_code=422,
                            detail=f"At most {MAX_MINUTES} minutes can be simulated")

    home_formation = payload.home_formation or home_team.default_formation
    if home_formation not in FORMATION_SLOTS:
        raise HTTPException(status_code=422,
                            detail=f"Unknown formation '{home_formation}'")
    away_formation = payload.away_formation or "4-4-2"
    if away_formation not in FORMATION_SLOTS:
        raise HTTPException(status_code=422,
                            detail=f"Unknown formation '{away_formation}'")

    home = _setup_from_team(scope, home_team, home_formation, payload.home_press_height)

    opponent_is_synthetic = False
    if payload.away_team_id:
        away_team = scope.get(Team, payload.away_team_id)
        if away_team is None:
            raise HTTPException(status_code=404, detail="Away team not found")
        if away_team.id == home_team.id:
            raise HTTPException(status_code=422, detail="A team cannot play itself")
        away = _setup_from_team(scope, away_team, away_formation, payload.away_press_height)
    else:
        opponent_is_synthetic = True
        away = _synthetic_opponent(
            payload.away_name or "Opposition", away_formation,
            payload.away_strength, payload.away_press_height, payload.seed + 1)

    result = simulate(
        home, away,
        minutes=payload.minutes,
        seed=payload.seed,
        playback_hz=payload.playback_hz,
        auto_subs=payload.auto_subs,
        pitch=Pitch(),
    )

    match_id = _persist(scope, result, home_team, payload) if payload.persist else None

    return SimulationResponse(
        match_id=match_id,
        opponent_is_synthetic=opponent_is_synthetic,
        summary=result.summary(),
        playback=result.playback(),
        note=("The opposition was generated at the level you chose, not taken from "
              "a squad on file." if opponent_is_synthetic else
              "Both sides were played from their real squads."),
    )
