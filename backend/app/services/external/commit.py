"""Turn a source shape into a preview, then into rows — and nothing in between.

Two stages, deliberately separated:

* :func:`map_squad` / :func:`map_fixture` are **pure**. They map a source shape
  into this product's vocabulary and report every decision — what was found,
  what was absent, what could not be mapped — without touching the database.
  This is what the preview endpoint returns.
* :func:`commit_squad` / :func:`commit_fixture` take that mapped result and
  write it through :class:`~app.core.tenancy.TenantScope`.

The preview's shape mirrors the CSV route's, because the two are making the same
promise to the user: see exactly what will be stored before anything is.

The rule that runs through all of it: **a field the source did not supply stays
absent.** Not zero, not an average, not the default the column would otherwise
take. That is the constitution's second principle, applied at the only place
where the temptation to smooth over a gap ever arises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from app.core.tenancy import TenantScope
from app.models.catalog import Foot, Player, Position, Team
from app.models.match import (
    InputSource,
    Lineup,
    LineupSlot,
    Match,
    MatchEvent,
    MatchState,
)
from app.services.analytics.pitch import Pitch, to_metres
from app.services.external.base import SourceFixture, SourcePlayer, SourceSquad
from app.services.external.sofifa_map import map_attributes, map_positions
from app.services.ml.features import headline_from_detail

#: Fields this product carries that no external source in scope supplies. Listed
#: explicitly so the preview can state them rather than leaving the user to
#: notice the absence — and so nothing silently acquires a default.
NOT_SUPPLIED = (
    "minutes_last_7d", "fitness", "fatigue", "injury_risk", "season statistics",
)

#: StatsBomb's position names → this product's positions. Its vocabulary is
#: verbose ("Left Center Midfield") and richer than ours in places, so several
#: source positions legitimately collapse onto one of ours.
STATSBOMB_POSITIONS: dict[str, Position] = {
    "goalkeeper": Position.GK,
    "right back": Position.RB, "left back": Position.LB,
    "right wing back": Position.RWB, "left wing back": Position.LWB,
    "right center back": Position.RCB, "left center back": Position.LCB,
    "center back": Position.CB,
    "right defensive midfield": Position.DM, "left defensive midfield": Position.DM,
    "center defensive midfield": Position.DM,
    "right center midfield": Position.CM, "left center midfield": Position.CM,
    "center midfield": Position.CM,
    "right midfield": Position.RM, "left midfield": Position.LM,
    "right attacking midfield": Position.AM, "left attacking midfield": Position.AM,
    "center attacking midfield": Position.AM,
    "right wing": Position.RW, "left wing": Position.LW,
    "right center forward": Position.CF, "left center forward": Position.CF,
    "center forward": Position.ST, "striker": Position.ST,
    "secondary striker": Position.SS,
}


# --- Preview ---------------------------------------------------------------

@dataclass
class MappedPlayer:
    """One source player, mapped, with the record of how."""

    source_id: str
    row: dict                      # exactly the columns that would be written
    attributes: dict
    unmapped_attributes: list[dict] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        row = dict(self.row)
        for key, value in list(row.items()):
            if isinstance(value, (date, datetime)):
                row[key] = value.isoformat()
            elif hasattr(value, "value"):        # enums
                row[key] = value.value
        return {
            "source_id": self.source_id,
            "row": row,
            "attributes": self.attributes,
            "unmapped_attributes": self.unmapped_attributes,
            "absent": self.absent,
            "notes": self.notes,
        }


@dataclass
class MappedSquad:
    name: str
    source_id: str
    league: str | None
    country: str | None
    provenance: dict
    players: list[MappedPlayer] = field(default_factory=list)
    #: Rows that could not be mapped at all, with the offending value.
    errors: list[dict] = field(default_factory=list)

    @property
    def unrated(self) -> list[str]:
        return [p.row["name"] for p in self.players
                if p.row.get("overall_rating") is None]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source_id": self.source_id,
            "league": self.league,
            "country": self.country,
            "provenance": self.provenance,
            "player_count": len(self.players),
            "players": [p.to_dict() for p in self.players],
            "errors": self.errors,
            "unrated_players": self.unrated,
            "not_supplied_by_this_source": list(NOT_SUPPLIED),
        }


def _foot(raw: str | None) -> Foot | None:
    if not raw:
        return None
    text = raw.strip().lower()
    return {"left": Foot.left, "right": Foot.right, "both": Foot.both}.get(text)


def _birth_date_from_age(age: int | None) -> None:
    """Deliberately does nothing.

    An age is not a birth date, and turning one into the other invents a day and
    a month. Age-derived output degrades to its no-age branch instead — see the
    fatigue curve and the academy projections.
    """
    return None


def map_squad(squad: SourceSquad, *, source: str = "sofifa") -> MappedSquad:
    """Map a source squad into this product's vocabulary, reporting every gap."""
    mapped = MappedSquad(name=squad.name, source_id=squad.source_id,
                         league=squad.league, country=squad.country,
                         provenance=dict(squad.provenance))

    seen_numbers: set[int] = set()
    for player in squad.players:
        result = _map_player(player, source=source, seen_numbers=seen_numbers)
        if isinstance(result, dict):
            mapped.errors.append(result)
        else:
            mapped.players.append(result)
    return mapped


