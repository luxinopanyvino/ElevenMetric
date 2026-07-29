"""Feature engineering shared by the ML engines.

The two non-obvious pieces here are the **fatigue curve** and **positional
fit**; everything downstream leans on both.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.models.catalog import POSITION_ANCHOR, POSITION_LINE, Position

# --- Attribute vocabulary --------------------------------------------------
#
# Three layers, mirroring how scouting databases and football games are
# organised. A club can supply only the headline six and everything still
# works; supplying the detail improves positional fit, which is what drives
# selection, substitutions and transfer scoring.

#: The six summary faces. These are the minimum a club needs to provide.
HEADLINE_KEYS = ("pace", "shooting", "passing", "dribbling", "defending", "physical")

#: Detail attributes, grouped under the headline they roll up to. A missing
#: detail falls back to its group's headline value rather than to the player's
#: overall rating — a striker's `finishing` is far better approximated by their
#: `shooting` than by their average level.
DETAIL_GROUPS: dict[str, tuple[str, ...]] = {
    "pace": ("acceleration", "sprint_speed"),
    "shooting": ("finishing", "shot_power", "long_shots", "volleys", "penalties",
                 "heading_accuracy"),
    "passing": ("vision", "crossing", "free_kick_accuracy", "short_passing",
                "long_passing", "curve"),
    "dribbling": ("agility", "balance", "reactions", "ball_control", "composure"),
    "defending": ("interceptions", "defensive_awareness", "standing_tackle",
                  "sliding_tackle"),
    "physical": ("jumping", "stamina", "strength", "aggression"),
}

#: Goalkeeping is a different sport and needs its own attributes. Judging a
#: keeper on outfield faces — the previous approach here — made every
#: goalkeeping decision the weakest output in the system.
GK_KEYS = ("gk_diving", "gk_handling", "gk_kicking", "gk_reflexes",
           "gk_positioning", "gk_speed")

#: Work rates, off and on the ball.
WORK_RATE_KEYS = ("work_rate_off", "work_rate_def")

#: Everything an ingest may supply. All values are 0-99.
ATTRIBUTE_KEYS: tuple[str, ...] = (
    HEADLINE_KEYS
    + tuple(k for group in DETAIL_GROUPS.values() for k in group)
    + GK_KEYS
    + WORK_RATE_KEYS
)

#: detail key → the headline it rolls up to.
DETAIL_PARENT: dict[str, str] = {
    key: parent for parent, keys in DETAIL_GROUPS.items() for key in keys
}

#: Per-position attribute weights, summing to 1. These encode what the position
#: actually demands and are the basis of `position_fit`.
POSITION_WEIGHTS: dict[str, dict[str, float]] = {
    "GK": {"gk_reflexes": 0.22, "gk_diving": 0.20, "gk_positioning": 0.20,
           "gk_handling": 0.18, "gk_kicking": 0.12, "composure": 0.08},
    "CB": {"defensive_awareness": 0.22, "standing_tackle": 0.18,
           "heading_accuracy": 0.14, "strength": 0.14, "interceptions": 0.12,
           "sprint_speed": 0.10, "short_passing": 0.10},
    "FB": {"sprint_speed": 0.18, "stamina": 0.16, "standing_tackle": 0.14,
           "crossing": 0.14, "defensive_awareness": 0.12, "acceleration": 0.12,
           "short_passing": 0.08, "agility": 0.06},
    "DM": {"interceptions": 0.20, "defensive_awareness": 0.18, "short_passing": 0.16,
           "standing_tackle": 0.14, "strength": 0.12, "long_passing": 0.10,
           "stamina": 0.10},
    "CM": {"short_passing": 0.20, "vision": 0.16, "ball_control": 0.14,
           "long_passing": 0.12, "stamina": 0.12, "defensive_awareness": 0.10,
           "dribbling": 0.10, "composure": 0.06},
    "AM": {"vision": 0.20, "short_passing": 0.16, "dribbling": 0.16,
           "ball_control": 0.14, "finishing": 0.12, "agility": 0.12,
           "composure": 0.10},
    "W":  {"dribbling": 0.18, "acceleration": 0.18, "sprint_speed": 0.16,
           "ball_control": 0.14, "crossing": 0.12, "agility": 0.12,
           "finishing": 0.10},
    "ST": {"finishing": 0.24, "shot_power": 0.16, "composure": 0.14,
           "heading_accuracy": 0.12, "sprint_speed": 0.12, "strength": 0.12,
           "ball_control": 0.10},
}

#: Position → weight bucket.
POSITION_BUCKET: dict[Position, str] = {
    Position.GK: "GK",
    Position.CB: "CB", Position.RCB: "CB", Position.LCB: "CB",
    Position.RB: "FB", Position.LB: "FB", Position.RWB: "FB", Position.LWB: "FB",
    Position.DM: "DM",
    Position.CM: "CM", Position.RM: "CM", Position.LM: "CM",
    Position.AM: "AM", Position.SS: "AM",
    Position.RW: "W", Position.LW: "W",
    Position.CF: "ST", Position.ST: "ST",
}


def attribute(player, key: str, default: float | None = None) -> float:
    """Read an attribute, falling back sensibly when it was never supplied.

    The chain is: the key itself → its headline group (so ``finishing`` falls
    back to ``shooting``, not to the player's average) → the caller's default →
    the overall rating. This is what lets a club supply only the six headline
    faces and still get useful positional fit.
    """
    attrs = getattr(player, "attributes", None) or {}

    val = attrs.get(key)
    if val is not None:
        return float(val)

    parent = DETAIL_PARENT.get(key)
    if parent is not None and attrs.get(parent) is not None:
        return float(attrs[parent])

    if default is not None:
        return float(default)
    return float(getattr(player, "overall_rating", 70.0))


def headline_from_detail(attrs: dict) -> dict:
    """Derive any missing headline face from the detail beneath it.

    Clubs that export a full scouting profile rarely also export the summary
    faces; this fills them so the two representations stay consistent.
    """
    out = dict(attrs)
    for parent, keys in DETAIL_GROUPS.items():
        if out.get(parent) is not None:
            continue
        present = [float(out[k]) for k in keys if out.get(k) is not None]
        if present:
            out[parent] = round(sum(present) / len(present), 1)
    return out


#: How much of a player's rating a bad positional fit costs. A fit of 0.5 with
#: this factor removes 30% of the rating — punitive enough that the optimiser
#: avoids square pegs, mild enough that a 92-rated winger at striker still beats
#: a 78-rated natural.
FIT_PENALTY = 0.60


def position_fit(player, target: Position) -> float:
    """0-1 suitability of ``player`` for ``target``.

    Combines a declared-position term (natural > secondary > same role bucket >
    same line > other) with an attribute-profile term, so a fast, defensively
    sound full-back is rated as a plausible emergency centre-back, but a striker
    is not. The three weights sum to 1, so a natural player with a
    position-typical profile scores exactly 1.0.
    """
    natural = getattr(player, "primary_position", None)
    secondary = set(getattr(player, "secondary_positions", None) or [])

    if natural == target:
        declared = 1.0
    elif target.value in secondary:
        declared = 0.93
    elif natural is not None and POSITION_BUCKET.get(natural) == POSITION_BUCKET.get(target):
        # e.g. CF ↔ ST, RB ↔ LB — the same job on a different patch of grass.
        declared = 0.88
    elif natural is not None and POSITION_LINE.get(natural) == POSITION_LINE.get(target):
        declared = 0.72
    else:
        declared = 0.35

    # A goalkeeper is not a field player and vice-versa, regardless of profile.
    if (natural == Position.GK) != (target == Position.GK):
        return 0.05

    bucket = POSITION_BUCKET.get(target, "CM")
    weights = POSITION_WEIGHTS[bucket]
    overall = float(getattr(player, "overall_rating", 70.0))
    profile = sum(w * attribute(player, k, overall) for k, w in weights.items())
    # Normalise against the player's own overall: >1 means the profile suits the
    # role better than the player's average level suggests.
    profile_ratio = profile / max(overall, 1.0)
    profile_term = min(1.15, max(0.75, profile_ratio))

    # Distance between position anchors damps wild reassignments.
    if natural is not None and natural in POSITION_ANCHOR and target in POSITION_ANCHOR:
        ax, ay = POSITION_ANCHOR[natural]
        bx, by = POSITION_ANCHOR[target]
        dist = math.hypot(ax - bx, ay - by)
        spatial = math.exp(-dist / 55.0)
    else:
        spatial = 0.7

    return float(min(1.0, declared * 0.70 + profile_term * 0.18 + spatial * 0.12))


def effective_level(player, target: Position, performance_multiplier: float = 1.0) -> float:
    """The player's rating as it applies at ``target``, right now.

    Fit is applied as a damped penalty rather than a raw multiplier: a 0.9 fit
    costs 6% of the rating, not 10%. Multiplying directly over-punishes small
    positional compromises, which are routine in real teamsheets.
    """
    overall = float(getattr(player, "overall_rating", 70.0))
    fit = position_fit(player, target)
    return overall * (1.0 - FIT_PENALTY * (1.0 - fit)) * performance_multiplier


@dataclass
class FatigueState:
    #: 0-1 multiplier on the player's effective level right now.
    performance_multiplier: float
    #: 0-100 accumulated fatigue.
    fatigue_index: float
    #: 0-1 probability of a muscle injury in the next 15 minutes.
    injury_hazard: float
    drivers: dict


def fatigue_state(
    *,
    minutes_played: int,
    age: float | None,
    stamina: float,
    minutes_last_7d: int = 0,
    baseline_fatigue: float = 0.0,
    high_intensity_km: float | None = None,
    match_intensity: float = 1.0,
) -> FatigueState:
    """Model in-match decline.

    Shape of the curve: negligible decay for the first ~55 minutes, then an
    accelerating decline whose onset and slope depend on stamina, age and how
    much the player has already played in the preceding week. This matches the
    well-documented drop in high-intensity running in the closing 20 minutes.
    """
    stamina = float(min(max(stamina, 1.0), 99.0))
    age = age if age is not None else 26.0

    # Minute at which decline begins.
    onset = 45.0 + 0.32 * stamina - 0.55 * max(0.0, age - 29.0)
    onset -= 0.012 * minutes_last_7d          # a congested week brings it forward
    onset -= 0.15 * baseline_fatigue
    onset = max(25.0, onset)

    # Slope per minute past the onset.
    slope = 0.0042 + 0.00006 * max(0.0, (75.0 - stamina)) + 0.00008 * max(0.0, age - 30.0)
    slope *= match_intensity

    over = max(0.0, minutes_played - onset)
    decay = 1.0 - min(0.42, slope * over * (1.0 + over / 220.0))

    residual = 1.0 - 0.0022 * baseline_fatigue
    multiplier = float(max(0.55, decay * residual))

    fatigue_index = float(min(100.0, baseline_fatigue + (1.0 - decay) * 190.0))

    # Hazard rises sharply once accumulated load is high and the player is deep
    # into a match — the acute:chronic workload story, simplified.
    acute_ratio = minutes_last_7d / 270.0
    hazard = 0.004 + 0.020 * max(0.0, over / 45.0) ** 1.6 + 0.028 * max(0.0, acute_ratio - 0.85)
    hazard += 0.0009 * max(0.0, age - 31.0) * 10
    if high_intensity_km is not None and high_intensity_km > 2.6:
        hazard += 0.012 * (high_intensity_km - 2.6)
    hazard = float(min(0.55, max(0.002, hazard)))

    return FatigueState(
        performance_multiplier=round(multiplier, 4),
        fatigue_index=round(fatigue_index, 2),
        injury_hazard=round(hazard, 4),
        drivers={
            "decline_onset_minute": round(onset, 1),
            "minutes_played": minutes_played,
            "minutes_last_7d": minutes_last_7d,
            "stamina": stamina,
            "age": round(age, 1),
        },
    )


#: (peak age, post-peak decay rate) per position group. Ageing is not uniform:
#: goalkeepers hold their level into their mid-thirties, centre-backs and holding
#: midfielders decline slowly because their game is positional, and pace-reliant
#: roles fall off fastest.
_AGE_PROFILE: dict[str, tuple[float, float]] = {
    "GK": (30.5, 0.014),
    "CB": (28.5, 0.022),
    "DM": (28.5, 0.024),
    "CM": (27.5, 0.028),
    "AM": (27.0, 0.030),
    "ST": (27.5, 0.030),
    "FB": (26.5, 0.036),
    "W":  (26.5, 0.038),
}


def age_curve(age: float | None, position: Position | None = None) -> float:
    """Relative level at ``age`` compared with the position's peak age.

    Rises from 0.80 at 17 to 1.0 at the peak, then decays.

    **This is a projection tool, not a discount on current ability.**
    ``overall_rating`` already states what a player is *now*; multiplying it by
    this curve would double-count age and systematically undervalue everyone who
    is not exactly at their peak. Use it to ask "what will this player be worth
    in two years" — ``age_curve(age + 2) / age_curve(age)`` — and to flag squad
    areas about to age out. Do not use it to rank players today.
    """
    if age is None:
        return 1.0
    bucket = POSITION_BUCKET.get(position, "CM") if position is not None else "CM"
    peak, rate = _AGE_PROFILE[bucket]
    if age < peak:
        return float(min(1.0, 0.80 + 0.20 * (age - 17.0) / max(peak - 17.0, 1.0)))
    return float(max(0.60, 1.0 - rate * (age - peak) ** 1.25))


def player_feature_vector(player, *, minute: int = 0, target: Position | None = None) -> dict:
    """Flat feature dict used by the models and echoed in report evidence."""
    target = target or getattr(player, "primary_position", Position.CM)
    fs = fatigue_state(
        minutes_played=minute,
        age=getattr(player, "age", None),
        stamina=attribute(player, "stamina", getattr(player, "overall_rating", 70.0)),
        minutes_last_7d=getattr(player, "minutes_last_7d", 0) or 0,
        baseline_fatigue=getattr(player, "fatigue", 0.0) or 0.0,
    )
    overall = float(getattr(player, "overall_rating", 70.0))
    fit = position_fit(player, target)
    return {
        "overall": overall,
        "potential": float(getattr(player, "potential_rating", overall)),
        "age": getattr(player, "age", None),
        "age_multiplier": age_curve(getattr(player, "age", None), getattr(player, "primary_position", None)),
        "fitness": float(getattr(player, "fitness", 100.0)),
        "position_fit": round(fit, 4),
        "performance_multiplier": fs.performance_multiplier,
        "fatigue_index": fs.fatigue_index,
        "injury_hazard": fs.injury_hazard,
        "effective_level": round(effective_level(player, target, fs.performance_multiplier), 2),
        "fatigue_drivers": fs.drivers,
    }
