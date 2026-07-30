"""External data sources — SoFIFA squads and StatsBomb fixtures.

Every test here runs **offline**. Nothing in this file makes a network request
or requires `statsbombpy` to be installed: the SoFIFA path is exercised through
a saved export and a saved page, and the StatsBomb path through open-data JSON
captured into `tests/fixtures/external/`.

That is not a convenience. The feature's contract is that a source going away —
a markup change, a blocked host, a missing package — degrades the product
visibly rather than breaking it, and a suite that needed the network could not
assert that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.tenancy import TenantContext, TenantScope
from app.models.catalog import Player, Position, Team
from app.models.match import InputSource, Lineup, Match, MatchEvent
from app.services.external import sofifa, statsbomb
from app.services.external.base import FetchError, SourceUnavailable
from app.services.external.commit import commit_fixture, commit_squad, map_fixture, map_squad
from app.services.external.sofifa_map import map_attributes, map_positions, parse_work_rate
from app.services.ml.features import is_rankable, split_rankable
from app.services.ml.lineup_optimizer import best_xi

FIXTURES = Path(__file__).parent / "fixtures" / "external"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def squad():
    return sofifa.load_squad_from_bytes(_read("sofifa_squad.csv"),
                                        filename="sofifa_squad.csv")


@pytest.fixture(scope="module")
def fixture():
    return statsbomb.load_fixture_from_json(
        lineups=_json("statsbomb_lineups.json"),
        events=_json("statsbomb_events.json"),
        match_id=8650,
    )


def _scope(db, tenant_slug: str = "demo-fc") -> TenantScope:
    from app.models.tenant import Tenant

    tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).one()
    return TenantScope(db, TenantContext(tenant_id=tenant.id, tenant_slug=tenant.slug,
                                         user_id=None, role="owner"))


# --- Mapping ---------------------------------------------------------------

def test_positions_map_exactly_and_report_what_they_cannot():
    positions, unknown = map_positions("CDM, CM")
    assert positions == [Position.DM, Position.CM]
    assert unknown == []

    positions, unknown = map_positions("SWEEPER")
    assert positions == []
    assert unknown == ["SWEEPER"]


def test_attributes_map_and_unknown_ones_are_reported_not_guessed():
    mapped, unmapped = map_attributes({
        "movement_sprint_speed": 91,
        "defending_marking_awareness": 44,
        "goalkeeping_reflexes": 12,
        "physic": 70,
        "vibes": 99,
    })
    assert mapped == {"sprint_speed": 91.0, "defensive_awareness": 44.0,
                      "gk_reflexes": 12.0, "physical": 70.0}
    assert [u["source_label"] for u in unmapped] == ["vibes"]


def test_attacking_positioning_is_reported_unmapped_not_folded_into_defending():
    """It is a different skill; folding it in would corrupt every defender."""
    mapped, unmapped = map_attributes({"mentality_positioning": 88})
    assert mapped == {}
    assert len(unmapped) == 1
    assert "attacking positioning" in unmapped[0]["reason"]


def test_work_rate_pairs_are_mapped_onto_the_products_scale():
    assert parse_work_rate("High/Medium") == {"work_rate_off": 85.0,
                                              "work_rate_def": 60.0}
    assert parse_work_rate("nonsense") == {}


# --- SoFIFA: file route ----------------------------------------------------

def test_export_file_reads_a_full_squad(squad):
    assert squad.name == "Testville FC"
    assert squad.league == "Test Premier League"
    assert len(squad.players) == 18
    assert squad.provenance["source"] == "sofifa"
    assert squad.provenance["retrieved"] == "file"
    assert squad.provenance["edition"] == sofifa.EDITION


def test_provenance_says_these_are_game_ratings_not_measurements(squad):
    note = squad.provenance["note"].lower()
    assert "ea sports fc" in note
    assert "not measurements" in note


def test_mapped_squad_fills_the_full_attribute_vocabulary(squad):
    mapped = map_squad(squad)
    assert mapped.errors == []
    assert len(mapped.players) == 18
    keeper = next(p for p in mapped.players
                  if p.row["primary_position"] is Position.GK)
    assert len(keeper.attributes) >= 40
    assert keeper.attributes["gk_reflexes"] > 0
    assert set(keeper.attributes) >= {"pace", "shooting", "passing", "dribbling",
                                      "defending", "physical"}


def test_secondary_positions_survive_the_mapping(squad):
    mapped = map_squad(squad)
    versatile = [p for p in mapped.players if p.row["secondary_positions"]]
    assert versatile, "the fixture squad has multi-position players"
    for player in versatile:
        for value in player.row["secondary_positions"]:
            Position(value)   # every stored value is a real position


def test_fields_the_source_never_supplied_are_absent_not_defaulted(squad):
    """Principle II, at the only place the temptation arises."""
    mapped = map_squad(squad)
    for player in mapped.players:
        assert "minutes_last_7d" not in player.row
        assert "fitness" not in player.row
        assert "fatigue" not in player.row
    assert "minutes_last_7d" in mapped.to_dict()["not_supplied_by_this_source"]


def test_an_unmappable_position_is_an_error_with_the_offending_value():
    csv = (b"sofifa_id,short_name,player_positions,overall\n"
           b"1,Good Player,ST,80\n"
           b"2,Odd Player,SWEEPER,80\n")
    mapped = map_squad(sofifa.load_squad_from_bytes(csv, filename="x.csv"))
    assert len(mapped.players) == 1
    assert len(mapped.errors) == 1
    assert mapped.errors[0]["value"] == "SWEEPER"
    assert mapped.errors[0]["field"] == "position"


def test_an_age_without_a_birth_date_is_never_turned_into_one():
    csv = (b"sofifa_id,short_name,player_positions,overall,age\n"
           b"1,Ageless,ST,80,27\n")
    mapped = map_squad(sofifa.load_squad_from_bytes(csv, filename="x.csv"))
    player = mapped.players[0]
    assert player.row["birth_date"] is None
    assert any("no birth date" in note for note in player.notes)


def test_a_file_with_no_usable_rows_is_refused_not_half_imported():
    with pytest.raises(FetchError) as exc:
        sofifa.load_squad_from_bytes(b"a,b,c\n1,2,3\n", filename="x.csv")
    assert "at least one row" in exc.value.expected


# --- SoFIFA: saved page route ----------------------------------------------

SAVED_PAGE = """
<html><head><title>Testville FC - EA FC 26 - sofifa</title></head><body>
<h1>Testville FC</h1>
<a href="/league/13/premier-league">Test Premier League</a>
<table>
<thead><tr><th></th><th>Name</th><th>Age</th><th>OVR</th><th>POT</th>
<th>Value</th><th>Wage</th></tr></thead>
<tbody>
<tr><td>1</td>
    <td><a href="/player/200000/tv-keeper/260002/">TV Keeper</a>
        <span class="pos pos0">GK</span></td>
    <td>28</td><td>84</td><td>85</td><td>&euro;32M</td><td>&euro;90K</td></tr>