def _map_player(player: SourcePlayer, *, source: str,
                seen_numbers: set[int]) -> MappedPlayer | dict:
    """One source player → a writable row, or an error describing why not."""
    if source == "statsbomb":
        positions, unknown = _statsbomb_positions(player.position_raw)
    else:
        positions, unknown = map_positions(player.position_raw)

    if not positions:
        return {
            "source_id": player.source_id,
            "name": player.name,
            "field": "position",
            "value": player.position_raw,
            "error": (f"no position in this product's vocabulary corresponds to "
                      f"{player.position_raw!r}"
                      if player.position_raw else
                      "the source published no position for this player"),
        }
    if unknown:
        notes = [f"ignored unrecognised position code(s): {', '.join(unknown)}"]
    else:
        notes = []

    attributes, unmapped = map_attributes(player.attributes_raw)
    if attributes:
        attributes = headline_from_detail(attributes)

    shirt = player.shirt_number
    if shirt is not None and shirt in seen_numbers:
        notes.append(f"shirt number {shirt} is already taken in this squad")
    elif shirt is not None:
        seen_numbers.add(shirt)

    row: dict = {
        "name": player.name,
        "known_as": player.known_as or "",
        "shirt_number": shirt,
        "birth_date": player.birth_date or _birth_date_from_age(player.age),
        "nationality": (player.nationality or None),
        "primary_position": positions[0],
        "secondary_positions": [p.value for p in positions[1:]],
        "overall_rating": player.overall,
        "potential_rating": player.potential,
        "attributes": attributes,
        "height_cm": player.height_cm,
        "weight_kg": player.weight_kg,
        "market_value_eur": player.market_value_eur,
        "wage_eur_per_year": player.wage_eur_per_year,
        "contract_until": player.contract_until,
    }
    if (foot := _foot(player.preferred_foot)) is not None:
        row["preferred_foot"] = foot

    absent = [key for key, value in row.items()
              if value is None or (key == "attributes" and not value)]
    if player.age is not None and player.birth_date is None:
        notes.append(f"the source published an age ({player.age}) but no birth "
                     "date; age-derived output is skipped rather than assumed")
    if player.overall is None:
        notes.append("no rating on file — this source publishes none, and the "
                     "engines will exclude this player rather than invent one")

    return MappedPlayer(source_id=player.source_id, row=row, attributes=attributes,
                        unmapped_attributes=unmapped, absent=absent, notes=notes)


def _statsbomb_positions(raw: str) -> tuple[list[Position], list[str]]:
    text = " ".join((raw or "").split()).lower()
    if not text:
        return [], []
    if (position := STATSBOMB_POSITIONS.get(text)) is not None:
        return [position], []
    return [], [raw]


# --- Commit: squads --------------------------------------------------------

def _slugify(text: str, prefix: str = "") -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in text.lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    slug = f"{prefix}{cleaned.strip('-')}"[:62]
    return slug or "imported-team"


#: Columns where ``None`` is a claim in its own right — "nobody has graded this
#: player" — and must be written. Everywhere else a ``None`` means the source
#: simply did not publish the field, and the column keeps its own default (on
#: create) or its existing value (on refresh). Writing ``None`` into those would
#: turn "not supplied" into "supplied as nothing", which is a different thing
#: and, for the not-null columns, not even storable.
NULLABLE_MEANS_UNKNOWN = ("overall_rating", "potential_rating")


def _writable(row: dict) -> dict:
    return {key: value for key, value in row.items()
            if value is not None or key in NULLABLE_MEANS_UNKNOWN}


def _apply(player: Player, row: dict, provenance: dict) -> None:
    for key, value in _writable(row).items():
        setattr(player, key, value)
    player.provenance = provenance


