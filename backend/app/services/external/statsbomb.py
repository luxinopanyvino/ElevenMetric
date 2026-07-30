"""StatsBomb open-data reader — real competitions, fixtures, lineups and events.

**What this source is.** Free, publicly released data about matches that
actually happened. It is the opposite of the SoFIFA source in every way that
matters: real observations rather than a game's ratings, and *no player ratings
at all*. A lineup names eleven real people and grades none of them, which is
why `Player.overall_rating` is nullable — see `app/models/catalog.py`.

**Attribution.** StatsBomb's open-data user agreement requires attribution.
Every row imported here carries it in its provenance record, and the UI shows it
wherever StatsBomb-derived output appears.

**Optional.** `statsbombpy` is not a runtime dependency. Without it this module
reports the source unavailable with the install command, the API starts
normally, and every other route behaves identically.

Coordinates are left in StatsBomb's own 120x80 frame here and converted at
commit time through `analytics.pitch.to_metres`, which already knows that frame.
Converting once, at the boundary, is why this adapter needs no geometry code.
"""

from __future__ import annotations

import math
from datetime import datetime

from app.services.external.base import (
    FetchError,
    SourceCapability,
    SourceEvent,
    SourceFixture,
    SourcePlayer,
    SourceUnavailable,
    provenance,
)

SOURCE = "statsbomb"
EDITION = "open data"
FRAME = "statsbomb"
ATTRIBUTION = "Data provided by StatsBomb (open data)."
PROVENANCE_NOTE = (
    "Real match data from StatsBomb's free open-data release. Publishes no "
    "player ratings — imported players are stored unrated rather than guessed at."
)
INSTALL_HINT = "pip install -r backend/requirements-external.txt"


def _sb():
    """Import `statsbombpy`, or explain precisely why the source is off."""
    try:
        from statsbombpy import sb
    except ImportError as exc:
        raise SourceUnavailable(
            SOURCE,
            "the statsbombpy package is not installed",
            f"Run `{INSTALL_HINT}` to enable it.",
        ) from exc
    return sb


def available() -> tuple[bool, str, str]:
    try:
        _sb()
    except SourceUnavailable as exc:
        return False, exc.reason, exc.remedy
    return True, "", ""


def capability() -> SourceCapability:
    ok, reason, remedy = available()
    return SourceCapability(
        key=SOURCE,
        label="StatsBomb — real match data (open data)",
        what_it_is=(
            "Competitions, fixtures, lineups and on-ball events from matches "
            "that actually happened, released free by StatsBomb. Real "
            "observations — and no player ratings whatsoever, so players "
            "imported from here arrive ungraded."
        ),
        tier=2,
        supplies=["competitions and seasons", "fixtures with dates and scores",
                  "both starting lineups", "one row per on-ball action with "
                  "coordinates", "the source's own shot xG, kept as the "
                  "source's"],
        does_not_supply=["player ratings or attributes", "market values",
                         "fitness or fatigue", "tracking data"],
        available=ok,
        reason=reason,
        remedy=remedy,
        attribution=ATTRIBUTION,
        terms_url="https://github.com/statsbomb/open-data/blob/master/LICENSE.pdf",
    )


# --- Vocabulary mapping ----------------------------------------------------

#: StatsBomb event type → this product's type vocabulary (see `MatchEvent`).
#: Types with no equivalent — "Starting XI", "Half Start", "Tactical Shift" —
#: are absent on purpose and their rows are skipped rather than forced into a
#: nearby bucket.
TYPE_MAP: dict[str, str] = {
    "Pass": "pass",
    "Carry": "carry",
    "Shot": "shot",
    "Dribble": "dribble",
    "Duel": "duel",
    "Interception": "interception",
    "Pressure": "pressure",
    "Clearance": "clearance",
    "Ball Recovery": "recovery",
    "Foul Committed": "foul",
    "Goal Keeper": "save",
    "Block": "tackle",
    "Substitution": "sub_off",
    "Bad Behaviour": "card",
    "Own Goal Against": "shot",
}

#: Outcomes the analytics layer counts as a success. Anything else is a failure,
#: so the mapping only has to be faithful, not exhaustive.
_SUCCESS = {"success", "complete", "completed", "goal"}


