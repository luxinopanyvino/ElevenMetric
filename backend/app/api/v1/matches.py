"""Matches and their data feeds."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import Scope, require
from app.models.catalog import Team
from app.models.match import InputSource, Match, MatchEvent, TrackingFrame
from app.schemas.entities import EventBatch, MatchCreate, MatchOut, TrackingBatch
from app.services.analytics.pitch import PROVIDER_FRAMES, Pitch, to_metres

router = APIRouter(tags=["matches"])


@router.post("/matches", response_model=MatchOut, status_code=201,
             dependencies=[Depends(require("match:write"))])
def create_match(payload: MatchCreate, scope: Scope) -> MatchOut:
    if scope.get(Team, payload.team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")
    match = Match(**payload.model_dump())
    scope.add(match)
    scope.commit()
    scope.refresh(match)
    return MatchOut.model_validate(match)


@router.get("/matches", response_model=list[MatchOut])
def list_matches(
    scope: Scope, team_id: str | None = None, season: str | None = None,
    limit: int = Query(default=50, le=200),
) -> list[MatchOut]:
    criteria = []
    if team_id:
        criteria.append(Match.team_id == team_id)
    if season:
        criteria.append(Match.season == season)
    matches = scope.all(Match, *criteria, limit=limit, order_by=Match.created_at.desc())
    return [MatchOut.model_validate(m) for m in matches]


@router.get("/matches/{match_id}", response_model=MatchOut)
def get_match(match_id: str, scope: Scope) -> MatchOut:
    match = scope.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return MatchOut.model_validate(match)


@router.delete("/matches/{match_id}", status_code=204,
               dependencies=[Depends(require("match:write"))])
def delete_match(match_id: str, scope: Scope) -> None:
    match = scope.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    scope.delete(match)
    scope.commit()


# --- Event ingest ----------------------------------------------------------

@router.post("/matches/{match_id}/events", status_code=201,
             dependencies=[Depends(require("match:write"))])
def ingest_events(match_id: str, payload: EventBatch, scope: Scope) -> dict:
    """Ingest an event feed.

    Coordinates are converted from the provider's frame into metres on this
    match's pitch dimensions. Getting this wrong silently corrupts every
    downstream metric, so an unknown provider is a hard 422.
    """
    match = scope.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if payload.provider.lower() not in PROVIDER_FRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown provider frame '{payload.provider}'. Known: {sorted(PROVIDER_FRAMES)}",
        )
    if not payload.events:
        raise HTTPException(status_code=422, detail="No events supplied")

    pitch = Pitch(match.pitch_length_m, match.pitch_width_m)

    if payload.replace_existing:
        scope.delete_where(MatchEvent, MatchEvent.match_id == match_id)

    rows = []
    for e in payload.events:
        x, y = to_metres(e.x, e.y, payload.provider, pitch)
        end_x = end_y = None
        if e.end_x is not None and e.end_y is not None:
            end_x, end_y = to_metres(e.end_x, e.end_y, payload.provider, pitch)
        rows.append(MatchEvent(
            match_id=match_id, period=e.period, minute=e.minute, second=e.second,
            type=e.type.lower(), outcome=e.outcome.lower(), is_own_team=e.is_own_team,
            player_id=e.player_id, team_id=e.team_id,
            x=x, y=y, end_x=end_x, end_y=end_y, qualifiers=e.qualifiers,
        ))

    scope.add_all(rows)
    if match.source == InputSource.manual:
        match.source = InputSource.event_data
    match.provider = payload.provider
    scope.commit()

    return {
        "match_id": match_id,
        "ingested": len(rows),
        "provider": payload.provider,
        "converted_to": "metres on a "
                        f"{match.pitch_length_m:.0f}x{match.pitch_width_m:.0f} pitch",
    }


@router.get("/matches/{match_id}/events")
def list_events(
    match_id: str, scope: Scope,
    type: str | None = None,
    own_team_only: bool = False,
    limit: int = Query(default=1000, le=20000),
    offset: int = 0,
) -> dict:
    if scope.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    criteria = [MatchEvent.match_id == match_id]
    if type:
        criteria.append(MatchEvent.type == type.lower())
    if own_team_only:
        criteria.append(MatchEvent.is_own_team.is_(True))
    events = scope.all(MatchEvent, *criteria, limit=limit, offset=offset,
                       order_by=(MatchEvent.period, MatchEvent.minute, MatchEvent.second))
    return {
        "match_id": match_id,
        "count": len(events),
        "events": [
            {
                "id": e.id, "period": e.period, "minute": e.minute, "second": e.second,
                "type": e.type, "outcome": e.outcome, "is_own_team": e.is_own_team,
                "player_id": e.player_id, "x": e.x, "y": e.y,
                "end_x": e.end_x, "end_y": e.end_y, "qualifiers": e.qualifiers,
            }
            for e in events
        ],
    }


# --- Tracking ingest -------------------------------------------------------

@router.post("/matches/{match_id}/tracking", status_code=201,
             dependencies=[Depends(require("match:write"))])
def ingest_tracking(match_id: str, payload: TrackingBatch, scope: Scope) -> dict:
    """Ingest tracking frames, decimated to ``target_hz``.

    A 90-minute match at 25 Hz is 135,000 frames; nothing in the analytics layer
    benefits from more than ~5 Hz, and storing the raw rate makes every query
    slower for no gain.
    """
    match = scope.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if payload.provider.lower() not in PROVIDER_FRAMES:
        raise HTTPException(status_code=422, detail=f"Unknown provider frame '{payload.provider}'")
    if not payload.frames:
        raise HTTPException(status_code=422, detail="No frames supplied")

    pitch = Pitch(match.pitch_length_m, match.pitch_width_m)

    if payload.replace_existing:
        scope.delete_where(TrackingFrame, TrackingFrame.match_id == match_id)

    frames = sorted(payload.frames, key=lambda f: (f.period, f.timestamp_ms))
    interval_ms = int(1000 / payload.target_hz)
    kept: list[TrackingFrame] = []
    last_kept_ms: dict[int, int] = {}

    def _convert(positions: dict[str, list[float]]) -> dict[str, list[float]]:
        out = {}
        for pid, pos in positions.items():
            if len(pos) < 2:
                continue
            mx, my = to_metres(pos[0], pos[1], payload.provider, pitch)
            out[pid] = [round(mx, 2), round(my, 2)]
        return out

    for f in frames:
        last = last_kept_ms.get(f.period)
        if last is not None and f.timestamp_ms - last < interval_ms:
            continue
        last_kept_ms[f.period] = f.timestamp_ms

        ball = None
        if f.ball and len(f.ball) >= 2:
            bx, by = to_metres(f.ball[0], f.ball[1], payload.provider, pitch)
            ball = [round(bx, 2), round(by, 2)] + ([f.ball[2]] if len(f.ball) > 2 else [])

        kept.append(TrackingFrame(
            match_id=match_id, period=f.period, timestamp_ms=f.timestamp_ms,
            home_positions=_convert(f.home_positions),
            away_positions=_convert(f.away_positions),
            ball=ball, possession_team=f.possession_team,
        ))

    scope.add_all(kept)
    match.source = InputSource.tracking
    match.provider = payload.provider
    scope.commit()

    return {
        "match_id": match_id,
        "received": len(payload.frames),
        "stored": len(kept),
        "decimated_to_hz": payload.target_hz,
        "note": "Frames are stored in metres after decimation.",
    }


@router.get("/matches/{match_id}/tracking")
def list_tracking(
    match_id: str, scope: Scope,
    limit: int = Query(default=500, le=10000), offset: int = 0,
) -> dict:
    if scope.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    frames = scope.all(TrackingFrame, TrackingFrame.match_id == match_id,
                       limit=limit, offset=offset,
                       order_by=(TrackingFrame.period, TrackingFrame.timestamp_ms))
    return {
        "match_id": match_id,
        "count": len(frames),
        "frames": [
            {
                "period": f.period, "timestamp_ms": f.timestamp_ms,
                "home_positions": f.home_positions, "away_positions": f.away_positions,
                "ball": f.ball, "possession_team": f.possession_team,
            }
            for f in frames
        ],
    }
