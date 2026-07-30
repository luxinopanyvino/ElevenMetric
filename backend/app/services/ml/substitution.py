"""Substitution engine.

Answers three questions in one pass: **who to take off**, **who to bring on**,
and **when**. The output is ordered by expected gain in xGD over the remaining
minutes, and every recommendation carries the numbers that produced it.

Laws-of-the-game constraints are enforced: five substitutions across three
stoppages (plus half-time, which does not count against the window budget).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.catalog import Position
from app.services.ml.features import (
    UNRANKED_FALLBACK as UNRANKED,
    attribute,
    fatigue_state,
    is_rankable,
    player_feature_vector,
    position_fit,
    rating_or,
)
from app.services.ml.registry import get_model

MAX_SUBSTITUTIONS = 5
MAX_WINDOWS = 3
#: Minutes a substitute needs before contributing at full level.
ADAPTATION_MINUTES = 6.0


@dataclass
class SubCandidate:
    player_out_id: str
    player_out_name: str
    player_in_id: str
    player_in_name: str
    position: Position
    minute_from: int
    minute_to: int
    expected_gain: float          # xGD over the remaining minutes
    confidence: float
    priority: float
    drivers: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "player_out_id": self.player_out_id,
            "player_out": self.player_out_name,
            "player_in_id": self.player_in_id,
            "player_in": self.player_in_name,
            "position": self.position.value,
            "minute_window": f"{self.minute_from}-{self.minute_to}",
            "expected_gain": round(self.expected_gain, 4),
            "expected_gain_unit": "xGD (rest of match)",
            "confidence": round(self.confidence, 3),
            "priority": round(self.priority, 1),
            "drivers": self.drivers,
            "evidence": self.evidence,
        }


def _tactical_need(position: Position, vulnerabilities: list[dict]) -> tuple[float, str | None]:
    """How much a change at ``position`` addresses a diagnosed weakness."""
    if not vulnerabilities:
        return 0.0, None

    from app.models.catalog import POSITION_LINE

    line = POSITION_LINE.get(position, "MID")
    best, reason = 0.0, None
    for v in vulnerabilities:
        vid = v.get("id", "")
        sev = float(v.get("severity", 50)) / 100.0
        weight = 0.0
        if vid.startswith("lane_leak"):
            lane = vid.replace("lane_leak_", "").replace("_", " ")
            side_positions = {
                "left": {Position.LB, Position.LWB, Position.LM, Position.LW, Position.LCB},
                "right": {Position.RB, Position.RWB, Position.RM, Position.RW, Position.RCB},
            }
            if "left" in lane and position in side_positions["left"]:
                weight = 0.9
            elif "right" in lane and position in side_positions["right"]:
                weight = 0.9
            elif "centre" in lane and line in {"DEF", "MID"}:
                weight = 0.6
        elif vid == "press_without_compactness" and line in {"MID", "DEF"}:
            weight = 0.7
        elif vid == "weak_counterpress" and line in {"MID", "ATT"}:
            weight = 0.75
        elif vid == "final_third_stall" and line == "ATT":
            weight = 0.85
        elif vid == "low_shot_quality" and line == "ATT":
            weight = 0.7

        score = weight * sev
        if score > best:
            best, reason = score, v.get("title")
    return min(1.0, best), reason


def _card_risk(player, events: list, player_id: str) -> tuple[float, bool]:
    """Probability of a second yellow before full time, and whether booked."""
    booked = False
    fouls = 0
    for e in events:
        if e.player_id != player_id:
            continue
        etype = (e.type or "").lower()
        if etype == "card" and (e.qualifiers or {}).get("card") == "yellow":
            booked = True
        elif etype == "foul":
            fouls += 1
    if not booked:
        return 0.0, False
    # A booked player committing fouls at a high rate is the classic forced sub.
    base = 0.14 + 0.06 * fouls
    aggression = attribute(player, "defending", 70.0) / 100.0
    return float(min(0.62, base * (0.8 + 0.4 * aggression))), True


def recommend_substitutions(
    *,
    starters: list,                       # (player, position, minutes_played)
    bench: list,                          # player objects
    minute: int,
    score_difference: int = 0,
    vulnerabilities: list[dict] | None = None,
    events: list | None = None,
    subs_used: int = 0,
    windows_used: int = 0,
    max_results: int = 6,
    match_length: int = 90,
) -> list[SubCandidate]:
    """Rank substitution options.

    ``starters`` is a list of ``(player, Position, minutes_played)`` tuples.
    """
    vulnerabilities = vulnerabilities or []
    events = events or []
    model = get_model("impact")

    # Swapping an ungraded player in or out cannot be justified by a level
    # difference nobody has measured, so both sides of the swap must be graded.
    starters = [row for row in starters if is_rankable(row[0])]
    bench = [p for p in bench if is_rankable(p)]

    minutes_remaining = max(0, match_length - minute)
    if minutes_remaining <= 2 or subs_used >= MAX_SUBSTITUTIONS or windows_used >= MAX_WINDOWS:
        return []

    candidates: list[SubCandidate] = []

    for player_out, pos, mins in starters:
        if pos == Position.GK:
            continue  # keepers are only changed for injury, which is not modelled here

        out_feat = player_feature_vector(player_out, minute=mins, target=pos)
        out_fs = fatigue_state(
            minutes_played=mins,
            age=getattr(player_out, "age", None),
            stamina=attribute(player_out, "stamina", rating_or(player_out, UNRANKED)),
            minutes_last_7d=getattr(player_out, "minutes_last_7d", 0) or 0,
            baseline_fatigue=getattr(player_out, "fatigue", 0.0) or 0.0,
        )
        need, need_reason = _tactical_need(pos, vulnerabilities)
        card_p, booked = _card_risk(player_out, events, player_out.id)

        out_rows = [{
            "effective_level": out_feat["effective_level"],
            "position_fit": out_feat["position_fit"],
            "performance_multiplier": out_fs.performance_multiplier,
            "minutes_remaining": minutes_remaining,
            "fresh_legs_edge": 0.0,
            "tactical_need": 0.0,
            "score_state": score_difference,
        }]
        out_value = float(model.predict(out_rows)[0])

        for player_in in bench:
            if not getattr(player_in, "is_available", True):
                continue
            if player_in.id == player_out.id:
                continue
            fit = position_fit(player_in, pos)
            if fit < 0.45:
                continue

            in_feat = player_feature_vector(player_in, minute=0, target=pos)
            fresh_edge = max(0.0, 1.0 - out_fs.performance_multiplier)

            in_rows = [{
                "effective_level": in_feat["effective_level"],
                "position_fit": fit,
                "performance_multiplier": 1.0,
                "minutes_remaining": minutes_remaining,
                "fresh_legs_edge": fresh_edge,
                "tactical_need": need,
                "score_state": score_difference,
            }]
            in_value = float(model.predict(in_rows)[0])

            # A substitute needs a few minutes to get into the game, so only
            # part of their time on the pitch is spent at full effectiveness.
            # This damps the *gain*; applying it to `effective_level` would
            # rescale the rating itself and turn an 84 into a 52.
            adaptation = minutes_remaining / (minutes_remaining + ADAPTATION_MINUTES)
            gain = (in_value - out_value) * adaptation
            # Injury avoided is worth something even when the swap is neutral.
            gain += out_fs.injury_hazard * 0.22
            gain += card_p * 0.30

            # Chasing a goal? weight attackers up, protecting a lead? down.
            from app.models.catalog import POSITION_LINE
            line = POSITION_LINE.get(pos, "MID")
            if score_difference < 0 and line == "ATT":
                gain *= 1.15
            elif score_difference > 0 and line == "DEF":
                gain *= 1.10

            if gain <= 0.002:
                continue

            drivers: list[str] = []
            if out_fs.performance_multiplier < 0.90:
                drivers.append(
                    f"{player_out.display_name} is at "
                    f"{out_fs.performance_multiplier * 100:.0f}% of level after {mins}' "
                    f"(decline began around minute {out_fs.drivers['decline_onset_minute']:.0f})"
                )
            if in_feat["effective_level"] > out_feat["effective_level"]:
                drivers.append(
                    f"{player_in.display_name} projects "
                    f"{in_feat['effective_level'] - out_feat['effective_level']:.1f} "
                    "effective-level points higher right now"
                )
            if need_reason:
                drivers.append(f"Addresses: {need_reason}")
            if booked:
                drivers.append(
                    f"Booked, with a {card_p * 100:.0f}% modelled risk of a second yellow"
                )
            if out_fs.injury_hazard > 0.12:
                drivers.append(
                    f"Injury hazard elevated at {out_fs.injury_hazard * 100:.1f}% "
                    f"({getattr(player_out, 'minutes_last_7d', 0)}' in the last 7 days)"
                )
            if fit < 0.8:
                drivers.append(
                    f"Positional fit at {pos.value} is only {fit:.0%} — a compromise, not a like-for-like"
                )

            # Timing: bring the change forward when fatigue or card risk drive it.
            urgency = out_fs.injury_hazard * 2.2 + card_p * 1.4 + (1 - out_fs.performance_multiplier) * 2.0
            offset = int(max(0, 12 - urgency * 14))
            m_from = min(match_length - 1, minute + offset)
            m_to = min(match_length, m_from + 10)

            confidence = float(
                min(0.94, 0.42 + 0.30 * fit + 0.18 * min(1.0, mins / 60.0) + 0.12 * need)
            )

            candidates.append(SubCandidate(
                player_out_id=player_out.id,
                player_out_name=player_out.display_name,
                player_in_id=player_in.id,
                player_in_name=player_in.display_name,
                position=pos,
                minute_from=m_from,
                minute_to=m_to,
                expected_gain=gain,
                confidence=confidence,
                # Gains live in the 0.005-0.15 xGD range, so the scale is chosen
                # to spread that band across the usable part of 0-100.
                priority=float(min(100.0, 30 + gain * 400 + card_p * 55
                                   + out_fs.injury_hazard * 90)),
                drivers=drivers,
                evidence={
                    "out": out_feat,
                    "in": in_feat,
                    "position_fit_in": round(fit, 3),
                    "tactical_need": round(need, 3),
                    "card_risk": round(card_p, 3),
                    "minutes_remaining": minutes_remaining,
                    "model_version": model.version,
                },
            ))

    candidates.sort(key=lambda c: -c.expected_gain)

    # One recommendation per outgoing player, and respect the remaining budget.
    seen_out: set[str] = set()
    seen_in: set[str] = set()
    final: list[SubCandidate] = []
    budget = MAX_SUBSTITUTIONS - subs_used
    for c in candidates:
        if c.player_out_id in seen_out or c.player_in_id in seen_in:
            continue
        seen_out.add(c.player_out_id)
        seen_in.add(c.player_in_id)
        final.append(c)
        if len(final) >= min(max_results, budget):
            break
    return final


def workload_alerts(starters: list, *, minute: int = 90) -> list[dict]:
    """Players whose accumulated load warrants rotation, independent of form."""
    alerts = []
    for player, pos, mins in starters:
        fs = fatigue_state(
            minutes_played=mins,
            age=getattr(player, "age", None),
            stamina=attribute(player, "stamina", rating_or(player, UNRANKED)),
            minutes_last_7d=getattr(player, "minutes_last_7d", 0) or 0,
            baseline_fatigue=getattr(player, "fatigue", 0.0) or 0.0,
        )
        m7 = getattr(player, "minutes_last_7d", 0) or 0
        if fs.injury_hazard < 0.14 and m7 < 240:
            continue
        alerts.append({
            "player_id": player.id,
            "player": player.display_name,
            "position": pos.value if hasattr(pos, "value") else str(pos),
            "injury_hazard": fs.injury_hazard,
            "fatigue_index": fs.fatigue_index,
            "minutes_last_7d": m7,
            "recommendation": (
                "Rest for the next fixture"
                if m7 >= 270
                else "Cap minutes and monitor"
            ),
            "drivers": fs.drivers,
        })
    return sorted(alerts, key=lambda a: -a["injury_hazard"])