def _clean(value):
    """`NaN` → `None`. pandas fills absent cells with NaN, which is not absent."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _named(value) -> str | None:
    """StatsBomb nests every enum as ``{"id": .., "name": ..}``. Take the name."""
    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, dict):
        return _clean(value.get("name"))
    return str(value)


def _outcome_for(event: dict, mapped_type: str) -> str:
    """Faithful outcome string, defaulting to success only when the source does.

    StatsBomb records an outcome *only when something went wrong* for passes —
    an absent ``pass.outcome`` means the pass was completed. Reproducing that
    convention faithfully is what keeps this product's pass-completion figures
    equal to the source's.
    """
    if mapped_type == "pass":
        outcome = _named((event.get("pass") or {}).get("outcome"))
        return "success" if outcome is None else outcome.lower()
    if mapped_type == "shot":
        outcome = _named((event.get("shot") or {}).get("outcome"))
        return "unknown" if outcome is None else outcome.lower()
    if mapped_type == "dribble":
        outcome = _named((event.get("dribble") or {}).get("outcome")) or ""
        return "success" if outcome.lower() == "complete" else "incomplete"
    if mapped_type == "duel":
        outcome = _named((event.get("duel") or {}).get("outcome"))
        if outcome is None:
            return "unknown"
        return "success" if outcome.lower().startswith(("won", "success")) else outcome.lower()
    if mapped_type == "interception":
        outcome = _named((event.get("interception") or {}).get("outcome"))
        if outcome is None:
            return "success"
        return "success" if outcome.lower().startswith(("won", "success")) else outcome.lower()
    if mapped_type == "save":
        outcome = _named((event.get("goalkeeper") or {}).get("outcome"))
        return "unknown" if outcome is None else outcome.lower()
    return "success"


def _location(value) -> tuple[float | None, float | None]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None, None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None, None


def formation_name(code) -> str:
    """StatsBomb's ``433`` → ``"4-3-3"``. Anything unrecognised stays absent."""
    digits = str(_clean(code) or "").strip().split(".")[0]
    if not digits.isdigit() or not 3 <= len(digits) <= 5:
        return ""
    return "-".join(digits)


# --- Browsing --------------------------------------------------------------

def competitions() -> list[dict]:
    """Every competition/season pair the open data publishes."""
    sb = _sb()
    try:
        frame = sb.competitions()
    except Exception as exc:                     # noqa: BLE001 - third-party surface
        raise FetchError(SOURCE, "statsbombpy.competitions()",
                         "the open-data competition index", str(exc)) from exc
    return [
        {
            "competition_id": int(row["competition_id"]),
            "season_id": int(row["season_id"]),
            "country": row.get("country_name"),
            "competition": row.get("competition_name"),
            "season": row.get("season_name"),
            "gender": row.get("competition_gender"),
        }
        for _, row in frame.iterrows()
    ]


def matches(competition_id: int, season_id: int) -> list[dict]:
    """Fixtures in a competition season. Reads nothing into the database."""
    sb = _sb()
    try:
        frame = sb.matches(competition_id=competition_id, season_id=season_id)
    except Exception as exc:                     # noqa: BLE001
        raise FetchError(SOURCE,
                         f"statsbombpy.matches({competition_id}, {season_id})",
                         "a fixture list", str(exc)) from exc
    out = []
    for _, row in frame.iterrows():
        out.append({
            "match_id": int(row["match_id"]),
            "date": str(row.get("match_date") or ""),
            "kick_off": str(_clean(row.get("kick_off")) or ""),
            "competition": row.get("competition_name") or row.get("competition"),
            "season": str(row.get("season_name") or row.get("season") or ""),
            "home": row.get("home_team"),
            "away": row.get("away_team"),
            "home_score": int(row.get("home_score") or 0),
            "away_score": int(row.get("away_score") or 0),
            "stage": _clean(row.get("competition_stage")),
        })
    out.sort(key=lambda m: m["date"])
    return out


# --- Fixture import --------------------------------------------------------

