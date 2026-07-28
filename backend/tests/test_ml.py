"""ML engines: fit, fatigue, XI selection, substitutions, transfers, academy."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from app.models.academy import Pathway
from app.models.catalog import Foot, Position
from app.services.ml import academy as academy_engine
from app.services.ml import lineup_optimizer, substitution
from app.services.ml import transfer as transfer_engine
from app.services.ml.features import (
    age_curve,
    effective_level,
    fatigue_state,
    position_fit,
)
from app.services.ml.registry import get_model


def player(**kw):
    base = dict(
        id=kw.pop("id", "p1"), name="Player", known_as="Player",
        primary_position=Position.CM, secondary_positions=[], preferred_foot=Foot.right,
        overall_rating=80.0, potential_rating=85.0, attributes={}, age=26.0,
        fitness=100.0, fatigue=0.0, minutes_last_7d=0, is_available=True,
        shirt_number=None,
    )
    base.update(kw)
    base.setdefault("display_name", base["known_as"] or base["name"])
    return SimpleNamespace(**base)


# --- Positional fit --------------------------------------------------------

def test_natural_position_is_a_perfect_fit():
    p = player(primary_position=Position.ST)
    assert position_fit(p, Position.ST) == pytest.approx(1.0, abs=0.02)


def test_fit_ordering_natural_bucket_line_other():
    p = player(primary_position=Position.CF)
    natural = position_fit(p, Position.CF)
    bucket = position_fit(p, Position.ST)        # same role bucket
    line = position_fit(p, Position.LW)          # same line
    other = position_fit(p, Position.CB)         # different line
    assert natural > bucket > line > other


def test_secondary_position_beats_a_bare_line_match():
    versatile = player(primary_position=Position.CM, secondary_positions=["LB"])
    plain = player(primary_position=Position.CM)
    assert position_fit(versatile, Position.LB) > position_fit(plain, Position.LB)


def test_goalkeepers_and_outfielders_never_swap():
    gk = player(primary_position=Position.GK)
    striker = player(primary_position=Position.ST)
    assert position_fit(gk, Position.ST) < 0.1
    assert position_fit(striker, Position.GK) < 0.1


def test_effective_level_damps_the_fit_penalty():
    """A small positional compromise must not cost a rating point per percent."""
    p = player(primary_position=Position.CF, overall_rating=90)
    fit = position_fit(p, Position.ST)
    raw = 90 * fit
    damped = effective_level(p, Position.ST)
    assert damped > raw
    assert damped < 90


# --- Fatigue ---------------------------------------------------------------

def test_fresh_player_is_at_full_level():
    fs = fatigue_state(minutes_played=0, age=26, stamina=80)
    assert fs.performance_multiplier == pytest.approx(1.0, abs=0.01)


def test_performance_declines_over_the_match():
    early = fatigue_state(minutes_played=30, age=26, stamina=80)
    late = fatigue_state(minutes_played=85, age=26, stamina=80)
    assert late.performance_multiplier < early.performance_multiplier


def test_low_stamina_brings_the_decline_forward():
    strong = fatigue_state(minutes_played=75, age=26, stamina=95)
    weak = fatigue_state(minutes_played=75, age=26, stamina=55)
    assert weak.drivers["decline_onset_minute"] < strong.drivers["decline_onset_minute"]
    assert weak.performance_multiplier < strong.performance_multiplier


def test_a_congested_week_brings_the_decline_forward():
    rested = fatigue_state(minutes_played=70, age=27, stamina=80, minutes_last_7d=0)
    loaded = fatigue_state(minutes_played=70, age=27, stamina=80, minutes_last_7d=270)
    assert loaded.performance_multiplier < rested.performance_multiplier
    assert loaded.injury_hazard > rested.injury_hazard


def test_injury_hazard_rises_late_and_with_load():
    early = fatigue_state(minutes_played=20, age=30, stamina=75, minutes_last_7d=250)
    late = fatigue_state(minutes_played=88, age=30, stamina=75, minutes_last_7d=250)
    assert late.injury_hazard > early.injury_hazard
    assert 0 < late.injury_hazard < 1


def test_performance_multiplier_has_a_floor():
    fs = fatigue_state(minutes_played=200, age=39, stamina=20,
                       minutes_last_7d=800, baseline_fatigue=100)
    assert fs.performance_multiplier >= 0.55


# --- Age curve -------------------------------------------------------------

def test_age_curve_peaks_and_declines():
    assert age_curve(18, Position.ST) < age_curve(27, Position.ST)
    assert age_curve(34, Position.ST) < age_curve(27, Position.ST)


def test_keepers_age_more_gently_than_wingers():
    assert age_curve(34, Position.GK) > age_curve(34, Position.RW)


# --- Hungarian assignment --------------------------------------------------

def test_hungarian_finds_the_optimal_assignment():
    cost = np.array([[4.0, 1.0, 3.0], [2.0, 0.0, 5.0], [3.0, 2.0, 2.0]])
    pairs = lineup_optimizer.hungarian(cost)
    total = sum(cost[r, c] for r, c in pairs)
    # Brute force the optimum for a 3x3.
    from itertools import permutations
    best = min(sum(cost[i, p[i]] for i in range(3)) for p in permutations(range(3)))
    assert total == pytest.approx(best)


def test_hungarian_beats_greedy_where_greedy_traps_itself():
    cost = np.array([[1.0, 2.0], [1.1, 9.0]])
    pairs = dict(lineup_optimizer.hungarian(cost))
    # Greedy takes (0,0)=1.0 then is forced into (1,1)=9.0, total 10.0.
    assert sum(cost[r, c] for r, c in pairs.items()) == pytest.approx(3.1)


# --- Best XI ---------------------------------------------------------------

def _squad():
    spec = [
        (Position.GK, 85), (Position.LB, 80), (Position.LCB, 82), (Position.RCB, 81),
        (Position.RB, 79), (Position.DM, 83), (Position.CM, 84), (Position.CM, 78),
        (Position.LW, 86), (Position.ST, 88), (Position.RW, 82),
        (Position.CB, 74), (Position.CM, 72), (Position.ST, 75), (Position.GK, 70),
    ]
    return [player(id=f"p{i}", primary_position=pos, overall_rating=ovr, known_as=f"P{i}")
            for i, (pos, ovr) in enumerate(spec)]


def test_best_xi_fills_every_slot():
    xi = lineup_optimizer.best_xi(_squad(), "4-3-3")
    assert len(xi.slots) == 11
    assert all(s["player_id"] for s in xi.slots)
    assert len({s["player_id"] for s in xi.slots}) == 11


def test_best_xi_puts_the_keeper_in_goal():
    xi = lineup_optimizer.best_xi(_squad(), "4-3-3")
    gk_slot = next(s for s in xi.slots if s["position"] == "GK")
    assert gk_slot["natural_position"] == "GK"


def test_best_xi_respects_locked_slots():
    squad = _squad()
    xi = lineup_optimizer.best_xi(squad, "4-3-3", locked={9: "p12"})
    slot = next(s for s in xi.slots if s["slot_index"] == 9)
    assert slot["player_id"] == "p12"
    assert slot["locked"] is True


def test_best_xi_excludes_unavailable_players():
    squad = _squad()
    squad[9].is_available = False           # the 88-rated striker
    xi = lineup_optimizer.best_xi(squad, "4-3-3")
    assert squad[9].id not in {s["player_id"] for s in xi.slots}


def test_best_xi_flags_out_of_position_players():
    thin = [player(id=f"p{i}", primary_position=Position.CM, overall_rating=80)
            for i in range(11)]
    thin[0].primary_position = Position.GK
    xi = lineup_optimizer.best_xi(thin, "4-3-3")
    assert any(s.get("out_of_position") for s in xi.slots)
    assert xi.warnings


def test_ignore_load_changes_selection_when_a_starter_is_gassed():
    squad = _squad()
    squad[9].fatigue = 85          # the best striker is cooked
    squad[9].minutes_last_7d = 270
    with_load = lineup_optimizer.best_xi(squad, "4-3-3")
    without = lineup_optimizer.best_xi(squad, "4-3-3", ignore_load=True)
    st_with = next(s for s in with_load.slots if s["position"] == "ST")["player_id"]
    st_without = next(s for s in without.slots if s["position"] == "ST")["player_id"]
    assert st_without == "p9"
    assert st_with != st_without


def test_unknown_formation_is_rejected():
    with pytest.raises(ValueError, match="Unknown formation"):
        lineup_optimizer.best_xi(_squad(), "9-0-1")


def test_compare_formations_is_ranked():
    ranking = lineup_optimizer.compare_formations(_squad(), top_n=4)
    scores = [r["mean_effective_level"] for r in ranking]
    assert scores == sorted(scores, reverse=True)


# --- Substitutions ---------------------------------------------------------

def _match_state(minute=70, fatigue=60, bench_rating=84):
    starters = [
        (player(id="out", primary_position=Position.ST, overall_rating=85,
                known_as="Tired", fatigue=fatigue, minutes_last_7d=270),
         Position.ST, minute),
    ]
    bench = [player(id="in", primary_position=Position.ST,
                    overall_rating=bench_rating, known_as="Fresh")]
    return starters, bench


def test_a_fresh_equal_replaces_an_exhausted_starter():
    starters, bench = _match_state(minute=80, fatigue=80)
    subs = substitution.recommend_substitutions(
        starters=starters, bench=bench, minute=80
    )
    assert subs, "expected a substitution when the starter is spent"
    assert subs[0].player_in_id == "in"
    assert subs[0].expected_gain > 0


def test_no_substitution_when_the_bench_is_clearly_worse():
    starters, bench = _match_state(minute=20, fatigue=0, bench_rating=60)
    subs = substitution.recommend_substitutions(
        starters=starters, bench=bench, minute=20
    )
    assert subs == []


def test_substitution_budget_is_respected():
    starters, bench = _match_state(minute=80, fatigue=80)
    assert substitution.recommend_substitutions(
        starters=starters, bench=bench, minute=80, subs_used=5
    ) == []
    assert substitution.recommend_substitutions(
        starters=starters, bench=bench, minute=80, windows_used=3
    ) == []


def test_no_substitution_at_full_time():
    starters, bench = _match_state(minute=90, fatigue=80)
    assert substitution.recommend_substitutions(
        starters=starters, bench=bench, minute=90
    ) == []


def test_goalkeepers_are_not_rotated_tactically():
    starters = [(player(id="gk", primary_position=Position.GK, overall_rating=70,
                        fatigue=90), Position.GK, 80)]
    bench = [player(id="gk2", primary_position=Position.GK, overall_rating=88)]
    assert substitution.recommend_substitutions(
        starters=starters, bench=bench, minute=80
    ) == []


def test_a_booked_player_raises_the_priority():
    starters, bench = _match_state(minute=60, fatigue=50)
    card = SimpleNamespace(
        player_id="out", type="card", qualifiers={"card": "yellow"},
        period=1, minute=30, second=0, is_own_team=True, outcome="", x=0, y=0,
    )
    plain = substitution.recommend_substitutions(
        starters=starters, bench=bench, minute=60, events=[]
    )
    booked = substitution.recommend_substitutions(
        starters=starters, bench=bench, minute=60, events=[card]
    )
    assert booked and plain
    assert booked[0].priority > plain[0].priority
    assert any("yellow" in d for d in booked[0].drivers)


def test_each_player_appears_once_in_the_recommendations():
    starters = [
        (player(id=f"s{i}", primary_position=Position.CM, overall_rating=78,
                fatigue=75, minutes_last_7d=270), Position.CM, 80)
        for i in range(3)
    ]
    bench = [player(id=f"b{i}", primary_position=Position.CM, overall_rating=80)
             for i in range(3)]
    subs = substitution.recommend_substitutions(starters=starters, bench=bench, minute=80)
    assert len({s.player_out_id for s in subs}) == len(subs)
    assert len({s.player_in_id for s in subs}) == len(subs)


def test_workload_alerts_flag_overloaded_players():
    starters = [
        (player(id="a", minutes_last_7d=280, fatigue=70), Position.CM, 90),
        (player(id="b", minutes_last_7d=0, fatigue=2), Position.CM, 90),
    ]
    alerts = substitution.workload_alerts(starters)
    assert [a["player_id"] for a in alerts] == ["a"]


# --- Transfers -------------------------------------------------------------

def market_player(**kw):
    base = dict(
        id=kw.pop("id", "m1"), name="Target", primary_position=Position.CB,
        secondary_positions=[], preferred_foot=Foot.right, overall_rating=84.0,
        potential_rating=88.0, attributes={}, age=25.0, league_tier=1,
        asking_price_eur=30_000_000, wage_demand_eur_per_year=4_000_000,
        agent_fee_pct=0.05, release_clause_eur=None, injury_history_days_2y=0,
        minutes_last_season=2500, availability=0.8, current_club="Club",
        league="League", deal_type=None,
    )
    base.update(kw)
    base["total_cost_eur"] = int(base["asking_price_eur"] * (1 + base["agent_fee_pct"]))
    return SimpleNamespace(**base)


def test_needs_detects_a_position_with_no_cover():
    squad = [player(id=f"p{i}", primary_position=Position.CM, overall_rating=80)
             for i in range(11)]
    needs = transfer_engine.detect_needs(squad, relevant_positions={Position.ST})
    assert needs and needs[0].position == Position.ST
    assert needs[0].severity >= 0.75


def test_unavailable_players_do_not_count_as_depth():
    squad = [
        player(id="a", primary_position=Position.RCB, overall_rating=90),
        player(id="b", primary_position=Position.RCB, overall_rating=88),
    ] + [player(id=f"m{i}", primary_position=Position.CM, overall_rating=80) for i in range(9)]

    healthy = transfer_engine.detect_needs(squad, relevant_positions={Position.RCB})
    for p in squad[:2]:
        p.is_available = False
    injured = transfer_engine.detect_needs(squad, relevant_positions={Position.RCB})

    healthy_sev = healthy[0].severity if healthy else 0.0
    assert injured and injured[0].severity > healthy_sev


def test_scan_is_limited_to_positions_the_formation_uses():
    positions = transfer_engine.positions_for_formations(["4-3-3"])
    assert Position.RWB not in positions
    assert Position.ST in positions
    assert Position.RWB in transfer_engine.positions_for_formations(["3-5-2"])


def test_league_strength_discounts_lower_tiers():
    squad = [player(id=f"p{i}", primary_position=Position.CM, overall_rating=70)
             for i in range(11)]
    needs = transfer_engine.detect_needs(squad, relevant_positions={Position.CB})
    top = market_player(id="top", league_tier=1)
    lower = market_player(id="low", league_tier=5)
    scored = transfer_engine.score_targets([top, lower], needs, squad_players=squad)
    by_id = {t.market_player.id: t for t in scored}
    assert by_id["top"].quality > by_id["low"].quality


def test_bundle_respects_both_budgets():
    squad = [player(id=f"p{i}", primary_position=Position.CM, overall_rating=65)
             for i in range(11)]
    needs = transfer_engine.detect_needs(squad, relevant_positions={Position.CB, Position.ST})
    market = [
        market_player(id="cheap", primary_position=Position.CB,
                      asking_price_eur=10_000_000, wage_demand_eur_per_year=1_000_000),
        market_player(id="dear", primary_position=Position.ST, overall_rating=90,
                      asking_price_eur=200_000_000, wage_demand_eur_per_year=30_000_000),
    ]
    targets = transfer_engine.score_targets(market, needs, squad_players=squad)
    bundle, info = transfer_engine.select_bundle(
        targets, budget_eur=30_000_000, wage_budget_eur=3_000_000
    )
    assert {t.market_player.id for t in bundle} == {"cheap"}
    assert info["total_fee_eur"] <= 30_000_000
    assert info["total_wage_eur_per_year"] <= 3_000_000


def test_bundle_never_signs_the_same_player_twice():
    squad = [player(id=f"p{i}", primary_position=Position.CM, overall_rating=60)
             for i in range(11)]
    needs = transfer_engine.detect_needs(
        squad, relevant_positions={Position.LCB, Position.RCB}
    )
    versatile = market_player(id="one", primary_position=Position.CB,
                              secondary_positions=["LCB", "RCB"], overall_rating=85)
    targets = transfer_engine.score_targets([versatile], needs, squad_players=squad)
    assert len(targets) >= 2, "the same player should score against both needs"
    bundle, _ = transfer_engine.select_bundle(
        targets, budget_eur=200_000_000, wage_budget_eur=50_000_000, max_signings=4
    )
    assert len({t.market_player.id for t in bundle}) == len(bundle)


def test_release_clause_beats_a_higher_asking_price():
    squad = [player(id=f"p{i}", primary_position=Position.CM, overall_rating=60)
             for i in range(11)]
    needs = transfer_engine.detect_needs(squad, relevant_positions={Position.CB})
    mp = market_player(asking_price_eur=90_000_000, release_clause_eur=40_000_000)
    targets = transfer_engine.score_targets([mp], needs, squad_players=squad)
    assert targets[0].effective_cost < 50_000_000


def test_bundle_reports_why_nothing_fits():
    _bundle, info = transfer_engine.select_bundle([], budget_eur=0, wage_budget_eur=0)
    assert "reason" in info


# --- Academy ---------------------------------------------------------------

def academy_player(*, ability=70, potential=85, age=18.0, bio=0.0,
                   growth=4.0, n_assessments=4, senior_minutes=0):
    today = date.today()
    assessments = []
    for i in range(n_assessments):
        months_ago = (n_assessments - 1 - i) * 6
        assessments.append(SimpleNamespace(
            assessed_on=today - timedelta(days=int(months_ago * 30.44)),
            ability=ability - growth * (months_ago / 12.0),
            technical=ability, tactical=ability, physical=ability, mental=ability,
            level="academy",
        ))
    return SimpleNamespace(
        id="y1", name="Prospect", primary_position=Position.CM,
        birth_date=today - timedelta(days=int(age * 365.25)), age=age,
        current_ability=ability, potential_ability=potential,
        biological_age_offset=bio, minutes_this_season=1500,
        senior_minutes=senior_minutes, assessments=assessments,
    )


def _senior(rating=75):
    return [player(id=f"s{i}", overall_rating=rating + i) for i in range(8)]


def test_faster_growth_means_an_earlier_arrival():
    slow = academy_engine.project(academy_player(growth=1.0), senior_players=_senior())
    fast = academy_engine.project(academy_player(growth=8.0), senior_players=_senior())
    assert fast.months_to_first_team < slow.months_to_first_team


def test_a_ceiling_below_the_bar_is_not_a_promotion_path():
    proj = academy_engine.project(
        academy_player(ability=55, potential=60), senior_players=_senior(rating=85)
    )
    assert proj.months_to_first_team is None
    assert proj.pathway in {Pathway.review, Pathway.release}
    assert any("ceiling" in w for w in proj.warnings)


def test_late_developer_ability_is_adjusted_upward():
    plain = academy_engine.project(academy_player(bio=0.0), senior_players=_senior())
    late = academy_engine.project(academy_player(bio=-2.0), senior_players=_senior())
    assert late.adjusted_ability > plain.adjusted_ability
    assert any("Late developer" in d for d in late.drivers)


def test_early_maturer_is_warned_about():
    early = academy_engine.project(academy_player(bio=1.5), senior_players=_senior())
    assert any("Early maturer" in w for w in early.warnings)


def test_a_player_already_at_the_bar_is_ready_now():
    proj = academy_engine.project(
        academy_player(ability=90, potential=95), senior_players=_senior(rating=70)
    )
    assert proj.months_to_first_team == 0.0
    assert proj.pathway == Pathway.promote_now


def test_few_assessments_lowers_confidence_and_warns():
    thin = academy_engine.project(academy_player(n_assessments=1), senior_players=_senior())
    rich = academy_engine.project(academy_player(n_assessments=6), senior_players=_senior())
    assert thin.confidence < rich.confidence
    assert any("assessment" in w for w in thin.warnings)


def test_first_team_bar_scales_with_the_senior_squad():
    weak = academy_engine.first_team_bar(_senior(rating=60))
    strong = academy_engine.first_team_bar(_senior(rating=88))
    assert strong > weak


def test_trajectory_is_monotonic_and_capped_by_potential():
    proj = academy_engine.project(academy_player(potential=82), senior_players=_senior())
    values = [p["ability"] for p in proj.trajectory]
    assert values == sorted(values)
    assert max(values) <= 82.001


def test_review_sorts_ready_players_first():
    players_ = [
        academy_player(ability=50, potential=90, growth=2.0),
        academy_player(ability=95, potential=99),   # ready now
    ]
    players_[0].id, players_[1].id = "slow", "ready"
    result = academy_engine.review_squad(players_, _senior(rating=70))
    assert result["projections"][0]["academy_player_id"] == "ready"


def test_relative_age_quartile():
    assert academy_engine.relative_age_quartile(date(2008, 1, 15)) == 1
    assert academy_engine.relative_age_quartile(date(2008, 11, 3)) == 4
    assert academy_engine.relative_age_quartile(None) is None


# --- Model registry --------------------------------------------------------

@pytest.mark.parametrize("name", ["impact", "academy"])
def test_models_load_and_report_metrics(name):
    bundle = get_model(name)
    assert bundle.version
    assert bundle.provenance == "bootstrap"
    assert bundle.metrics and bundle.feature_names


def test_impact_model_rewards_a_better_player():
    model = get_model("impact")
    row = {
        "effective_level": 70, "position_fit": 0.9, "performance_multiplier": 1.0,
        "minutes_remaining": 45, "fresh_legs_edge": 0.0, "tactical_need": 0.0,
        "score_state": 0,
    }
    better = dict(row, effective_level=88)
    assert model.predict([better])[0] > model.predict([row])[0]