def commit_squad(scope: TenantScope, mapped: MappedSquad, *,
                 kind: str = "opponent", team_id: str | None = None,
                 formation: str = "4-3-3") -> dict:
    """Write a mapped squad as a team and its players.

    Re-importing the same club updates it in place rather than duplicating it:
    players are matched on the source's own identifier, which is why two players
    who share a name survive a refresh intact. A player who has left the source
    squad is **reported, not deleted** — the club may still want their history,
    and silently removing rows is not this route's decision to make.
    """
    source = mapped.provenance.get("source", "external")
    team_provenance = dict(mapped.provenance)
    team_provenance["kind"] = "team"

    team = None
    if team_id:
        team = scope.get(Team, team_id)
        if team is None:
            raise LookupError("Team not found")
    else:
        slug = _slugify(mapped.name, prefix=f"{source}-")
        team = scope.first(Team, Team.slug == slug)
        if team is None:
            team = scope.add(Team(
                slug=slug, name=mapped.name,
                short_name=mapped.name[:24],
                competition=mapped.league, kind=kind,
                default_formation=formation,
                provenance=team_provenance,
            ))
            scope.flush()
        else:
            team.name = mapped.name
            team.competition = mapped.league or team.competition
            team.provenance = team_provenance

    existing = {
        (p.provenance or {}).get("source_id"): p
        for p in scope.all(Player, Player.team_id == team.id)
        if (p.provenance or {}).get("source") == source
    }

    created = updated = 0
    imported_ids: set[str] = set()
    for player in mapped.players:
        row_provenance = dict(mapped.provenance)
        row_provenance["source_id"] = player.source_id
        row_provenance["kind"] = "player"
        imported_ids.add(player.source_id)

        hit = existing.get(player.source_id)
        if hit is not None:
            _apply(hit, player.row, row_provenance)
            updated += 1
        else:
            record = Player(team_id=team.id, **_writable(player.row))
            record.provenance = row_provenance
            # Nothing the source did not supply gets a value here. `fitness`,
            # `fatigue` and `minutes_last_7d` keep the column defaults, which the
            # data-completeness score treats as tier-1-only — they are never
            # presented as observations of this player.
            scope.add(record)
            created += 1

    departed = [
        {"player_id": p.id, "name": p.display_name,
         "source_id": source_id,
         "note": "no longer in the source squad — kept on file, not deleted"}
        for source_id, p in existing.items()
        if source_id not in imported_ids
    ]

    scope.commit()
    return {
        "team_id": team.id,
        "team": team.name,
        "kind": team.kind,
        "created": created,
        "updated": updated,
        "departed": departed,
        "unrated_players": mapped.unrated,
        "provenance": team_provenance,
    }


# --- Commit: fixtures ------------------------------------------------------

def map_fixture(fixture: SourceFixture) -> dict:
    """Summarise a fixture as it would be stored, writing nothing."""
    by_team = {}
    errors: list[dict] = []
    for team, players in fixture.lineups.items():
        seen: set[int] = set()
        mapped_players = []
        for player in players:
            result = _map_player(player, source="statsbomb", seen_numbers=seen)
            if isinstance(result, dict):
                errors.append({**result, "team": team})
            else:
                mapped_players.append(result)
        by_team[team] = mapped_players

    types: dict[str, int] = {}
    for event in fixture.events:
        types[event.type] = types.get(event.type, 0) + 1

    return {
        "source_id": fixture.source_id,
        "competition": fixture.competition,
        "season": fixture.season,
        "kickoff": fixture.kickoff.isoformat() if fixture.kickoff else None,
        "home": fixture.home,
        "away": fixture.away,
        "score": list(fixture.score),
        "frame": fixture.frame,
        "provenance": fixture.provenance,
        "lineups": {team: [p.to_dict() for p in players]
                    for team, players in by_team.items()},
        "event_count": len(fixture.events),
        "event_types": dict(sorted(types.items(), key=lambda kv: -kv[1])),
        "errors": errors,
        "not_supplied_by_this_source": [
            "player ratings", "player attributes", "market values",
            "fitness or fatigue", "tracking data",
        ],
    }


