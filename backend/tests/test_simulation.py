"""The match engine: does it produce a football match, and the same one twice?

The engine's job is not to be right about any particular fixture — it is to stay
inside the bands a real match lives in. A simulation that reports 103 shots or a
centre-back covering 17 km is worse than no simulation, because the numbers look
authoritative. These tests are those bands.
"""

from __future__ import annotations

import pytest

from app.models.catalog import Position
from app.services.analytics.pitch import Pitch
from app.services.simulation.engine import (
    SimPlayer,
    TeamSetup,
    _finishing_skill,
    simulate,
)

API = "/api/v1"

FORMATION_433 = [
    Position.GK, Position.LB, Position.CB, Position.CB, Position.RB,
    Position.CM, Position.CM, Position.CM, Position.LW, Position.ST, Position.RW,
]
BENCH_SHAPE = [Position.GK, Position.CB, Position.LB, Position.RB, Position.CM,
               Position.CM, Position.LW, Position.RW, Position.ST]


def _player(index: int, position: Position, rating: float, *,
            stamina: float = 78.0, fatigue: float = 0.0) -> SimPlayer:
    return SimPlayer(
        id=f"p{index}", name=f"Player {index}", position=position, rating=rating,
        attributes={"pace": rating, "shooting": rating, "passing": rating,
                    "dribbling": rating, "defending": rating, "physical": rating,
                    "stamina": stamina, "short_passing": rating,
                    "long_passing": rating - 4, "finishing": rating,
                    "defensive_awareness": rating},
        age=26.0, start_fatigue=fatigue,
    )


def _side(name: str, rating: float, *, offset: int = 0,
          bench_stamina: float = 80.0, starter_stamina: float = 78.0,
          starter_fatigue: float = 0.0) -> TeamSetup:
    starters = [_player(offset + i, pos, rating, stamina=starter_stamina,
                        fatigue=starter_fatigue)
                for i, pos in enumerate(FORMATION_433)]
    bench = [_player(offset + 11 + i, pos, rating - 2, stamina=bench_stamina)
             for i, pos in enumerate(BENCH_SHAPE)]
    for b in bench:
        b.on_pitch = False
    return TeamSetup(name=name, formation="4-3-3", starters=starters, bench=bench)


@pytest.fixture(scope="module")
def match():
    """A well-conditioned fixture: nobody drops far enough to be replaced, which
    is the right baseline for the realism bands."""
    return simulate(_side("Home", 80), _side("Away", 78, offset=100),
                    minutes=90, seed=4242, pitch=Pitch())


@pytest.fixture(scope="module")
def subbed_match():
    """A fixture that actually produces substitutions.

    Kept separate rather than folded into `match`: the substitution tests are
    worthless if the seed happens not to trigger one, and a squad tired enough to
    guarantee swaps is not the squad you want to assert distance bands against.
    """
    result = simulate(_side("Tired", 80, starter_stamina=48, starter_fatigue=35),
                      _side("Away", 78, offset=100, starter_stamina=48,
                            starter_fatigue=35),
                      minutes=90, seed=4242, pitch=Pitch())
    assert result.substitutions, "fixture must produce substitutions"
    return result


# --- Determinism -----------------------------------------------------------

def test_the_same_seed_replays_the_same_match():
    """The seed is offered in the UI as a promise: replay this fixture. If the
    engine drifted, that promise would be a lie the user cannot check."""
    a = simulate(_side("H", 80), _side("A", 78, offset=100), minutes=20, seed=7)
    b = simulate(_side("H", 80), _side("A", 78, offset=100), minutes=20, seed=7)
    assert a.score == b.score
    assert a.shots == b.shots
    assert a.playback_frames == b.playback_frames


def test_different_seeds_give_different_matches():
    a = simulate(_side("H", 80), _side("A", 78, offset=100), minutes=20, seed=7)
    b = simulate(_side("H", 80), _side("A", 78, offset=100), minutes=20, seed=8)
    assert a.playback_frames != b.playback_frames


# --- Football-shaped output ------------------------------------------------

def test_shot_volume_is_in_a_plausible_band(match):
    total = match.shots[0] + match.shots[1]
    assert 12 <= total <= 42, f"{total} shots in 90 minutes"