def fetch_fixture(match_id: int, *, with_events: bool = True) -> SourceFixture:
    """Read one fixture — both lineups and the event feed — from the open data.

    Asks `statsbombpy` for the raw open-data JSON (``fmt="dict"``) rather than
    its flattened DataFrame, so the live path and the file path
    (:func:`load_fixture_from_json`) run through exactly the same parser. One
    parser means the offline tests genuinely cover the live behaviour.
    """
    sb = _sb()

    try:
        raw_lineups = sb.lineups(match_id=match_id, fmt="dict")
    except Exception as exc:                     # noqa: BLE001 - third-party surface
        raise FetchError(SOURCE, f"statsbombpy.lineups({match_id})",
                         "two team lineups", str(exc)) from exc

    raw_events: list[dict] = []
    if with_events:
        try:
            events_by_id = sb.events(match_id=match_id, fmt="dict")
        except Exception as exc:                 # noqa: BLE001
            raise FetchError(SOURCE, f"statsbombpy.events({match_id})",
                             "an event feed", str(exc)) from exc
        raw_events = sorted(events_by_id.values(), key=lambda e: e.get("index", 0))

    return load_fixture_from_json(
        lineups=list(raw_lineups.values()),
        events=raw_events,
        match_id=match_id,
        retrieved="fetch",
    )


def load_fixture_from_json(*, lineups: list[dict], events: list[dict],
                           match_id: int | str,
                           retrieved: str = "file") -> SourceFixture:
    """Build a fixture from StatsBomb open-data JSON.

    Accepts exactly what the public ``open-data`` repository publishes —
    ``lineups/<match_id>.json`` and ``events/<match_id>.json`` — so a user with
    a local checkout can import without the package, and the tests can run with
    no network at all.
    """
    parsed_lineups: dict[str, list[SourcePlayer]] = {}
    for team in lineups:
        name = str(team.get("team_name") or team.get("team", {}).get("name") or "")
        if not name:
            continue
        parsed_lineups[name] = [_player_from_lineup(row)
                                for row in team.get("lineup", [])]

    if len(parsed_lineups) < 2:
        raise FetchError(
            SOURCE, f"lineups for match {match_id}", "two team lineups",
            f"got {len(parsed_lineups)} — this fixture may be metadata-only in "
            "the open-data release")

    parsed_events = parse_events(events)
    formations = _formations(events)

    home, away = list(parsed_lineups)[0], list(parsed_lineups)[1]
    record = provenance(source=SOURCE, edition=EDITION, source_id=str(match_id),
                        source_url=("https://github.com/statsbomb/open-data/blob/"
                                    f"master/data/events/{match_id}.json"),
                        retrieved=retrieved, note=PROVENANCE_NOTE)
    record["attribution"] = ATTRIBUTION
    if formations:
        record["formations"] = formations

    fixture = SourceFixture(
        source_id=str(match_id),
        competition="", season="", kickoff=None,
        home=home, away=away,
        lineups=parsed_lineups, events=parsed_events, frame=FRAME,
        provenance=record,
    )
    return fixture


def _player_from_lineup(row: dict) -> SourcePlayer:
    positions = row.get("positions") or []
    position_raw = ""
    if positions:
        position_raw = str(positions[0].get("position") or "")
    nickname = _clean(row.get("player_nickname"))
    country = row.get("country")
    return SourcePlayer(
        source_id=str(row.get("player_id") or ""),
        name=str(row.get("player_name") or ""),
        known_as=str(nickname) if nickname else "",
        position_raw=position_raw,
        shirt_number=(int(row["jersey_number"])
                      if _clean(row.get("jersey_number")) is not None else None),
        nationality=_named(country),
        # Left as None on purpose: StatsBomb publishes no ratings, and a
        # placeholder here would be the product inventing a measurement.
        overall=None,
        potential=None,
    )


def _formations(events: list[dict]) -> dict[str, str]:
    """Each side's starting shape, from the ``Starting XI`` events."""
    out: dict[str, str] = {}
    for event in events:
        if _named(event.get("type")) != "Starting XI":
            continue
        team = _named(event.get("team"))
        shape = formation_name((event.get("tactics") or {}).get("formation"))
        if team and shape:
            out[team] = shape
    return out


