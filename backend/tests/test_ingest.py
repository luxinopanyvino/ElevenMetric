"""CSV ingestion: header mapping, validation, and the commit contract."""

from __future__ import annotations

import pytest

from app.services.ingest import csv_ingest
from app.services.ml.features import (
    ATTRIBUTE_KEYS,
    DETAIL_GROUPS,
    GK_KEYS,
    HEADLINE_KEYS,
    POSITION_WEIGHTS,
    attribute,
    headline_from_detail,
)

API = "/api/v1"


# --- Attribute vocabulary --------------------------------------------------

def test_position_weights_sum_to_one():
    for bucket, weights in POSITION_WEIGHTS.items():
        assert sum(weights.values()) == pytest.approx(1.0), bucket


def test_position_weights_only_use_known_attributes():
    known = set(ATTRIBUTE_KEYS)
    for bucket, weights in POSITION_WEIGHTS.items():
        assert set(weights) <= known, f"{bucket} references unknown attributes"


def test_goalkeepers_are_judged_on_goalkeeping_attributes():
    """The old model scored keepers on outfield faces, which made every
    goalkeeping decision guesswork."""
    gk_weights = set(POSITION_WEIGHTS["GK"])
    assert gk_weights & set(GK_KEYS), "GK bucket must use gk_* attributes"
    for bucket, weights in POSITION_WEIGHTS.items():
        if bucket != "GK":
            assert not set(weights) & set(GK_KEYS), f"{bucket} must not use gk_*"


def test_every_detail_rolls_up_to_a_headline():
    for parent in DETAIL_GROUPS:
        assert parent in HEADLINE_KEYS


def test_missing_detail_falls_back_to_its_headline_not_the_overall():
    class P:
        attributes = {"shooting": 90}
        overall_rating = 60

    # `finishing` is far better approximated by `shooting` than by the average.
    assert attribute(P(), "finishing") == 90.0
    # Something with no parent still falls back to the overall.
    assert attribute(P(), "gk_diving") == 60.0


def test_headline_is_derived_from_detail_when_absent():
    filled = headline_from_detail({"finishing": 80, "shot_power": 90, "volleys": 70})
    assert filled["shooting"] == pytest.approx(80.0)
    # An explicit headline is never overwritten.
    assert headline_from_detail({"shooting": 50, "finishing": 90})["shooting"] == 50


# --- CSV parsing -----------------------------------------------------------

PLAYERS_CSV = (
    "Full Name;POS;DOB;OVR;Foot;Minutes 7d;pace;shooting;passing;dribbling;defending;physical\n"
    "Ana Ruiz;CM;1999-04-02;83;left;180;72;70;88;84;66;70\n"
    "Beto Lima;ST;2001-11-30;79;right;90;88;84;62;80;35;82\n"
)


def test_semicolon_delimiter_is_detected():
    """Spanish- and German-locale spreadsheets export semicolons; assuming
    commas breaks half of real files."""
    result = csv_ingest.parse("players", PLAYERS_CSV.encode())
    assert result.total_rows == 2
    assert len(result.rows) == 2


def test_headers_are_matched_through_aliases_and_case():
    result = csv_ingest.parse("players", PLAYERS_CSV.encode())
    assert result.mapping["Full Name"] == "name"
    assert result.mapping["POS"] == "primary_position"
    assert result.mapping["DOB"] == "birth_date"
    assert result.mapping["OVR"] == "overall_rating"
    assert result.mapping["Minutes 7d"] == "minutes_last_7d"


def test_attribute_columns_are_collected_into_one_blob():
    row = csv_ingest.parse("players", PLAYERS_CSV.encode()).rows[0]
    assert row["attributes"]["passing"] == 88
    assert "passing" not in row


def test_a_bad_value_fails_only_its_own_row():
    csv = PLAYERS_CSV + "Carl Weiss;GOALIE;1996-01-05;81;right;0;55;25;74;48;30;80\n"
    result = csv_ingest.parse("players", csv.encode())
    assert len(result.rows) == 2
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err.row == 4 and err.column == "primary_position" and err.value == "GOALIE"
    assert "unknown position" in err.message


def test_missing_required_columns_are_named():
    result = csv_ingest.parse("players", b"known_as,shirt_number\nX,9\n")
    assert set(result.missing_required) == {"name", "primary_position", "overall_rating"}
    assert not result.ok