def test_scoreline_is_plausible(match):
    assert sum(match.score) <= 8, match.score


def test_conversion_is_anchored_on_xg(match):
    """The app invites a manager to read xG against the scoreline, so the two have
    to be on the same scale. Conversion is drawn from each shot's own xG for
    exactly this reason; before that it was anchored on an unrelated constant and
    the engine scored two and a half times the xG it recorded.

    This asserts the *mechanism*, by recomputing the engine's own goal probability
    from each recorded shot. Asserting the realised scoreline instead cannot be
    made both meaningful and stable: at 25 shots a match, two standard deviations
    of pure luck is ±30%, so a band tight enough to catch a 20% bias would be
    flaky and a band wide enough to be stable would catch almost nothing.
    """
    shots = [e for e in match.events if e.type == "shot"]
    by_id = {p.id: p for p in match.home.all_players() + match.away.all_players()}
    xg = p_goal = 0.0
    for e in shots:
        player = by_id.get(e.player_id)
        quality = (_finishing_skill(player) * player.condition) if player else 0.75
        xg += e.xg
        p_goal += min(0.92, e.xg * (0.40 + 0.80 * quality))
    assert xg > 0
    assert 0.90 <= p_goal / xg <= 1.15, f"converts at {p_goal / xg:.2f}x its own xG"


def test_realised_goals_are_a_believable_scoreline():
    """The loose companion to the test above: whatever the variance, twelve
    fixtures must not average a cricket score or a goalless league."""
    goals = [sum(simulate(_side("H", 80), _side("A", 78, offset=100),
                          minutes=90, seed=seed).score)
             for seed in range(40, 52)]
    mean = sum(goals) / len(goals)
    assert 1.5 <= mean <= 4.5, f"{mean:.1f} goals a game: {goals}"


def test_shot_quality_matches_real_football(match):
    """xG per shot sits near 0.10 in real football. It read 0.33 while the carry
    model interpolated every attempt onto the centre of the goal, which is where
    xG peaks — the engine was manufacturing chances it had not created."""
    shots = [e for e in match.events if e.type == "shot"]
    assert shots
    per_shot = sum(e.xg for e in shots) / len(shots)
    assert 0.06 <= per_shot <= 0.15, f"{per_shot:.3f} xG per shot"