<tr><td>9</td>
    <td><a href="/player/200013/tv-striker/260002/">TV Striker</a>
        <span class="pos pos25">ST</span><span class="pos pos24">CF</span></td>
    <td>24</td><td>88</td><td>93</td><td>&euro;120.5M</td><td>&euro;250K</td></tr>
</tbody></table></body></html>
"""


def test_a_saved_club_page_parses_the_same_way_a_live_fetch_would():
    squad = sofifa.load_squad_from_bytes(SAVED_PAGE.encode(), filename="team.html")
    assert squad.name == "Testville FC"
    assert squad.league == "Test Premier League"
    assert [p.name for p in squad.players] == ["TV Keeper", "TV Striker"]

    striker = squad.players[1]
    assert striker.source_id == "200013"
    assert striker.position_raw == "ST CF"
    assert striker.overall == 88.0
    assert striker.potential == 93.0
    assert striker.age == 24
    assert striker.market_value_eur == 120_500_000
    assert striker.wage_eur_per_year == 250_000
    assert squad.provenance["retrieved"] == "file"


def test_optional_attribute_columns_are_read_by_their_header_label():
    """SoFIFA lets a visitor switch extra attribute columns on.

    They are keyed by their own header, not by column index, so the mapper can
    resolve them — and name the ones it cannot.
    """
    page = ("<html><body><h1>Attr FC</h1><table><thead><tr>"
            "<th></th><th>Name</th><th>Age</th><th>OVR</th>"
            "<th>Sprint speed</th><th>Finishing</th><th>Vibes</th></tr></thead>"
            "<tbody><tr><td>9</td>"
            "<td><a href='/player/1/x/260002/'>A Striker</a>"
            "<span class='pos pos25'>ST</span></td>"
            "<td>24</td><td>88</td><td>93</td><td>91</td><td>77</td>"
            "</tr></tbody></table></body></html>")

    squad = sofifa.load_squad_from_bytes(page.encode(), filename="team.html")
    assert squad.players[0].attributes_raw == {
        "Sprint speed": 93.0, "Finishing": 91.0, "Vibes": 77.0}

    player = map_squad(squad).players[0]
    assert player.attributes["sprint_speed"] == 93.0
    assert player.attributes["finishing"] == 91.0
    # The headline faces are derived from the detail present, as everywhere else.
    assert player.attributes["pace"] == 93.0
    assert [u["source_label"] for u in player.unmapped_attributes] == ["Vibes"]


def test_markup_that_carries_no_players_is_refused_with_what_was_expected():
    with pytest.raises(FetchError) as exc:
        sofifa.load_squad_from_bytes(
            b"<html><body><table><tr><td>nothing</td></tr></table></body></html>",
            filename="team.html")
    assert "player" in exc.value.expected
    assert exc.value.source == "sofifa"


# --- SoFIFA: commit --------------------------------------------------------

def test_committing_a_squad_writes_a_usable_team(db, squad):
    scope = _scope(db)
    result = commit_squad(scope, map_squad(squad), kind="opponent")

    assert result["created"] == 18
    assert result["updated"] == 0
    assert result["unrated_players"] == []

    team = scope.get(Team, result["team_id"])
    assert team is not None
    assert team.provenance["source"] == "sofifa"
    assert team.provenance["edition"] == sofifa.EDITION

    players = scope.all(Player, Player.team_id == team.id)
    assert len(players) == 18
    assert all(p.provenance["source"] == "sofifa" for p in players)
    assert all(p.provenance["source_id"] for p in players)


def test_an_imported_squad_can_be_selected_from(db, squad):
    """The whole point of Story 1: the engines work on it immediately."""
    scope = _scope(db)
    result = commit_squad(scope, map_squad(squad), kind="opponent")
    players = scope.all(Player, Player.team_id == result["team_id"])

    xi = best_xi(players, "4-3-3")
    assert len([s for s in xi.slots if s.get("player_id")]) == 11
    assert xi.excluded == []
    assert xi.mean_effective_level > 0


def test_reimporting_the_same_club_refreshes_it_rather_than_duplicating(db, squad):
    scope = _scope(db)
    first = commit_squad(scope, map_squad(squad), kind="opponent")
    before = len(scope.all(Player, Player.team_id == first["team_id"]))

    second = commit_squad(scope, map_squad(squad), kind="opponent")
    after = len(scope.all(Player, Player.team_id == second["team_id"]))

    assert second["team_id"] == first["team_id"]
    assert second["updated"] == 18
    assert second["created"] == 0
    assert after == before


def test_a_player_who_left_the_source_squad_is_reported_not_deleted(db, squad):
    scope = _scope(db)
    commit_squad(scope, map_squad(squad), kind="opponent")

    trimmed = map_squad(squad)
    departing = trimmed.players.pop()
    result = commit_squad(scope, trimmed, kind="opponent")

    assert [d["source_id"] for d in result["departed"]] == [departing.source_id]
    survivors = scope.all(Player, Player.team_id == result["team_id"])
    assert any(p.provenance.get("source_id") == departing.source_id
               for p in survivors), "the departed player is kept on file"


# --- StatsBomb: parsing ----------------------------------------------------

def test_open_data_json_parses_without_the_package_or_the_network(fixture):
    assert set(fixture.lineups) == {"Belgium", "Brazil"}
    assert all(len(players) >= 11 for players in fixture.lineups.values())
    assert fixture.events
    assert fixture.frame == "statsbomb"
    assert fixture.provenance["source"] == "statsbomb"
    assert "StatsBomb" in fixture.provenance["attribution"]


def test_statsbomb_players_arrive_ungraded_rather_than_guessed_at(fixture):
    """The source publishes no ratings. Neither does this product invent them."""
    for players in fixture.lineups.values():
        for player in players:
            assert player.overall is None
            assert player.potential is None
            assert player.attributes_raw == {}


def test_event_types_map_only_where_an_equivalent_exists(fixture):
    known = {"pass", "carry", "shot", "dribble", "duel", "tackle", "interception",
             "pressure", "clearance", "recovery", "foul", "save", "sub_on",
             "sub_off", "card"}
    types = {e.type for e in fixture.events}
    assert types <= known
    assert "pass" in types and "shot" in types


def test_an_absent_pass_outcome_means_the_pass_was_completed(fixture):
    """StatsBomb only records an outcome when something went wrong."""
    passes = [e for e in fixture.events if e.type == "pass"]
    completed = [e for e in passes if e.outcome == "success"]
    assert 0.6 < len(completed) / len(passes) < 0.98


def test_the_sources_xg_is_kept_as_the_sources(fixture):
    shots = [e for e in fixture.events if e.type == "shot"]
    assert shots
    assert any("source_xg" in e.qualifiers for e in shots)
    # `MatchEvent.xg` belongs to this product's model and is never filled from
    # someone else's — the source's number lives under its own name.
    assert all("xg" not in e.qualifiers for e in shots)


def test_coordinates_stay_in_the_sources_frame_until_commit(fixture):
    assert all(0 <= e.x <= 120 and 0 <= e.y <= 80 for e in fixture.events)


def test_starting_formations_are_read_rather_than_assumed(fixture):
    formations = fixture.provenance.get("formations")
    assert formations
    assert all("-" in shape for shape in formations.values())


def test_a_fixture_with_one_lineup_is_refused():
    with pytest.raises(FetchError) as exc:
        statsbomb.load_fixture_from_json(
            lineups=[{"team_name": "Only One", "lineup": []}],
            events=[], match_id=1)
    assert "two team lineups" in exc.value.expected


# --- StatsBomb: commit -----------------------------------------------------

def test_committing_a_fixture_produces_a_tier_two_match(db, fixture):
    scope = _scope(db)
    meta = _json("statsbomb_match.json")
    statsbomb.enrich_fixture(fixture, meta)
    result = commit_fixture(scope, fixture)

    match = scope.get(Match, result["match_id"])
    assert match is not None
    assert match.source is InputSource.event_data
    assert match.provider == "statsbomb"
    assert match.competition == "FIFA World Cup"
    assert match.provenance["source"] == "statsbomb"

    events = scope.all(MatchEvent, MatchEvent.match_id == match.id)
    assert len(events) == len(fixture.events)
    # Converted out of the 120x80 frame into metres on the way in.
    assert all(0 <= e.x <= match.pitch_length_m for e in events)
    assert all(0 <= e.y <= match.pitch_width_m for e in events)

    lineups = scope.all(Lineup, Lineup.match_id == match.id)
    assert len(lineups) == 2
    assert all(lineup.slots for lineup in lineups)


def test_imported_lineup_players_are_stored_unrated(db, fixture):
    scope = _scope(db)
    result = commit_fixture(scope, fixture)
    match = scope.get(Match, result["match_id"])
    lineup = scope.all(Lineup, Lineup.match_id == match.id)[0]

    players = [scope.get(Player, slot.player_id) for slot in lineup.slots]
    assert players
    assert all(p.overall_rating is None for p in players)
    assert all(p.attributes == {} for p in players)
    assert all(not is_rankable(p) for p in players)


def test_ungraded_players_are_excluded_from_selection_with_a_reason(db, fixture):
    scope = _scope(db)
    result = commit_fixture(scope, fixture)
    match = scope.get(Match, result["match_id"])
    lineup = scope.all(Lineup, Lineup.match_id == match.id)[0]
    players = [scope.get(Player, slot.player_id) for slot in lineup.slots]

    xi = best_xi(players, "4-3-3")
    assert xi.excluded, "every one of them is ungraded"
    assert len(xi.excluded) == len(players)
    assert all("no rating" in entry["reason"] for entry in xi.excluded)
    assert all(slot.get("player_id") is None for slot in xi.slots)


def test_an_imported_fixture_analyses_at_tier_two_and_no_higher(client, auth, db, fixture):
    """The point of Story 2, and the honesty rule that bounds it.

    An imported event feed must produce a real tier-2 report — and must leave
    every tracking-only field `null` rather than inferring it from events.
    """
    scope = _scope(db)
    statsbomb.enrich_fixture(fixture, _json("statsbomb_match.json"))
    result = commit_fixture(scope, fixture)

    response = client.post("/api/v1/analysis/match", headers=auth,
                           json={"match_id": result["match_id"]})
    assert response.status_code == 200, response.text
    report = response.json()

    assert "event_data" in report["inputs_used"]
    assert "lineup" in report["inputs_used"]
    possession = report["possession"]
    assert possession, "an event feed must produce possession metrics"
    assert possession.get("pass_possession_pct") is not None
    assert possession.get("field_tilt_pct") is not None
    assert possession.get("ppda") is not None
    assert report["tactics"]

    # The report is labelled as derived from events, not from tracking.
    assert possession["method"] == "events"
    assert "tracking" not in report["inputs_used"]

    # Tier 2 is not tier 3 or 4: completeness reflects what was actually
    # supplied, and confidence is scaled by it. This import must not raise
    # either for tiers StatsBomb open data does not provide.
    assert 0 < report["data_completeness"] < 1
    assert 0 < report["confidence"] < 1


def test_the_products_own_xg_column_is_left_for_the_products_own_model(db, fixture):
    scope = _scope(db)
    result = commit_fixture(scope, fixture)
    events = scope.all(MatchEvent, MatchEvent.match_id == result["match_id"])
    shots = [e for e in events if e.type == "shot"]
    assert shots
    assert all(e.xg is None for e in shots)
    assert any("source_xg" in (e.qualifiers or {}) for e in shots)


def test_the_simulator_tells_the_three_kinds_of_opponent_apart(client, auth, db, squad):
    """A squad on file, an imported real squad, and a generated stand-in are
    three different claims about what was played. The response says which."""
    scope = _scope(db)
    imported = commit_squad(scope, map_squad(squad), kind="opponent")
    own = client.get("/api/v1/teams", headers=auth).json()
    own_id = next(t["id"] for t in own if t["kind"] == "first_team")

    against_import = client.post("/api/v1/simulation/run", headers=auth, json={
        "home_team_id": own_id, "away_team_id": imported["team_id"],
        "away_formation": "4-3-3", "minutes": 90, "persist": False,
        "playback_hz": 0.2,
    })
    assert against_import.status_code == 200, against_import.text
    body = against_import.json()
    assert body["opponent_origin"] == "imported"
    assert body["opponent_is_synthetic"] is False
    assert body["opponent_provenance"]["source"] == "sofifa"
    assert "sofifa" in body["note"]

    generated = client.post("/api/v1/simulation/run", headers=auth, json={
        "home_team_id": own_id, "minutes": 90, "persist": False, "playback_hz": 0.2,
    })
    assert generated.json()["opponent_origin"] == "generated"
    assert generated.json()["opponent_is_synthetic"] is True


# --- Honest degradation ----------------------------------------------------

def test_sources_report_themselves_with_reason_and_remedy():
    from app.services.external import capabilities

    reported = {source["key"]: source for source in capabilities()}
    assert set(reported) == {"sofifa", "statsbomb"}
    for source in reported.values():
        assert source["what_it_is"]
        assert source["supplies"] and source["does_not_supply"]
        if not source["available"]:
            assert source["reason"] and source["remedy"]

    # The two sources must never be described as interchangeable.
    assert "ratings" in reported["sofifa"]["supplies"][3]
    assert "player ratings or attributes" in reported["statsbomb"]["does_not_supply"]


def test_statsbomb_reports_the_install_command_when_the_package_is_missing(monkeypatch):
    def _no_package():
        raise SourceUnavailable("statsbomb", "the statsbombpy package is not "
                                "installed", f"Run `{statsbomb.INSTALL_HINT}`.")

    monkeypatch.setattr(statsbomb, "_sb", _no_package)
    capability = statsbomb.capability()
    assert capability.available is False
    assert "statsbombpy" in capability.reason
    assert "requirements-external.txt" in capability.remedy


def test_fetching_is_off_by_default_and_says_so():
    from app.services.external import http

    enabled, reason, remedy = http.fetch_enabled()
    assert enabled is False
    assert "switched off" in reason
    assert "ELEVENMETRIC_EXTERNAL_FETCH_ENABLED" in remedy


def test_a_disabled_fetch_raises_rather_than_returning_nothing():
    from app.services.external import http

    with pytest.raises(SourceUnavailable) as exc:
        http.fetch("https://sofifa.com/team/241", source="sofifa",
                   expected="a squad table", ttl_hours=0)
    assert "ELEVENMETRIC_EXTERNAL_FETCH_ENABLED" in exc.value.remedy


def test_split_rankable_explains_who_it_dropped():
    class Stub:
        def __init__(self, rating):
            self.id = "x"
            self.name = "Stub"
            self.overall_rating = rating

    rankable, excluded = split_rankable([Stub(80.0), Stub(None)])
    assert len(rankable) == 1
    assert len(excluded) == 1
    assert "will not be invented" in excluded[0]["reason"]


# --- API -------------------------------------------------------------------

def test_sources_endpoint_names_what_each_source_is(client, auth):
    response = client.get("/api/v1/external/sources", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert {s["key"] for s in body["sources"]} == {"sofifa", "statsbomb"}
    assert "not interchangeable" in body["note"]


def test_preview_from_a_file_writes_nothing(client, auth, db):
    before = db.query(Player).count()
    response = client.post(
        "/api/v1/external/sofifa/preview-file",
        headers=auth,
        files={"file": ("sofifa_squad.csv", _read("sofifa_squad.csv"), "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["player_count"] == 18
    assert body["errors"] == []
    assert body["provenance"]["source"] == "sofifa"
    assert db.query(Player).count() == before


def test_commit_from_a_file_creates_the_team(client, auth):
    response = client.post(
        "/api/v1/external/sofifa/commit-file",
        headers=auth,
        files={"file": ("sofifa_squad.csv", _read("sofifa_squad.csv"), "text/csv")},
        data={"club_name": "Committed FC", "kind": "opponent"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["team"] == "Committed FC"
    assert body["created"] + body["updated"] == 18
    assert body["provenance"]["source"] == "sofifa"


def test_a_file_with_bad_rows_is_refused_unless_partial_is_opted_into(client, auth):
    csv = (b"sofifa_id,short_name,player_positions,overall\n"
           b"1,Fine,ST,80\n2,Broken,SWEEPER,80\n")
    common = {"headers": auth, "files": {"file": ("x.csv", csv, "text/csv")}}

    refused = client.post("/api/v1/external/sofifa/commit-file", **common)
    assert refused.status_code == 422
    assert refused.json()["detail"]["errors"][0]["value"] == "SWEEPER"

    accepted = client.post(
        "/api/v1/external/sofifa/commit-file", headers=auth,
        files={"file": ("x.csv", csv, "text/csv")},
        data={"club_name": "Partial FC", "allow_partial": "true"},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["created"] == 1


def test_live_routes_report_unavailable_rather_than_failing_opaquely(client, auth):
    response = client.get("/api/v1/external/sofifa/clubs?q=madrid", headers=auth)
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["source"] == "sofifa"
    assert "ELEVENMETRIC_EXTERNAL_FETCH_ENABLED" in detail["remedy"]


def test_an_imported_team_is_invisible_to_another_tenant(client, auth, rival_auth):
    """Principle III: existence is itself private — 404, never 403."""
    created = client.post(
        "/api/v1/external/sofifa/commit-file",
        headers=auth,
        files={"file": ("sofifa_squad.csv", _read("sofifa_squad.csv"), "text/csv")},
        data={"club_name": "Isolated FC"},
    )
    assert created.status_code == 201, created.text
    team_id = created.json()["team_id"]

    assert client.get(f"/api/v1/teams/{team_id}", headers=auth).status_code == 200
    assert client.get(f"/api/v1/teams/{team_id}",
                      headers=rival_auth).status_code == 404


def test_an_empty_upload_is_refused(client, auth):
    response = client.post(
        "/api/v1/external/sofifa/preview-file", headers=auth,
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert response.status_code == 422