def test_unrecognised_columns_are_reported_not_guessed():
    result = csv_ingest.parse("players", b"name,primary_position,overall_rating,xyz\nA,CM,80,1\n")
    assert result.unmapped_headers == ["xyz"]
    assert result.rows[0]["name"] == "A"


def test_several_date_formats_are_accepted():
    for value in ("1999-04-02", "02/04/1999", "02-04-1999"):
        csv = f"name,primary_position,overall_rating,birth_date\nA,CM,80,{value}\n"
        rows = csv_ingest.parse("players", csv.encode()).rows
        assert rows[0]["birth_date"].year == 1999


def test_ratings_are_clamped_to_the_scale():
    csv = "name,primary_position,overall_rating\nA,CM,140\n"
    assert csv_ingest.parse("players", csv.encode()).rows[0]["overall_rating"] == 99.0


def test_unknown_dataset_is_rejected():
    with pytest.raises(ValueError, match="Unknown dataset"):
        csv_ingest.parse("nope", b"a\n1\n")


def test_template_round_trips_through_the_parser():
    """The template must itself be a valid file, or it teaches the wrong shape."""
    for key in csv_ingest.DATASETS:
        result = csv_ingest.parse(key, csv_ingest.template(key).encode())
        assert not result.missing_required, key
        assert not result.errors, (key, [e.to_dict() for e in result.errors])
        assert len(result.rows) == 1, key


# --- API -------------------------------------------------------------------

def test_dataset_catalogue_is_exposed(client, auth):
    body = client.get(f"{API}/ingest/datasets", headers=auth).json()
    keys = {d["key"] for d in body["datasets"]}
    assert keys == {"players", "market_players", "academy_players",
                    "academy_assessments", "events", "tracking"}
    assert "statsbomb" in body["providers"]


def test_template_download(client, auth):
    r = client.get(f"{API}/ingest/template/players", headers=auth)
    assert r.status_code == 200
    assert r.text.splitlines()[0].startswith("name,")


def test_preview_writes_nothing(client, auth, team_id):
    before = len(client.get(f"{API}/players", headers=auth, params={"team_id": team_id}).json())
    r = client.post(f"{API}/ingest/preview", headers=auth,
                    files={"file": ("s.csv", PLAYERS_CSV.encode(), "text/csv")},
                    data={"dataset": "players"})
    assert r.status_code == 200
    assert r.json()["valid_rows"] == 2
    after = len(client.get(f"{API}/players", headers=auth, params={"team_id": team_id}).json())
    assert after == before


def test_commit_refuses_a_partially_broken_file_by_default(client, auth, team_id):
    csv = PLAYERS_CSV + "Carl Weiss;GOALIE;1996-01-05;81;right;0;55;25;74;48;30;80\n"
    r = client.post(f"{API}/ingest/commit", headers=auth,
                    files={"file": ("s.csv", csv.encode(), "text/csv")},
                    data={"dataset": "players", "team_id": team_id})
    assert r.status_code == 422
    assert "allow_partial" in r.json()["detail"]


def test_commit_imports_and_reimport_updates_rather_than_duplicates(client, auth, team_id):
    csv = ("name,primary_position,overall_rating\n"
           "Import Test One,CM,77\n"
           "Import Test Two,ST,81\n")

    first = client.post(f"{API}/ingest/commit", headers=auth,
                        files={"file": ("s.csv", csv.encode(), "text/csv")},
                        data={"dataset": "players", "team_id": team_id})
    assert first.status_code == 201
    assert first.json()["created"] == 2

    bumped = csv.replace("Import Test One,CM,77", "Import Test One,CM,85")
    second = client.post(f"{API}/ingest/commit", headers=auth,
                         files={"file": ("s.csv", bumped.encode(), "text/csv")},
                         data={"dataset": "players", "team_id": team_id})
    assert second.json()["updated"] == 2
    assert second.json()["created"] == 0

    players = client.get(f"{API}/players", headers=auth, params={"team_id": team_id}).json()
    matches = [p for p in players if p["name"] == "Import Test One"]
    assert len(matches) == 1
    assert matches[0]["overall_rating"] == 85

    for p in players:
        if p["name"].startswith("Import Test"):
            client.delete(f"{API}/players/{p['id']}", headers=auth)