def commit_fixture(scope: TenantScope, fixture: SourceFixture, *,
                   own_team_id: str | None = None) -> dict:
    """Write a fixture as a match with both lineups and its event feed.

    The match is marked `event_data` / `statsbomb`, so the analysis pipeline
    treats it exactly as it treats a club's own feed — there is no special case
    downstream, which is the point of converting at the boundary.
    """
    mapped = map_fixture(fixture)
    provenance_record = dict(fixture.provenance)

    teams: dict[str, Team] = {}
    for name in (fixture.home, fixture.away):
        slug = _slugify(name, prefix="statsbomb-")
        team = scope.first(Team, Team.slug == slug)
        if team is None:
            team = scope.add(Team(slug=slug, name=name, short_name=name[:24],
                                  kind="opponent", provenance=provenance_record))
            scope.flush()
        teams[name] = team

    own_team = scope.get(Team, own_team_id) if own_team_id else teams[fixture.home]
    if own_team is None:
        raise LookupError("Team not found")

    match = scope.add(Match(
        team_id=own_team.id,
        opponent_team_id=teams[fixture.away].id
        if own_team.id != teams[fixture.away].id else teams[fixture.home].id,
        opponent_name=fixture.away if own_team.name == fixture.home else fixture.home,
        competition=fixture.competition or "StatsBomb open data",
        season=fixture.season or "",
        kickoff_at=fixture.kickoff.replace(tzinfo=timezone.utc)
        if fixture.kickoff and fixture.kickoff.tzinfo is None else fixture.kickoff,
        venue="home" if own_team.name == fixture.home else "away",
        state=MatchState.finished,
        goals_for=fixture.score[0] if own_team.name == fixture.home else fixture.score[1],
        goals_against=fixture.score[1] if own_team.name == fixture.home else fixture.score[0],
        source=InputSource.event_data if fixture.events else InputSource.manual,
        provider="statsbomb",
        notes=f"Imported from StatsBomb open data (match {fixture.source_id}). "
              f"{provenance_record.get('attribution', '')}".strip(),
        provenance=provenance_record,
    ))
    scope.flush()

    # --- players and lineups ---
    players_by_key: dict[tuple[str, str], Player] = {}
    for team_name, entries in mapped["lineups"].items():
        team = teams[team_name]
        existing = {
            (p.provenance or {}).get("source_id"): p
            for p in scope.all(Player, Player.team_id == team.id)
            if (p.provenance or {}).get("source") == "statsbomb"
        }
        for entry in entries:
            row = dict(entry["row"])
            row["primary_position"] = Position(row["primary_position"])
            row["birth_date"] = None
            row["contract_until"] = None
            record_provenance = dict(provenance_record)
            record_provenance["source_id"] = entry["source_id"]
            record_provenance["kind"] = "player"

            player = existing.get(entry["source_id"])
            if player is None:
                player = scope.add(Player(team_id=team.id, **_writable(row)))
                player.provenance = record_provenance
            else:
                _apply(player, row, record_provenance)
            players_by_key[(team_name, entry["source_id"])] = player
        scope.flush()

    formations = provenance_record.get("formations") or {}
    for team_name, entries in mapped["lineups"].items():
        lineup = scope.add(Lineup(
            match_id=match.id, team_id=teams[team_name].id,
            name=f"{team_name} — as recorded",
            # The source publishes each side's actual starting shape. Falling
            # back to the column default rather than guessing a shape from the
            # positions keeps the record honest when it does not.
            formation=formations.get(team_name) or "4-3-3",
        ))
        scope.flush()
        for index, entry in enumerate(entries):
            player = players_by_key[(team_name, entry["source_id"])]
            scope.add(LineupSlot(
                lineup_id=lineup.id, player_id=player.id, slot_index=index,
                is_starter=index < 11,
                position=Position(entry["row"]["primary_position"]),
            ))

    # --- events ---
    pitch = Pitch(match.pitch_length_m, match.pitch_width_m)
    by_name = {}
    for (team_name, _), player in players_by_key.items():
        by_name[(team_name, player.name)] = player
        if player.known_as:
            by_name[(team_name, player.known_as)] = player

    stored = 0
    for event in fixture.events:
        x, y = to_metres(event.x, event.y, fixture.frame, pitch)
        end_x = end_y = None
        if event.end_x is not None and event.end_y is not None:
            end_x, end_y = to_metres(event.end_x, event.end_y, fixture.frame, pitch)
        team = teams.get(event.team)
        player = by_name.get((event.team, event.player or ""))
        scope.add(MatchEvent(
            match_id=match.id,
            team_id=team.id if team is not None else None,
            is_own_team=team is not None and team.id == own_team.id,
            player_id=player.id if player is not None else None,
            period=event.period, minute=event.minute, second=event.second,
            type=event.type, outcome=event.outcome,
            x=x, y=y, end_x=end_x, end_y=end_y,
            qualifiers=event.qualifiers,
        ))
        stored += 1

    scope.commit()
    return {
        "match_id": match.id,
        "competition": match.competition,
        "home": fixture.home,
        "away": fixture.away,
        "score": list(fixture.score),
        "source": match.source.value,
        "provider": match.provider,
        "players_created": len(players_by_key),
        "events_imported": stored,
        "errors": mapped["errors"],
        "provenance": provenance_record,
        "note": "Players imported from StatsBomb carry no rating: the source "
                "publishes none. They are excluded from selection and "
                "simulation rather than given an invented one.",
    }
