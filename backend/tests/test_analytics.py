"""Analytics: geometry, value models, possession, formation, heatmaps."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.analytics import formation, heatmap, possession
from app.services.analytics.metrics import (
    PENALTY_XG,
    expected_goals,
    packing,
    progressive,
    xt_delta,
    xt_grid,
    xt_value,
)
from app.services.analytics.pitch import Pitch, to_metres
from app.services.cv.synthetic import SimEvent, simulate_match


# --- Pitch and coordinate conversion ---------------------------------------

def test_provider_frames_convert_to_metres():
    pitch = Pitch()
    # StatsBomb: 120x80 with a flipped y-axis.
    x, y = to_metres(120.0, 0.0, "statsbomb", pitch)
    assert x == pytest.approx(105.0)
    assert y == pytest.approx(68.0)          # y=0 in StatsBomb is the far side

    # Opta percentages.
    x, y = to_metres(50.0, 50.0, "opta", pitch)
    assert (x, y) == pytest.approx((52.5, 34.0))


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="Unknown provider frame"):
        to_metres(10, 10, "not-a-provider")


def test_thirds_and_box():
    pitch = Pitch()
    assert pitch.third_of(10) == "defensive"
    assert pitch.third_of(52.5) == "middle"
    assert pitch.third_of(100) == "attacking"
    assert pitch.in_penalty_area(95, 34)
    assert not pitch.in_penalty_area(95, 2)


# --- xG --------------------------------------------------------------------

def test_xg_falls_with_distance():
    pitch = Pitch()
    close = expected_goals(99, 34, pitch=pitch)
    mid = expected_goals(88, 34, pitch=pitch)
    far = expected_goals(70, 34, pitch=pitch)
    assert close > mid > far
    assert 0 < far < 0.1


def test_xg_falls_with_angle():
    pitch = Pitch()
    central = expected_goals(95, 34, pitch=pitch)
    wide = expected_goals(95, 8, pitch=pitch)
    assert central > wide


def test_xg_penalty_is_fixed():
    assert expected_goals(94, 34, is_penalty=True) == PENALTY_XG
    assert expected_goals(50, 10, situation="penalty") == PENALTY_XG


def test_xg_headers_are_worth_less_than_feet():
    assert expected_goals(98, 34, body_part="head") < expected_goals(98, 34, body_part="foot")


def test_xg_defenders_in_cone_reduce_value():
    open_shot = expected_goals(95, 34, defenders_in_cone=0)
    crowded = expected_goals(95, 34, defenders_in_cone=4)
    assert crowded < open_shot


def test_xg_stays_in_unit_interval():
    for x in range(0, 106, 5):
        for y in range(0, 69, 8):
            assert 0.0 <= expected_goals(x, y) <= 1.0


# --- xT --------------------------------------------------------------------

def test_xt_grid_increases_towards_goal():
    grid = xt_grid()
    centre_row = grid[grid.shape[0] // 2]
    assert centre_row[-1] > centre_row[0]
    assert np.all(grid >= 0)


def test_xt_value_rewards_forward_progress():
    assert xt_delta((30, 34), (80, 34)) > 0
    assert xt_delta((80, 34), (30, 34)) < 0


def test_xt_central_beats_wide_at_same_depth():
    assert xt_value(95, 34) > xt_value(95, 3)


def test_progressive_definition():
    # 25% closer to goal and at least 5 m.
    assert progressive((20, 34), (60, 34))
    assert not progressive((20, 34), (24, 34))
    # Entering the box always counts, even on a short pass.
    assert progressive((85, 34), (96, 34))
    # A short shuffle *inside* the box is not progression.
    assert not progressive((92, 34), (96, 34))


def test_packing_counts_bypassed_opponents():
    opponents = [(50, 34), (55, 30), (20, 5)]
    assert packing((40, 34), (70, 34), opponents) == 2
    # A backwards pass bypasses nobody.
    assert packing((70, 34), (40, 34), opponents) == 0


# --- Possession ------------------------------------------------------------

def _ev(**kw) -> SimEvent:
    base = dict(period=1, minute=0, second=0, type="pass", outcome="success",
                x=50.0, y=34.0, is_own_team=True)
    base.update(kw)
    return SimEvent(**base)


def test_pass_possession_share():
    events = [_ev(second=i) for i in range(7)] + [
        _ev(second=i, is_own_team=False) for i in range(3)
    ]
    res = possession.possession_from_events(events)
    assert res.pass_possession_pct == pytest.approx(70.0)


def test_incomplete_passes_do_not_count_as_possession():
    events = [_ev(second=i) for i in range(5)] + [
        _ev(second=i + 5, outcome="incomplete") for i in range(5)
    ]
    res = possession.possession_from_events(events)
    assert res.pass_possession_pct == pytest.approx(100.0)


def test_field_tilt_uses_final_third_touches():
    events = [_ev(second=i, x=95.0) for i in range(8)] + [
        _ev(second=i, x=95.0, is_own_team=False) for i in range(2)
    ]
    res = possession.possession_from_events(events)
    assert res.field_tilt_pct == pytest.approx(80.0)


def test_ppda_lower_means_more_aggressive_pressing():
    """PPDA = opponent passes in their own 60% / our defensive actions there."""
    opp_passes = [_ev(second=i, is_own_team=False, x=20.0) for i in range(20)]
    aggressive = opp_passes + [
        _ev(second=i, type="tackle", x=80.0) for i in range(10)
    ]
    passive = opp_passes + [_ev(second=i, type="tackle", x=80.0) for i in range(2)]

    assert possession.possession_from_events(aggressive).ppda == pytest.approx(2.0)
    assert possession.possession_from_events(passive).ppda == pytest.approx(10.0)


def test_possession_on_empty_input_returns_nulls_not_zeros():
    res = possession.possession_from_events([])
    assert res.pass_possession_pct is None
    assert res.time_possession_pct is None


def test_tracking_possession_uses_feed_labels():
    sim = simulate_match(minutes=6, frame_hz=5, seed=7)
    res = possession.possession_from_tracking(sim.frames)
    assert res.time_possession_pct is not None
    assert 0 <= res.time_possession_pct <= 100


def test_merge_prefers_primary_but_fills_gaps():
    a = possession.PossessionResult(time_possession_pct=55.0, method="tracking")
    b = possession.PossessionResult(pass_possession_pct=48.0, ppda=9.0, method="events")
    merged = possession.merge(a, b)
    assert merged.time_possession_pct == 55.0
    assert merged.pass_possession_pct == 48.0
    assert merged.ppda == 9.0


# --- Formation -------------------------------------------------------------

def _positions_for(shape: str) -> dict[str, tuple[float, float]]:
    from app.models.catalog import POSITION_ANCHOR
    from app.services.ml.lineup_optimizer import FORMATION_SLOTS

    return {
        f"p{i}": POSITION_ANCHOR[pos] for i, pos in enumerate(FORMATION_SLOTS[shape])
    }


@pytest.mark.parametrize("shape", ["4-3-3", "4-4-2", "3-5-2", "4-2-3-1"])
def test_formation_detection_recovers_the_shape(shape):
    result = formation.detect_formation(_positions_for(shape), goalkeeper_id="p0")
    total = sum(result.line_counts)
    assert total == 10, f"expected 10 outfielders, got {result.line_counts}"
    assert result.confidence > 0


def test_formation_reports_compactness_and_line_height():
    result = formation.detect_formation(_positions_for("4-3-3"), goalkeeper_id="p0")
    assert result.vertical_compactness > 0
    assert 0 < result.defensive_line_height < 105


def test_formation_needs_enough_players():
    result = formation.detect_formation({"a": (10, 10), "b": (20, 20)})
    assert result.formation == "unknown"


def test_shape_deviation_flags_a_mismatch():
    detected = formation.detect_formation(_positions_for("3-5-2"), goalkeeper_id="p0")
    same = formation.shape_deviation(detected.formation, detected)
    assert same["matches"] is True

    differing = formation.shape_deviation("4-4-2", detected)
    assert differing["deviation"] is not None


# --- Heatmaps --------------------------------------------------------------

def test_heatmap_normalises_to_one():
    hm = heatmap.build_heatmap([(50, 34), (60, 40), (55, 30)])
    assert hm.grid.sum() == pytest.approx(1.0)
    assert hm.method == "kde"


def test_heatmap_switches_to_histogram_when_dense():
    points = [(50 + i % 10, 34 + i % 7) for i in range(500)]
    assert heatmap.build_heatmap(points).method == "histogram"


def test_heatmap_centroid_tracks_the_points():
    hm = heatmap.build_heatmap([(80, 20), (82, 22), (78, 18)])
    assert hm.centroid[0] == pytest.approx(80, abs=1.5)
    assert hm.centroid[1] == pytest.approx(20, abs=1.5)


def test_heatmap_of_nothing_is_empty_not_an_error():
    hm = heatmap.build_heatmap([])
    assert hm.method == "empty"
    assert hm.grid.sum() == 0


def test_heatmap_clips_out_of_bounds_points():
    hm = heatmap.build_heatmap([(500, 500), (-20, -5)])
    assert hm.grid.sum() == pytest.approx(1.0)
    assert 0 <= hm.centroid[0] <= 105


def test_zone_control_is_signed_and_bounded():
    zones = heatmap.zone_control([(90, 34)] * 10, [(20, 34)] * 10)
    grid = np.array(zones["control"])
    assert grid.min() >= -1.0 and grid.max() <= 1.0
    # Our zone in their third should read as ours.
    assert grid[2][5] > 0


# --- The simulator itself --------------------------------------------------

def test_simulation_is_deterministic():
    a = simulate_match(minutes=5, seed=99)
    b = simulate_match(minutes=5, seed=99)
    assert len(a.events) == len(b.events)
    assert a.events[10].x == b.events[10].x


def test_simulation_produces_plausible_football():
    sim = simulate_match(minutes=90, seed=1)
    types = {e.type for e in sim.events}
    assert {"pass", "shot"} <= types
    passes = [e for e in sim.events if e.type == "pass"]
    completion = sum(1 for e in passes if e.outcome == "success") / len(passes)
    assert 0.6 < completion < 0.98, f"pass completion {completion:.2%} is not football"

    for e in sim.events:
        assert 0 <= e.x <= 105 and 0 <= e.y <= 68


def test_simulated_stronger_side_gets_more_of_the_ball():
    dominant = simulate_match(minutes=45, seed=3, home_strength=0.75)
    res = possession.possession_from_events(dominant.events)
    assert res.pass_possession_pct > 50


def test_gaussian_blur_conserves_mass():
    grid = np.zeros((21, 32))
    grid[10, 16] = 1.0
    blurred = heatmap._gaussian_blur(grid, sigma_cells=1.5)
    assert blurred.sum() == pytest.approx(1.0, rel=1e-6)
    assert blurred[10, 16] < 1.0
    assert math.isclose(blurred[10, 15], blurred[10, 17], rel_tol=1e-9)