def test_events_ingest_converts_the_provider_frame(client, auth, team_id):
    match = client.post(f"{API}/matches", headers=auth,
                        json={"team_id": team_id, "opponent_name": "CSV Test"}).json()
    csv = ("period,minute,second,type,outcome,is_own_team,x,y\n"
           "1,10,0,pass,success,yes,60,40\n")
    r = client.post(f"{API}/ingest/commit", headers=auth,
                    files={"file": ("e.csv", csv.encode(), "text/csv")},
                    data={"dataset": "events", "match_id": match["id"],
                          "provider": "statsbomb"})
    assert r.status_code == 201, r.text

    stored = client.get(f"{API}/matches/{match['id']}/events", headers=auth).json()["events"][0]
    # StatsBomb 120x80 with a flipped y-axis → the centre spot.
    assert stored["x"] == pytest.approx(52.5, abs=0.1)
    assert stored["y"] == pytest.approx(34.0, abs=0.1)
    client.delete(f"{API}/matches/{match['id']}", headers=auth)


def test_tracking_long_format_is_pivoted_and_decimated(client, auth, team_id):
    match = client.post(f"{API}/matches", headers=auth, json={"team_id": team_id}).json()
    lines = ["period,timestamp_ms,team,player_id,x,y"]
    for i in range(20):                     # 20 timestamps, 40 ms apart = 25 Hz
        ts = i * 40
        lines.append(f"1,{ts},home,p1,50,34")
        lines.append(f"1,{ts},away,o1,55,34")
        lines.append(f"1,{ts},ball,,52,34")
    csv = "\n".join(lines) + "\n"

    r = client.post(f"{API}/ingest/commit", headers=auth,
                    files={"file": ("t.csv", csv.encode(), "text/csv")},
                    data={"dataset": "tracking", "match_id": match["id"],
                          "target_hz": "5"})
    assert r.status_code == 201, r.text
    assert r.json()["created"] <= 5          # 800 ms at 5 Hz

    frames = client.get(f"{API}/matches/{match['id']}/tracking", headers=auth).json()["frames"]
    assert frames[0]["home_positions"] == {"p1": [50.0, 34.0]}
    assert frames[0]["ball"] == [52.0, 34.0]
    client.delete(f"{API}/matches/{match['id']}", headers=auth)


def test_assessments_skip_unknown_players_rather_than_failing(client, auth):
    csv = ("player_name,assessed_on,ability\n"
           "Nobody At All,2026-01-15,70\n")
    r = client.post(f"{API}/ingest/commit", headers=auth,
                    files={"file": ("a.csv", csv.encode(), "text/csv")},
                    data={"dataset": "academy_assessments"})
    assert r.status_code == 201
    body = r.json()
    assert body["created"] == 0
    assert any("Nobody At All" in w for w in body["warnings"])


def test_ingest_needs_write_capability(client, auth, team_id):
    viewer = client.post(f"{API}/auth/login", json={
        "email": "viewer@demo.fc", "password": "elevenmetric123",
    })
    if viewer.status_code != 200:      # created by test_api's role test
        pytest.skip("viewer account not present in this run")
    vh = {"Authorization": f"Bearer {viewer.json()['access_token']}"}
    r = client.post(f"{API}/ingest/commit", headers=vh,
                    files={"file": ("s.csv", PLAYERS_CSV.encode(), "text/csv")},
                    data={"dataset": "players", "team_id": team_id})
    assert r.status_code == 403


def test_unknown_attribute_key_is_rejected(client, auth, team_id):
    r = client.post(f"{API}/players", headers=auth, json={
        "name": "Typo Attr", "team_id": team_id,
        "attributes": {"pace": 80, "shoting": 70},
    })
    assert r.status_code == 422
    assert "Unknown attributes" in r.text


def test_out_of_range_attribute_is_rejected(client, auth, team_id):
    r = client.post(f"{API}/players", headers=auth, json={
        "name": "Too Fast", "team_id": team_id, "attributes": {"pace": 140},
    })
    assert r.status_code == 422


def test_reference_endpoint_publishes_the_attribute_vocabulary(client):
    body = client.get(f"{API}/meta/reference").json()["attributes"]
    assert set(body["headline"]) == set(HEADLINE_KEYS)
    assert set(body["goalkeeping"]) == set(GK_KEYS)
    assert len(body["all"]) == len(ATTRIBUTE_KEYS)
