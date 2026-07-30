"""CSV ingestion: inspect a file, preview what it would do, then commit."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from app.api.deps import Scope, require
from app.models.academy import AcademyAssessment, AcademyPlayer
from app.models.catalog import Player, Team
from app.models.match import InputSource, Match, MatchEvent, TrackingFrame
from app.models.transfer import MarketPlayer
from app.services.analytics.pitch import PROVIDER_FRAMES, Pitch, to_metres
from app.services.ingest import csv_ingest

router = APIRouter(tags=["ingest"])

MAX_CSV_MB = 32


async def _read_upload(file: UploadFile) -> bytes:
    raw = await file.read()
    if len(raw) > MAX_CSV_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413, detail=f"CSV exceeds the {MAX_CSV_MB} MB limit")
    if not raw:
        raise HTTPException(status_code=422, detail="The file is empty")
    return raw


@router.get("/ingest/datasets")
def datasets() -> dict:
    """Every dataset that can be imported, with its columns and aliases."""
    return {
        "datasets": csv_ingest.catalogue(),
        "providers": sorted(PROVIDER_FRAMES),
        "max_rows": csv_ingest.MAX_ROWS,
        "max_file_mb": MAX_CSV_MB,
        "note": "Headers are matched case-insensitively, ignoring spaces, "
                "hyphens and underscores, and common aliases are accepted. "
                "Unrecognised columns are reported, never guessed at.",
    }


@router.get("/ingest/template/{dataset}", response_class=PlainTextResponse)
def csv_template(dataset: str) -> str:
    """A ready-to-fill CSV: header row plus one example row."""
    if dataset not in csv_ingest.DATASETS:
        raise HTTPException(status_code=404,
                            detail=f"Unknown dataset '{dataset}'")
    return csv_ingest.template(dataset)


@router.post("/ingest/preview", dependencies=[Depends(require("squad:write"))])
async def preview(
    file: Annotated[UploadFile, File(description="CSV file")],
    dataset: Annotated[str, Form()],
) -> dict:
    """Parse and validate without writing anything.

    A bad import is far cheaper to catch than to undo, so the UI always runs
    this first and shows the mapping and per-row errors before offering commit.
    """
    raw = await _read_upload(file)
    try:
        result = csv_ingest.parse(dataset, raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.to_dict()


# --- Commit ----------------------------------------------------------------

def _strip(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "__row__"}


def _commit_players(scope: Scope, rows: list[dict], team_id: str | None) -> dict:
    if team_id and scope.get(Team, team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")

    existing = {p.name.lower(): p for p in scope.all(Player)}
    created = updated = 0
    for row in rows:
        data = _strip(row)
        hit = existing.get(str(data.get("name", "")).lower())
        if hit is not None:
            # Re-importing a squad file should refresh it, not duplicate it.
            for key, value in data.items():
                setattr(hit, key, value)
            updated += 1
        else:
            scope.add(Player(team_id=team_id, **data))
            created += 1
    scope.commit()
    return {"created": created, "updated": updated}


def _commit_market(scope: Scope, rows: list[dict], _ctx) -> dict:
    for row in rows:
        scope.add(MarketPlayer(**_strip(row)))
    scope.commit()
    return {"created": len(rows), "updated": 0}


def _commit_academy_players(scope: Scope, rows: list[dict], team_id: str | None) -> dict:
    if team_id and scope.get(Team, team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")
    existing = {p.name.lower(): p for p in scope.all(AcademyPlayer)}
    created = updated = 0
    for row in rows:
        data = _strip(row)
        hit = existing.get(str(data.get("name", "")).lower())
        if hit is not None:
            for key, value in data.items():
                setattr(hit, key, value)
            updated += 1
        else:
            scope.add(AcademyPlayer(team_id=team_id, **data))
            created += 1
    scope.commit()
    return {"created": created, "updated": updated}


def _commit_assessments(scope: Scope, rows: list[dict], _ctx) -> dict:
    by_name = {p.name.lower(): p for p in scope.all(AcademyPlayer)}
    unknown: set[str] = set()
    created = 0
    for row in rows:
        data = _strip(row)
        name = str(data.pop("player_name", "")).strip()
        player = by_name.get(name.lower())
        if player is None:
            unknown.add(name)
            continue
        scope.add(AcademyAssessment(academy_player_id=player.id, **data))
        created += 1
    scope.commit()
    if unknown:
        return {"created": created, "updated": 0,
                "warnings": [f"No academy player named {n!r} — those rows were skipped"
                             for n in sorted(unknown)]}
    return {"created": created, "updated": 0}


def _commit_events(scope: Scope, rows: list[dict], ctx: dict) -> dict:
    match = scope.get(Match, ctx.get("match_id") or "")
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    provider = (ctx.get("provider") or "elevenmetric").lower()
    if provider not in PROVIDER_FRAMES:
        raise HTTPException(status_code=422, detail=f"Unknown provider '{provider}'")

    pitch = Pitch(match.pitch_length_m, match.pitch_width_m)
    if ctx.get("replace_existing"):
        scope.delete_where(MatchEvent, MatchEvent.match_id == match.id)

    for row in rows:
        data = _strip(row)
        x, y = to_metres(data.pop("x"), data.pop("y"), provider, pitch)
        end_x = end_y = None
        if data.get("end_x") is not None and data.get("end_y") is not None:
            end_x, end_y = to_metres(data.pop("end_x"), data.pop("end_y"), provider, pitch)
        qualifiers = {}
        for key in ("situation", "body_part"):
            if data.get(key):
                qualifiers[key] = data.pop(key)
        data.pop("end_x", None)
        data.pop("end_y", None)
        data["type"] = str(data.get("type", "")).lower()
        data["outcome"] = str(data.get("outcome", "success")).lower()
        scope.add(MatchEvent(match_id=match.id, x=x, y=y, end_x=end_x, end_y=end_y,
                             qualifiers=qualifiers, **data))

    if match.source == InputSource.manual:
        match.source = InputSource.event_data
    match.provider = provider
    scope.commit()
    return {"created": len(rows), "updated": 0}


def _commit_tracking(scope: Scope, rows: list[dict], ctx: dict) -> dict:
    match = scope.get(Match, ctx.get("match_id") or "")
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    provider = (ctx.get("provider") or "elevenmetric").lower()
    if provider not in PROVIDER_FRAMES:
        raise HTTPException(status_code=422, detail=f"Unknown provider '{provider}'")
    target_hz = float(ctx.get("target_hz") or 5.0)

    pitch = Pitch(match.pitch_length_m, match.pitch_width_m)
    if ctx.get("replace_existing"):
        scope.delete_where(TrackingFrame, TrackingFrame.match_id == match.id)

    # Pivot the long format into frames keyed by (period, timestamp).
    frames: dict[tuple[int, int], dict] = {}
    for row in rows:
        key = (int(row["period"]), int(row["timestamp_ms"]))
        frame = frames.setdefault(key, {"home": {}, "away": {}, "ball": None})
        mx, my = to_metres(row["x"], row["y"], provider, pitch)
        side = str(row.get("team", "")).strip().lower()
        if side == "ball":
            frame["ball"] = [round(mx, 2), round(my, 2)]
        elif side in ("home", "away"):
            pid = str(row.get("player_id") or f"{side}_{len(frame[side])}")
            frame[side][pid] = [round(mx, 2), round(my, 2)]

    interval = int(1000 / target_hz)
    last_kept: dict[int, int] = {}
    stored = 0
    for (period, ts) in sorted(frames):
        previous = last_kept.get(period)
        if previous is not None and ts - previous < interval:
            continue
        last_kept[period] = ts
        frame = frames[(period, ts)]
        scope.add(TrackingFrame(
            match_id=match.id, period=period, timestamp_ms=ts,
            home_positions=frame["home"], away_positions=frame["away"],
            ball=frame["ball"],
        ))
        stored += 1

    match.source = InputSource.tracking
    match.provider = provider
    scope.commit()
    return {"created": stored, "updated": 0,
            "note": f"{len(frames)} frames received, {stored} stored after "
                    f"decimation to {target_hz} Hz"}


_COMMITTERS = {
    "players": _commit_players,
    "market_players": _commit_market,
    "academy_players": _commit_academy_players,
    "academy_assessments": _commit_assessments,
    "events": _commit_events,
    "tracking": _commit_tracking,
}


@router.post("/ingest/commit", status_code=201,
             dependencies=[Depends(require("squad:write"))])
async def commit(
    scope: Scope,
    file: Annotated[UploadFile, File(description="CSV file")],
    dataset: Annotated[str, Form()],
    team_id: Annotated[str | None, Form()] = None,
    match_id: Annotated[str | None, Form()] = None,
    provider: Annotated[str, Form()] = "elevenmetric",
    target_hz: Annotated[float, Form()] = 5.0,
    replace_existing: Annotated[bool, Form()] = False,
    allow_partial: Annotated[bool, Form()] = False,
) -> dict:
    """Write the file.

    Refuses by default when any row failed validation: a half-imported squad is
    worse than none. ``allow_partial`` opts into importing the good rows and
    skipping the rest.
    """
    raw = await _read_upload(file)
    try:
        result = csv_ingest.parse(dataset, raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if result.missing_required:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required columns: {result.missing_required}")
    if result.errors and not allow_partial:
        raise HTTPException(
            status_code=422,
            detail=f"{len(result.errors)} row(s) failed validation. Fix the file, "
                   "or set allow_partial to import the valid rows only.")
    if not result.rows:
        raise HTTPException(status_code=422, detail="No valid rows to import")

    ctx = {"match_id": match_id, "provider": provider, "target_hz": target_hz,
           "replace_existing": replace_existing}
    committer = _COMMITTERS[dataset]
    outcome = committer(
        scope, result.rows, team_id if dataset in ("players", "academy_players") else ctx)

    return {
        "dataset": dataset,
        "rows_in_file": result.total_rows,
        "rows_imported": len(result.rows),
        "rows_skipped": result.total_rows - len(result.rows),
        **outcome,
    }
