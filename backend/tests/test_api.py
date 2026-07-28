"""API surface: auth, tenant isolation, ingest, and the analysis endpoints."""

from __future__ import annotations

import pytest

API = "/api/v1"


# --- Auth ------------------------------------------------------------------

def test_login_returns_a_token_and_the_club(client):
    r = client.post(f"{API}/auth/login",
                    json={"email": "owner@demo.fc", "password": "elevenmetric"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["tenant"]["slug"] == "demo-fc"
    assert body["user"]["role"] == "owner"


def test_bad_password_is_rejected(client):
    r = client.post(f"{API}/auth/login",
                    json={"email": "owner@demo.fc", "password": "wrong"})
    assert r.status_code == 401


def test_protected_routes_need_a_token(client):
    assert client.get(f"{API}/players").status_code == 401
    assert client.get(f"{API}/teams").status_code == 401


def test_garbage_token_is_rejected(client):
    r = client.get(f"{API}/players", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_me_returns_the_current_user(client, auth):
    r = client.get(f"{API}/auth/me", headers=auth)
    assert r.status_code == 200
    assert r.json()["email"] == "owner@demo.fc"


# --- Tenant isolation ------------------------------------------------------

def test_tenants_see_only_their_own_players(client, auth, rival_auth):
    mine = client.get(f"{API}/players", headers=auth).json()
    theirs = client.get(f"{API}/players", headers=rival_auth).json()
    assert mine and theirs
    assert {p["id"] for p in mine}.isdisjoint({p["id"] for p in theirs})
    assert any(p["name"] == "Rival Striker" for p in theirs)
    assert not any(p["name"] == "Rival Striker" for p in mine)


def test_another_tenants_object_is_a_404_not_a_403(client, auth, rival_auth):
    """A 403 would confirm the id exists. Existence itself is private."""
    theirs = client.get(f"{API}/players", headers=rival_auth).json()[0]["id"]
    r = client.get(f"{API}/players/{theirs}", headers=auth)
    assert r.status_code == 404


def test_cross_tenant_match_access_is_blocked(client, auth, rival_auth, match_id):
    r = client.get(f"{API}/matches/{match_id}", headers=rival_auth)
    assert r.status_code == 404
    assert client.get(f"{API}/matches/{match_id}", headers=auth).status_code == 200


def test_cross_tenant_analysis_is_blocked(client, rival_auth, match_id):
    r = client.post(f"{API}/analysis/match", headers=rival_auth, json={"match_id": match_id})
    assert r.status_code == 404


def test_a_tenant_cannot_delete_another_tenants_player(client, auth, rival_auth):
    theirs = client.get(f"{API}/players", headers=rival_auth).json()[0]["id"]
    assert client.delete(f"{API}/players/{theirs}", headers=auth).status_code == 404
    # Still there for its owner.
    assert client.get(f"{API}/players/{theirs}", headers=rival_auth).status_code == 200


def test_overview_counts_are_per_tenant(client, auth, rival_auth):
    mine = client.get(f"{API}/meta/overview", headers=auth).json()
    theirs = client.get(f"{API}/meta/overview", headers=rival_auth).json()
    assert mine["players"] > theirs["players"]
    assert theirs["matches"] == 0


# --- Squad -----------------------------------------------------------------

def test_players_can_be_filtered_by_position(client, auth, team_id):
    r = client.get(f"{API}/players", headers=auth, params={"team_id": team_id, "position": "CM"})
    assert r.status_code == 200
    assert all(p["primary_position"] == "CM" for p in r.json())


def test_player_creation_validates_secondary_positions(client, auth, team_id):
    r = client.post(f"{API}/players", headers=auth, json={
        "name": "Bad Position", "team_id": team_id,
        "primary_position": "CM", "secondary_positions": ["QB"],
    })
    assert r.status_code == 422
    assert "Unknown positions" in r.text


def test_player_rating_is_bounded(client, auth, team_id):
    r = client.post(f"{API}/players", headers=auth, json={
        "name": "Too Good", "team_id": team_id, "overall_rating": 140,
    })
    assert r.status_code == 422


def test_player_round_trip(client, auth, team_id):
    created = client.post(f"{API}/players", headers=auth, json={
        "name": "Test Signing", "team_id": team_id, "primary_position": "DM",
        "overall_rating": 77, "attributes": {"pace": 70, "passing": 82},
    })
    assert created.status_code == 201
    pid = created.json()["id"]

    patched = client.patch(f"{API}/players/{pid}", headers=auth,
                           json={"overall_rating": 81, "is_available": False})
    assert patched.status_code == 200
    assert patched.json()["overall_rating"] == 81
    assert patched.json()["is_available"] is False

    assert client.delete(f"{API}/players/{pid}", headers=auth).status_code == 204
    assert client.get(f"{API}/players/{pid}", headers=auth).status_code == 404


def test_best_xi_returns_a_full_team(client, auth, team_id):
    r = client.post(f"{API}/lineups/best-xi", headers=auth,
                    json={"team_id": team_id, "formation": "4-3-3"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["slots"]) == 11
    assert body["mean_effective_level"] > 0
    assert "balance" in body


def test_best_xi_rejects_an_unknown_formation(client, auth, team_id):
    r = client.post(f"{API}/lineups/best-xi", headers=auth,
                    json={"team_id": team_id, "formation": "1-2-3-4"})
    assert r.status_code == 422


def test_formation_comparison_is_ranked(client, auth, team_id):
    r = client.get(f"{API}/lineups/formations/compare", headers=auth,
                   params={"team_id": team_id})
    assert r.status_code == 200
    ranking = r.json()["ranking"]
    scores = [x["mean_effective_level"] for x in ranking]
    assert scores == sorted(scores, reverse=True)


def test_lineup_rejects_twelve_starters(client, auth, team_id):
    players = client.get(f"{API}/players", headers=auth,
                         params={"team_id": team_id}).json()[:12]
    r = client.post(f"{API}/lineups", headers=auth, json={
        "team_id": team_id, "formation": "4-3-3",
        "slots": [{"player_id": p["id"], "slot_index": i, "is_starter": True}
                  for i, p in enumerate(players)],
    })
    assert r.status_code == 422
    assert "11 starters" in r.text


def test_lineup_rejects_a_duplicated_player(client, auth, team_id):
    p = client.get(f"{API}/players", headers=auth, params={"team_id": team_id}).json()[0]
    r = client.post(f"{API}/lineups", headers=auth, json={
        "team_id": team_id, "formation": "4-3-3",
        "slots": [
            {"player_id": p["id"], "slot_index": 0},
            {"player_id": p["id"], "slot_index": 1},
        ],
    })
    assert r.status_code == 422


# --- Ingest ----------------------------------------------------------------

def test_event_ingest_converts_provider_coordinates(client, auth, team_id):
    match = client.post(f"{API}/matches", headers=auth, json={
        "team_id": team_id, "opponent_name": "Ingest Test",
    }).json()

    r = client.post(f"{API}/matches/{match['id']}/events", headers=auth, json={
        "provider": "statsbomb",
        "events": [{"type": "pass", "x": 60.0, "y": 40.0, "end_x": 90.0, "end_y": 20.0}],
    })
    assert r.status_code == 201

    stored = client.get(f"{API}/matches/{match['id']}/events", headers=auth).json()["events"][0]
    # StatsBomb 120x80, y flipped → (52.5, 34.0) at the centre spot.
    assert stored["x"] == pytest.approx(52.5, abs=0.1)
    assert stored["y"] == pytest.approx(34.0, abs=0.1)

    client.delete(f"{API}/matches/{match['id']}", headers=auth)


def test_unknown_provider_frame_is_rejected(client, auth, team_id):
    match = client.post(f"{API}/matches", headers=auth,
                        json={"team_id": team_id}).json()
    r = client.post(f"{API}/matches/{match['id']}/events", headers=auth, json={
        "provider": "made-up", "events": [{"type": "pass", "x": 1, "y": 1}],
    })
    assert r.status_code == 422
    assert "Unknown provider frame" in r.text
    client.delete(f"{API}/matches/{match['id']}", headers=auth)


def test_tracking_ingest_decimates_to_the_target_rate(client, auth, team_id):
    match = client.post(f"{API}/matches", headers=auth,
                        json={"team_id": team_id}).json()
    # 25 Hz for 4 seconds = 100 frames; at 5 Hz only ~20 should survive.
    frames = [
        {"period": 1, "timestamp_ms": i * 40,
         "home_positions": {"a": [50.0, 34.0]}, "away_positions": {"b": [55.0, 34.0]}}
        for i in range(100)
    ]
    r = client.post(f"{API}/matches/{match['id']}/tracking", headers=auth,
                    json={"frames": frames, "target_hz": 5.0})
    assert r.status_code == 201
    body = r.json()
    assert body["received"] == 100
    assert body["stored"] <= 21
    client.delete(f"{API}/matches/{match['id']}", headers=auth)


def test_empty_ingest_is_rejected(client, auth, team_id):
    match = client.post(f"{API}/matches", headers=auth, json={"team_id": team_id}).json()
    r = client.post(f"{API}/matches/{match['id']}/events", headers=auth,
                    json={"events": []})
    assert r.status_code == 422
    client.delete(f"{API}/matches/{match['id']}", headers=auth)


# --- Analysis --------------------------------------------------------------

def test_match_analysis_produces_a_full_report(client, auth, match_id):
    r = client.post(f"{API}/analysis/match", headers=auth,
                    json={"match_id": match_id, "minute": 75, "score_difference": -1})
    assert r.status_code == 200, r.text
    report = r.json()

    assert report["data_completeness"] > 0.5
    assert 0 < report["confidence"] <= 1
    assert "event_data" in report["inputs_used"]
    assert "tracking" in report["inputs_used"]

    assert report["possession"]["pass_possession_pct"] is not None
    assert report["formation"]["formation"] != "unknown"
    assert report["tactics"]["identity"] != "unknown"
    assert report["heatmaps"]["team"]["grid"]
    assert report["zones"]["control"]
    assert report["summary"]

    for rec in report["recommendations"]:
        assert 0 <= rec["priority"] <= 100
        assert 0 <= rec["confidence"] <= 1


def test_analysis_of_a_match_with_no_data_explains_itself(client, auth, team_id):
    match = client.post(f"{API}/matches", headers=auth,
                        json={"team_id": team_id, "opponent_name": "Empty"}).json()
    r = client.post(f"{API}/analysis/match", headers=auth, json={"match_id": match["id"]})
    assert r.status_code == 422
    assert "ingest" in r.json()["detail"].lower()
    client.delete(f"{API}/matches/{match['id']}", headers=auth)


def test_analysing_at_an_earlier_minute_uses_less_data(client, auth, match_id):
    early = client.post(f"{API}/analysis/match", headers=auth,
                        json={"match_id": match_id, "minute": 20}).json()
    full = client.post(f"{API}/analysis/match", headers=auth,
                       json={"match_id": match_id, "minute": 90}).json()
    assert early["heatmaps"]["team"]["samples"] < full["heatmaps"]["team"]["samples"]


def test_report_is_retrievable_by_id(client, auth, match_id):
    created = client.post(f"{API}/analysis/match", headers=auth,
                          json={"match_id": match_id}).json()
    fetched = client.get(f"{API}/analysis/reports/{created['id']}", headers=auth)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]


def test_missing_match_is_404(client, auth):
    r = client.post(f"{API}/analysis/match", headers=auth, json={"match_id": "nope"})
    assert r.status_code == 404


# --- Transfers -------------------------------------------------------------

def test_needs_endpoint_lists_scanned_positions(client, auth, team_id):
    r = client.get(f"{API}/transfers/needs", headers=auth, params={"team_id": team_id})
    assert r.status_code == 200
    body = r.json()
    assert body["formation"] == "4-3-3"
    assert "RWB" not in body["scanned_positions"]
    assert all(0 <= n["severity"] <= 1 for n in body["needs"])


def test_scan_builds_an_affordable_bundle(client, auth, team_id):
    r = client.post(f"{API}/transfers/scan", headers=auth,
                    json={"team_id": team_id, "max_signings": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shortlist_id"]
    if body["bundle"]:
        assert body["budget"]["total_fee_eur"] <= body["budget"]["budget_eur"]
        assert (body["budget"]["total_wage_eur_per_year"]
                <= body["budget"]["wage_budget_eur_per_year"])
        assert len({t["market_player_id"] for t in body["bundle"]}) == len(body["bundle"])


def test_scan_rejects_style_weights_that_do_not_sum_to_one(client, auth, team_id):
    r = client.post(f"{API}/transfers/scan", headers=auth, json={
        "team_id": team_id,
        "style_weights": {"quality": 0.9, "fit": 0.9, "value": 0.9, "risk": 0.9},
    })
    assert r.status_code == 422


def test_scan_without_a_budget_is_rejected(client, auth, team_id):
    r = client.post(f"{API}/transfers/scan", headers=auth,
                    json={"team_id": team_id, "budget_eur": 0})
    assert r.status_code == 422


def test_market_pool_is_tenant_scoped(client, auth, rival_auth):
    assert client.get(f"{API}/transfers/market", headers=auth).json()
    assert client.get(f"{API}/transfers/market", headers=rival_auth).json() == []


# --- Academy ---------------------------------------------------------------

def test_academy_review_projects_every_player(client, auth, team_id):
    r = client.post(f"{API}/academy/review", headers=auth,
                    json={"team_id": team_id, "persist": True})
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["count"] == len(body["projections"])

    months = [p["months_to_first_team"] for p in body["projections"]
              if p["months_to_first_team"] is not None]
    assert months == sorted(months), "ready-soonest must come first"

    for p in body["projections"]:
        assert 0 <= p["readiness_score"] <= 100
        assert p["pathway"] in {
            "promote_now", "train_with_first_team", "loan_out",
            "continue_academy", "review", "release",
        }


def test_academy_pipeline_groups_arrivals_into_windows(client, auth, team_id):
    r = client.get(f"{API}/academy/pipeline", headers=auth,
                   params={"team_id": team_id, "horizon_months": 36})
    assert r.status_code == 200
    body = r.json()
    assert "windows" in body and "uncovered_positions" in body


def test_a_future_assessment_is_rejected(client, auth):
    player_id = client.get(f"{API}/academy/players", headers=auth).json()[0]["id"]
    r = client.post(f"{API}/academy/players/{player_id}/assessments", headers=auth,
                    json={"assessed_on": "2099-01-01", "ability": 70})
    assert r.status_code == 422


def test_promotion_creates_a_senior_player(client, auth, team_id):
    youth = client.get(f"{API}/academy/players", headers=auth).json()
    target = next(p for p in youth if not p.get("senior_player_id"))
    r = client.post(f"{API}/academy/players/{target['id']}/promote",
                    headers=auth, params={"team_id": team_id})
    assert r.status_code == 201
    senior_id = r.json()["senior_player_id"]
    assert client.get(f"{API}/players/{senior_id}", headers=auth).status_code == 200

    # Promoting twice is a conflict, not a second player.
    again = client.post(f"{API}/academy/players/{target['id']}/promote",
                        headers=auth, params={"team_id": team_id})
    assert again.status_code == 409
    client.delete(f"{API}/players/{senior_id}", headers=auth)


# --- Meta ------------------------------------------------------------------

def test_data_requirements_documents_every_tier(client):
    r = client.get(f"{API}/meta/data-requirements")
    assert r.status_code == 200
    tiers = r.json()["tiers"]
    assert [t["key"] for t in tiers] == ["squad", "event_data", "tracking", "video", "context"]
    for tier in tiers:
        assert tier["fields"] and tier["unlocks"]
        assert any(f["required"] for f in tier["fields"])


def test_data_readiness_scores_the_tenant(client, auth, rival_auth):
    mine = client.get(f"{API}/meta/data-readiness", headers=auth).json()
    theirs = client.get(f"{API}/meta/data-readiness", headers=rival_auth).json()
    assert mine["coverage_score"] > theirs["coverage_score"]
    assert theirs["gaps"]


def test_reference_data_covers_positions_and_formations(client):
    r = client.get(f"{API}/meta/reference")
    assert r.status_code == 200
    body = r.json()
    assert len(body["positions"]) >= 15
    assert "4-3-3" in body["formations"]
    assert body["pitch"]["length_m"] == 105.0
    for p in body["positions"]:
        assert 0 <= p["anchor_norm"][0] <= 1
        assert 0 <= p["anchor_norm"][1] <= 1


def test_model_registry_is_exposed(client):
    body = client.get(f"{API}/meta/models").json()
    names = {m["name"] for m in body["models"]}
    assert {"impact", "academy"} <= names
    assert body["cv"]["engine"] in {"yolo+bytetrack", "hog+bytetrack", "simulated"}


def test_video_capabilities_are_honest_about_the_engine(client):
    body = client.get(f"{API}/video/capabilities").json()
    assert "engine" in body and "note" in body
    if body["engine"] == "simulated":
        assert "not installed" in body["note"]


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# --- Plan and role enforcement --------------------------------------------

def test_a_viewer_cannot_write(client, auth, team_id):
    created = client.post(f"{API}/users", headers=auth, json={
        "email": "viewer@demo.fc", "password": "elevenmetric123", "role": "viewer",
    })
    assert created.status_code == 201

    viewer = client.post(f"{API}/auth/login", json={
        "email": "viewer@demo.fc", "password": "elevenmetric123",
    }).json()
    vh = {"Authorization": f"Bearer {viewer['access_token']}"}

    assert client.get(f"{API}/players", headers=vh).status_code == 200
    r = client.post(f"{API}/players", headers=vh,
                    json={"name": "Sneaky", "team_id": team_id})
    assert r.status_code == 403
    assert "lacks capability" in r.json()["detail"]


def test_duplicate_email_in_a_tenant_is_rejected(client, auth):
    r = client.post(f"{API}/users", headers=auth, json={
        "email": "owner@demo.fc", "password": "elevenmetric123", "role": "analyst",
    })
    assert r.status_code == 409


def test_api_key_is_shown_once_and_then_works(client, auth):
    created = client.post(f"{API}/api-keys", headers=auth, params={"label": "ingest"})
    assert created.status_code == 201
    raw = created.json()["key"]
    assert raw and raw.startswith("em_")

    listed = client.get(f"{API}/api-keys", headers=auth).json()
    assert all(k["key"] is None for k in listed), "plaintext keys must never be listed"

    r = client.get(f"{API}/players", headers={"X-API-Key": raw})
    assert r.status_code == 200

    key_id = created.json()["id"]
    assert client.delete(f"{API}/api-keys/{key_id}", headers=auth).status_code == 204
    assert client.get(f"{API}/players", headers={"X-API-Key": raw}).status_code == 401