def parse_events(events: list[dict]) -> list[SourceEvent]:
    """Open-data event JSON → `SourceEvent`, skipping types with no equivalent.

    Types this product has no bucket for — ``Starting XI``, ``Half Start``,
    ``Tactical Shift`` — are dropped rather than forced into a nearby one. An
    event filed under the wrong type is worse than an event that is absent,
    because the analytics layer will happily count it.
    """
    out: list[SourceEvent] = []
    for event in events:
        raw_type = _named(event.get("type")) or ""
        mapped = TYPE_MAP.get(raw_type)
        if mapped is None:
            continue

        x, y = _location(event.get("location"))
        if x is None or y is None:
            continue

        end_x = end_y = None
        for group in ("pass", "carry", "shot"):
            if (value := (event.get(group) or {}).get("end_location")) is not None:
                end_x, end_y = _location(value)
                if end_x is not None:
                    break

        qualifiers: dict = {"source_type": raw_type}
        if source_id := _clean(event.get("id")):
            qualifiers["source_event_id"] = str(source_id)
        if pattern := _named(event.get("play_pattern")):
            qualifiers["play_pattern"] = pattern
        if event.get("under_pressure"):
            qualifiers["under_pressure"] = True
        if timestamp := _clean(event.get("timestamp")):
            qualifiers["source_timestamp"] = str(timestamp)

        detail = event.get(mapped_group := _GROUP_FOR.get(mapped, "")) or {}
        if mapped_group:
            if body_part := _named(detail.get("body_part")):
                qualifiers["body_part"] = body_part
            if height := _named(detail.get("height")):
                qualifiers["pass_height"] = height
            if situation := _named(detail.get("type")):
                qualifiers["situation"] = situation
        # The source's own xG, kept under the source's name. `MatchEvent.xg` is
        # this product's model's output and is never filled from someone else's.
        if (xg := _clean((event.get("shot") or {}).get("statsbomb_xg"))) is not None:
            qualifiers["source_xg"] = round(float(xg), 4)

        player = _named(event.get("player"))
        out.append(SourceEvent(
            source_id=str(_clean(event.get("id")) or len(out)),
            team=_named(event.get("team")) or "",
            player=player,
            period=int(_clean(event.get("period")) or 1),
            minute=int(_clean(event.get("minute")) or 0),
            second=int(_clean(event.get("second")) or 0),
            type=mapped,
            outcome=_outcome_for(event, mapped),
            x=x, y=y, end_x=end_x, end_y=end_y,
            qualifiers=qualifiers,
        ))

        # A substitution is one row in the source and two facts here: someone
        # left the pitch and someone else came on.
        if mapped == "sub_off":
            replacement = _named((event.get("substitution") or {}).get("replacement"))
            if replacement:
                out.append(SourceEvent(
                    source_id=f"{out[-1].source_id}-on",
                    team=out[-1].team, player=replacement,
                    period=out[-1].period, minute=out[-1].minute,
                    second=out[-1].second, type="sub_on", outcome="success",
                    x=x, y=y,
                    qualifiers={"source_type": raw_type, "replaces": player},
                ))
    return out


#: Our type → the JSON group its detail lives under.
_GROUP_FOR = {
    "pass": "pass", "shot": "shot", "dribble": "dribble", "duel": "duel",
    "save": "goalkeeper", "foul": "foul_committed", "interception": "interception",
    "recovery": "ball_recovery", "clearance": "clearance",
}


def enrich_fixture(fixture: SourceFixture, meta: dict) -> SourceFixture:
    """Attach competition/season/date from a fixture row to a fetched fixture."""
    fixture.competition = str(meta.get("competition") or "")
    fixture.season = str(meta.get("season") or "")
    fixture.home = str(meta.get("home") or fixture.home)
    fixture.away = str(meta.get("away") or fixture.away)
    fixture.score = (int(meta.get("home_score") or 0), int(meta.get("away_score") or 0))
    if raw_date := str(meta.get("date") or ""):
        try:
            fixture.kickoff = datetime.fromisoformat(raw_date)
        except ValueError:
            fixture.kickoff = None
    fixture.provenance["competition"] = fixture.competition
    fixture.provenance["season"] = fixture.season
    return fixture