def test_shots_are_struck_from_realistic_distances(match):
    pitch = Pitch()
    shots = [e for e in match.events if e.type == "shot"]
    dists = sorted(
        ((pitch.length - e.x) ** 2 + (pitch.width / 2 - e.y) ** 2) ** 0.5
        for e in shots
    )
    median = dists[len(dists) // 2]
    assert 12.0 <= median <= 22.0, f"median shot from {median:.1f} m"


def test_shots_are_not_all_dead_central(match):
    """A tell-tale of the old carry model: every shot ended up on the goal's
    centre line, so every attempt looked like a one-on-one with the keeper."""
    pitch = Pitch()
    shots = [e for e in match.events if e.type == "shot"]
    off_centre = [e for e in shots if abs(e.y - pitch.width / 2) > 4.0]
    assert len(off_centre) / len(shots) > 0.25


def test_more_than_one_player_shoots(match):
    """The centre-forward took every shot in the match until the shape learned
    to send anyone else into the box."""
    for side in (match.home, match.away):
        shooters = [p for p in side.all_players() if p.shots]
        assert len(shooters) >= 3, [p.name for p in shooters]


def test_the_forward_does_not_take_almost_every_shot():
    """A ceiling, not a target — and a regression guard, not a claim of realism.

    Real centre-forwards take a quarter to a third of their side's shots. This
    engine's take about three quarters, because an eleven-slot shape with fixed
    anchors has no third-man runs or overlaps: the highest player is the one who
    ends up in shooting positions. docs/MODELS.md records the limitation. What
    this test does catch is the state it was in before, where the forward took
    *every* shot in the match.
    """
    forwards = (Position.ST, Position.CF, Position.SS)
    shares = []
    for seed in range(60, 66):
        r = simulate(_side("H", 80), _side("A", 78, offset=100), minutes=90, seed=seed)
        for side in (r.home, r.away):
            total = sum(p.shots for p in side.all_players())
            if total >= 6:
                shares.append(sum(p.shots for p in side.all_players()
                                  if p.position in forwards) / total)
    assert shares
    mean = sum(shares) / len(shares)
    assert mean <= 0.80, f"forwards took {mean:.0%} of the shots"


def test_distance_covered_is_human(match):
    """Outfielders run 9-13 km in a real match; the engine need not hit that
    exactly, but a figure outside 5-15 km means the movement model is broken."""
    outfield = [p for p in match.home.all_players()
                if p.position is not Position.GK and p.minutes_played > 20]
    assert outfield
    for p in outfield:
        assert 5.0 <= p.distance_m / 1000 <= 15.0, f"{p.name}: {p.distance_m/1000:.1f} km"


def test_pass_volume_and_accuracy_are_in_a_plausible_band(match):
    """A side plays 450-550 passes. The engine played a thousand until the
    interval between on-ball actions was doubled, and the per-player touch
    counts in the report read as nonsense next to a real one."""
    players = match.home.all_players()
    passes = sum(p.passes for p in players)
    completed = sum(p.passes_completed for p in players)
    assert 380 <= passes <= 640, passes
    assert 0.70 <= completed / passes <= 0.90, completed / passes


def test_no_single_player_monopolises_the_ball(match):
    touches = [p.touches for p in match.home.all_players()]
    assert max(touches) <= 170, max(touches)
    assert max(touches) / sum(touches) <= 0.28


def test_possession_shares_add_up(match):
    assert 20.0 <= match.possession_pct <= 80.0


def test_the_keeper_is_not_the_busiest_player(match):
    """An early version had the goalkeeper touching the ball more than anyone,
    because the block was so compressed that he was the nearest man."""
    outfield = [p for p in match.home.all_players()
                if p.position is not Position.GK]
    keeper = next(p for p in match.home.all_players() if p.position is Position.GK)
    assert keeper.touches < max(p.touches for p in outfield)


def test_every_position_and_coordinate_is_on_the_pitch(match):
    pitch = Pitch()
    for frame in match.frames[::37]:
        for positions in (frame.home_positions, frame.away_positions):
            for x, y in positions.values():
                assert -1 <= x <= pitch.length + 1
                assert -1 <= y <= pitch.width + 1


def test_attackers_get_nearer_goal_when_the_ball_does(match):
    """The shape has to react to the phase of play. While it did not, the whole
    side sat in a defending block all match: the forward was the only man ever
    inside shooting range and took every attempt."""
    pitch = Pitch()
    ids = [p.id for p in match.home.starters]
    wide_ids = [ids[i] for i, p in enumerate(match.home.starters)
                if p.position in (Position.LW, Position.RW)]
    assert wide_ids

    near, far = [], []
    for frame in match.frames:
        if frame.possession_team != "home" or not frame.ball:
            continue
        bucket = near if frame.ball[0] > pitch.length - 32 else far
        for pid in wide_ids:
            pos = frame.home_positions.get(pid)
            if pos:
                bucket.append(pos[0])
    assert near and far
    assert sum(near) / len(near) > sum(far) / len(far) + 4.0


def test_neither_direction_of_play_is_favoured():
    """The outfield band is written for a side attacking to the right, so the
    other side needs it mirrored. Applied as a single absolute clamp it let the
    away side attack seven metres closer to goal, and evenly matched teams
    finished 19 shots to 7."""
    home_shots = away_shots = 0
    for seed in range(70, 82):
        r = simulate(_side("H", 80), _side("A", 80, offset=100), minutes=90, seed=seed)
        home_shots += r.shots[0]
        away_shots += r.shots[1]
    total = home_shots + away_shots
    assert total > 100
    share = home_shots / total
    assert 0.40 <= share <= 0.60, f"home took {share:.0%} of the shots"


def test_the_block_stays_compact(match):
    """Front-to-back span. Used as absolute positions the anchors string the side
    over 87 m of grass and every compactness metric reads as broken."""
    spans = []
    for frame in match.frames[::53]:
        xs = [p[0] for pid, p in frame.home_positions.items()
              if pid != match.home.starters[0].id]
        spans.append(max(xs) - min(xs))
    mean_span = sum(spans) / len(spans)
    assert 22.0 <= mean_span <= 55.0, f"{mean_span:.1f} m block"


def test_events_carry_pitch_coordinates_and_a_clock(match):
    assert match.events
    for e in match.events:
        assert e.period in (1, 2)
        assert 0 <= e.minute <= match.minutes
        assert 0 <= e.second < 60
        assert 0 <= e.x <= 105 and 0 <= e.y <= 68


# --- Fatigue ---------------------------------------------------------------

def test_condition_declines_over_a_match(match):
    """Fatigue is the whole point of watching a simulation rather than reading a
    scoreline, so it has to actually move."""
    outfield = [p for p in match.home.starters if p.position is not Position.GK]
    played_through = [p for p in outfield if p.came_off_at is None]
    assert played_through
    assert min(p.condition for p in played_through) < 0.97


def test_condition_timeline_is_recorded_while_a_player_is_on_the_pitch(match):
    for player in match.home.starters:
        points = match.condition_timeline.get(player.id)
        assert points, f"no timeline for {player.name}"
        assert all(0.4 <= p["condition"] <= 1.0 for p in points)
        if player.came_off_at is not None:
            assert max(p["minute"] for p in points) <= player.came_off_at


def test_a_tired_squad_is_substituted_and_a_fresh_one_is_not():
    tired = simulate(
        _side("Tired", 80, starter_stamina=40, starter_fatigue=55),
        _side("Away", 78, offset=100), minutes=90, seed=11)
    fresh = simulate(
        _side("Fresh", 80, starter_stamina=95),
        _side("Away", 78, offset=100), minutes=90, seed=11)
    tired_subs = [s for s in tired.substitutions if s["side"] == "home"]
    fresh_subs = [s for s in fresh.substitutions if s["side"] == "home"]
    assert len(tired_subs) > len(fresh_subs)


def test_auto_subs_can_be_turned_off():
    result = simulate(_side("H", 80), _side("A", 78, offset=100),
                      minutes=90, seed=5, auto_subs=False)
    assert result.substitutions == []


# --- Substitutions ---------------------------------------------------------

def test_nobody_is_reported_twice(subbed_match):
    for side in (subbed_match.home, subbed_match.away):
        ids = [p.id for p in side.all_players()]
        assert len(ids) == len(set(ids))


def test_everyone_who_came_on_is_still_in_the_squad(subbed_match):
    """An earlier version moved players between `starters` and `bench` on a swap,
    so whoever came on belonged to neither list and vanished from the report —
    the two players you had just watched play were the two missing from it."""
    for side_name, side in (("home", subbed_match.home), ("away", subbed_match.away)):
        squad = {p.id for p in side.all_players()}
        for sub in subbed_match.substitutions:
            if sub["side"] != side_name:
                continue
            assert sub["on"]["id"] in squad, sub["on"]["name"]
            assert sub["off"]["id"] in squad, sub["off"]["name"]


def test_a_substituted_player_does_not_come_back_on(subbed_match):
    """Reachable before the lists stopped being mutated: a player pushed onto the
    bench on his way off looked exactly like a fresh substitute."""
    for side_name in ("home", "away"):
        subs = [s for s in subbed_match.substitutions if s["side"] == side_name]
        came_on = [s["on"]["id"] for s in subs]
        came_off = [s["off"]["id"] for s in subs]
        assert len(came_on) == len(set(came_on))
        assert len(came_off) == len(set(came_off))
        assert not set(came_on) & set(came_off)


def test_substitutions_carry_the_slot_they_changed(subbed_match):
    """The client relabels a pitch token at the sub minute; without the slot
    index it would have to guess which dot changed identity."""
    for sub in subbed_match.substitutions:
        assert sub["side"] in ("home", "away")
        assert 0 <= sub["slot_index"] < 11
        assert sub["slot_index"] != 0, "the engine should not auto-swap keepers"
        assert sub["on"]["id"] != sub["off"]["id"]
        assert 0 <= sub["minute"] <= subbed_match.minutes


def test_minutes_played_matches_time_on_the_pitch(subbed_match):
    for side in (subbed_match.home, subbed_match.away):
        for p in side.all_players():
            assert 0 <= p.minutes_played <= subbed_match.minutes
            if p.came_on_at is not None:
                assert p.minutes_played <= subbed_match.minutes - p.came_on_at + 1
            if p.came_on_at is None and p.came_off_at is None:
                assert p.minutes_played in (0, subbed_match.minutes)


def test_an_unused_substitute_has_no_match_record(subbed_match):
    used = {s["on"]["id"] for s in subbed_match.substitutions}
    unused = [p for p in subbed_match.home.bench if p.id not in used]
    assert unused
    for p in unused:
        assert p.minutes_played == 0
        assert p.came_on_at is None
        assert p.touches == 0
        assert p.distance_m == 0.0


# --- Playback stream -------------------------------------------------------

def test_playback_roster_is_the_kick_off_xi(match):
    """The stream is per-slot, so token zero has to start as the player who
    actually took the field — not as whoever finished the match in that slot."""
    roster = match.playback_roster
    assert len(roster) == 22
    assert [r["id"] for r in roster[:11]] == [p.id for p in match.home.starters]
    assert [r["id"] for r in roster[11:]] == [p.id for p in match.away.starters]


def test_playback_frames_are_flat_and_well_formed(match):
    payload = match.playback()
    assert payload["scale"] == 2
    expected = 1 + 22 * 2 + 2 + 1        # clock + 22 players + ball + possession
    assert match.playback_frames
    for frame in match.playback_frames[::29]:
        assert len(frame) == expected
        assert all(isinstance(v, int) for v in frame)
        assert frame[-1] in (0, 1)
    clocks = [f[0] for f in match.playback_frames]
    assert clocks == sorted(clocks)
    assert clocks[-1] <= match.minutes * 60


def test_playback_stays_small_enough_to_send():
    """Half-metre integers rather than floats: a 90-minute stream has to fit in
    one ordinary JSON response, or the whole feature stops being usable."""
    import json

    result = simulate(_side("H", 80), _side("A", 78, offset=100),
                      minutes=90, seed=3, playback_hz=0.5)
    size_kb = len(json.dumps(result.playback())) / 1024
    assert size_kb < 1200, f"{size_kb:.0f} KB"


# --- API -------------------------------------------------------------------

def test_options_lists_teams_and_formations(client, auth):
    body = client.get(f"{API}/simulation/options", headers=auth).json()
    assert body["teams"]
    assert "4-3-3" in body["formations"]
    assert {m["key"] for m in body["modes"]} == {"instant", "fast", "realtime"}


def test_run_against_a_generated_opponent_says_so(client, auth, team_id):
    res = client.post(f"{API}/simulation/run", headers=auth, json={
        "home_team_id": team_id, "away_strength": 76, "minutes": 20,
        "seed": 21, "persist": False,
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["opponent_is_synthetic"] is True
    assert "generated" in body["note"]
    assert body["match_id"] is None
    assert len(body["playback"]["roster"]) == 22


def test_a_persisted_simulation_becomes_an_analysable_match(client, auth, team_id):
    res = client.post(f"{API}/simulation/run", headers=auth, json={
        "home_team_id": team_id, "minutes": 15, "seed": 33, "persist": True,
    })
    assert res.status_code == 200, res.text
    match_id = res.json()["match_id"]
    assert match_id

    match = client.get(f"{API}/matches/{match_id}", headers=auth).json()
    assert match["competition"] == "Simulation"
    assert "not a record of a real match" in (match.get("notes") or "")


def test_a_team_cannot_play_itself(client, auth, team_id):
    res = client.post(f"{API}/simulation/run", headers=auth, json={
        "home_team_id": team_id, "away_team_id": team_id, "minutes": 10,
    })
    assert res.status_code == 422


def test_an_unknown_formation_is_refused(client, auth, team_id):
    res = client.post(f"{API}/simulation/run", headers=auth, json={
        "home_team_id": team_id, "home_formation": "9-1-0", "minutes": 10,
    })
    assert res.status_code == 422


def test_another_tenants_team_is_a_404(client, rival_auth, team_id):
    res = client.post(f"{API}/simulation/run", headers=rival_auth, json={
        "home_team_id": team_id, "minutes": 10,
    })
    assert res.status_code == 404
