"""External data sources: browse, preview, then commit.

Same contract as the CSV route — nothing is written until a preview has been
seen and a commit explicitly asked for — and the same failure discipline: a
source that is unavailable says so with the remedy (503), and a source that
answers with something unrecognised says what it expected (502). Neither writes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import Scope, require
from app.services.external import capabilities, sofifa, statsbomb
from app.services.external.base import FetchError, SourceUnavailable
from app.services.external.commit import commit_fixture, commit_squad, map_fixture, map_squad

router = APIRouter(prefix="/external", tags=["external"])

MAX_FILE_MB = 32


def _handle(exc: Exception):
    """Translate a source failure into a response that names the remedy."""
    if isinstance(exc, SourceUnavailable):
        raise HTTPException(status_code=503, detail={
            "source": exc.source, "reason": exc.reason, "remedy": exc.remedy,
        })
    if isinstance(exc, FetchError):
        raise HTTPException(status_code=502, detail={
            "source": exc.source, "url": exc.url,
            "expected": exc.expected, "detail": exc.detail,
        })
    raise exc


# --- Discovery -------------------------------------------------------------

@router.get("/sources")
def sources() -> dict:
    """Every external source, what it is, and whether it can be used now.

    Readable by any authenticated user so the UI can render the panel's state —
    including *why* a source is greyed out — before anyone clicks anything.
    """
    return {
        "sources": capabilities(),
        "note": "These sources are not interchangeable. SoFIFA supplies a video "
                "game's ratings for real clubs; StatsBomb supplies real match "
                "data and no ratings at all. Imported rows carry a provenance "
                "record saying which, and when it was read.",
    }


# --- SoFIFA ----------------------------------------------------------------

@router.get("/sofifa/clubs")
def sofifa_clubs(q: str, limit: int = 20) -> dict:
    """Search clubs by name. Writes nothing."""
    if len(q.strip()) < 2:
        raise HTTPException(status_code=422, detail="Search for at least 2 characters")
    try:
        return {"query": q, "clubs": sofifa.search_clubs(q, limit=limit)}
    except (SourceUnavailable, FetchError) as exc:
        _handle(exc)


@router.get("/sofifa/preview")
def sofifa_preview(club_id: str, with_attributes: bool = True) -> dict:
    """The squad exactly as it would be stored. Writes nothing."""
    try:
        squad = sofifa.fetch_squad(club_id, with_attributes=with_attributes)
    except (SourceUnavailable, FetchError) as exc:
        _handle(exc)
    return map_squad(squad, source=sofifa.SOURCE).to_dict()


@router.post("/sofifa/preview-file")
async def sofifa_preview_file(
    file: Annotated[UploadFile, File(description="A saved SoFIFA club page (.html) "
                                                "or a SoFIFA-format export (.csv)")],
    club_name: Annotated[str, Form()] = "",
) -> dict:
    """Preview a squad from a file. Works with no network and no live markup."""
    raw = await _read(file)
    try:
        squad = sofifa.load_squad_from_bytes(raw, filename=file.filename or "",
                                             club_name=club_name)
    except (SourceUnavailable, FetchError) as exc:
        _handle(exc)
    return map_squad(squad, source=sofifa.SOURCE).to_dict()


@router.post("/sofifa/commit", status_code=201,
             dependencies=[Depends(require("squad:write"))])
def sofifa_commit(
    scope: Scope,
    club_id: Annotated[str, Form()],
    with_attributes: Annotated[bool, Form()] = True,
    kind: Annotated[str, Form()] = "opponent",
    team_id: Annotated[str | None, Form()] = None,
    formation: Annotated[str, Form()] = "4-3-3",
    allow_partial: Annotated[bool, Form()] = False,
) -> dict:
    """Import a club as a team and its players."""
    try:
        squad = sofifa.fetch_squad(club_id, with_attributes=with_attributes)
    except (SourceUnavailable, FetchError) as exc:
        _handle(exc)
    return _commit_squad(scope, squad, kind=kind, team_id=team_id,
                         formation=formation, allow_partial=allow_partial)


@router.post("/sofifa/commit-file", status_code=201,
             dependencies=[Depends(require("squad:write"))])
async def sofifa_commit_file(
    scope: Scope,
    file: Annotated[UploadFile, File()],
    club_name: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "opponent",
    team_id: Annotated[str | None, Form()] = None,
    formation: Annotated[str, Form()] = "4-3-3",
    allow_partial: Annotated[bool, Form()] = False,
) -> dict:
    """Import a club from a saved page or an export file."""
    raw = await _read(file)
    try:
        squad = sofifa.load_squad_from_bytes(raw, filename=file.filename or "",
                                             club_name=club_name)
    except (SourceUnavailable, FetchError) as exc:
        _handle(exc)
    return _commit_squad(scope, squad, kind=kind, team_id=team_id,
                         formation=formation, allow_partial=allow_partial)


def _commit_squad(scope, squad, *, kind: str, team_id: str | None,
                  formation: str, allow_partial: bool) -> dict:
    mapped = map_squad(squad, source=sofifa.SOURCE)
    if mapped.errors and not allow_partial:
        raise HTTPException(status_code=422, detail={
            "message": f"{len(mapped.errors)} player(s) could not be mapped. "
                       "Set allow_partial to import the rest, or fix the source "
                       "data.",
            "errors": mapped.errors,
        })
    if not mapped.players:
        raise HTTPException(status_code=422, detail="No players could be mapped")
    if kind not in ("first_team", "academy", "opponent"):
        raise HTTPException(status_code=422,
                            detail="kind must be first_team, academy or opponent")
    try:
        return commit_squad(scope, mapped, kind=kind, team_id=team_id,
                            formation=formation)
    except LookupError:
        raise HTTPException(status_code=404, detail="Team not found")


# --- StatsBomb -------------------------------------------------------------

@router.get("/statsbomb/competitions")
def statsbomb_competitions() -> dict:
    try:
        return {"competitions": statsbomb.competitions(),
                "attribution": statsbomb.ATTRIBUTION}
    except (SourceUnavailable, FetchError) as exc:
        _handle(exc)


@router.get("/statsbomb/matches")
def statsbomb_matches(competition_id: int, season_id: int) -> dict:
    try:
        return {"matches": statsbomb.matches(competition_id, season_id),
                "attribution": statsbomb.ATTRIBUTION}
    except (SourceUnavailable, FetchError) as exc:
        _handle(exc)


@router.get("/statsbomb/preview")
def statsbomb_preview(match_id: int, competition_id: int | None = None,
                      season_id: int | None = None) -> dict:
    """The fixture exactly as it would be stored. Writes nothing."""
    try:
        fixture = statsbomb.fetch_fixture(match_id)
        _enrich(fixture, match_id, competition_id, season_id)
    except (SourceUnavailable, FetchError) as exc:
        _handle(exc)
    return map_fixture(fixture)


@router.post("/statsbomb/commit", status_code=201,
             dependencies=[Depends(require("squad:write"))])
def statsbomb_commit(
    scope: Scope,
    match_id: Annotated[int, Form()],
    competition_id: Annotated[int | None, Form()] = None,
    season_id: Annotated[int | None, Form()] = None,
    own_team_id: Annotated[str | None, Form()] = None,
) -> dict:
    """Import a fixture as a match with both lineups and its event feed."""
    try:
        fixture = statsbomb.fetch_fixture(match_id)
        _enrich(fixture, match_id, competition_id, season_id)
    except (SourceUnavailable, FetchError) as exc:
        _handle(exc)

    if not fixture.events:
        raise HTTPException(status_code=422, detail={
            "message": f"StatsBomb publishes no events for match {match_id} — it "
                       "is metadata-only in the open-data release. Importing it "
                       "would produce a match that cannot be analysed.",
        })
    try:
        return commit_fixture(scope, fixture, own_team_id=own_team_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Team not found")


def _enrich(fixture, match_id: int, competition_id: int | None,
            season_id: int | None) -> None:
    """Attach competition, season and date, which the event feed does not carry."""
    if competition_id is None or season_id is None:
        return
    for meta in statsbomb.matches(competition_id, season_id):
        if meta["match_id"] == match_id:
            statsbomb.enrich_fixture(fixture, meta)
            return


# --- Shared ----------------------------------------------------------------

async def _read(file: UploadFile) -> bytes:
    raw = await file.read()
    if len(raw) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=413,
                            detail=f"File exceeds the {MAX_FILE_MB} MB limit")
    if not raw:
        raise HTTPException(status_code=422, detail="The file is empty")
    return raw
