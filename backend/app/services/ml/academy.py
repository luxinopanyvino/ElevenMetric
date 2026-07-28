"""Academy engine: development tracking and time-to-first-team projection.

The question the section answers is "**when** will this player be ready, and
what should we do with them in the meantime". Two things make that harder than
fitting a line through past ratings:

* **Relative age effect** — players born early in the selection year are bigger
  and dominate age-group football for reasons that vanish in adulthood.
* **Biological vs chronological age** — a late developer's current output
  understates their level. `biological_age_offset` corrects for this, which is
  the whole point of bio-banding.

Both are applied before the growth model sees the numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from app.models.academy import Pathway
from app.models.catalog import POSITION_LINE, Position
from app.services.ml.features import age_curve, position_fit
from app.services.ml.registry import get_model

#: Composite ability considered first-team standard by default. Overridden per
#: club from the actual senior squad (see :func:`first_team_bar`).
DEFAULT_FIRST_TEAM_BAR = 68.0
HORIZON_MONTHS = 96.0


@dataclass
class Projection:
    academy_player_id: str
    name: str
    position: Position
    age: float | None
    current_ability: float
    #: Ability corrected for biological age and level of competition.
    adjusted_ability: float
    potential_ability: float
    growth_rate_per_year: float
    months_to_first_team: float | None
    projected_ready_on: date | None
    readiness_score: float           # 0-100 right now
    #: The bar this player is measured against — position-specific, so a
    #: goalkeeper is not judged against the squad's forwards.
    first_team_bar: float
    pathway: Pathway
    ceiling_reached_pct: float
    confidence: float
    #: Ability trajectory for the chart: [{"months_ahead", "ability"}].
    trajectory: list[dict] = field(default_factory=list)
    drivers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "academy_player_id": self.academy_player_id,
            "name": self.name,
            "position": self.position.value,
            "line": POSITION_LINE.get(self.position, "MID"),
            "age": round(self.age, 1) if self.age is not None else None,
            "current_ability": round(self.current_ability, 1),
            "adjusted_ability": round(self.adjusted_ability, 1),
            "potential_ability": round(self.potential_ability, 1),
            "growth_rate_per_year": round(self.growth_rate_per_year, 2),
            "months_to_first_team": (
                round(self.months_to_first_team, 1) if self.months_to_first_team is not None else None
            ),
            "projected_ready_on": self.projected_ready_on.isoformat() if self.projected_ready_on else None,
            "readiness_score": round(self.readiness_score, 1),
            "first_team_bar": round(self.first_team_bar, 1),
            "pathway": self.pathway.value,
            "ceiling_reached_pct": round(self.ceiling_reached_pct, 1),
            "confidence": round(self.confidence, 3),
            "trajectory": self.trajectory,
            "drivers": self.drivers,
            "warnings": self.warnings,
        }


def first_team_bar(senior_players: list, position: Position | None = None) -> float:
    """The level a youth player must reach to compete for senior minutes.

    Defined as the 25th percentile of the senior squad (i.e. better than the
    weakest quarter), because a prospect displaces the bottom of the roster
    before they displace a starter.
    """
    if not senior_players:
        return DEFAULT_FIRST_TEAM_BAR
    pool = senior_players
    if position is not None:
        same = [p for p in senior_players if position_fit(p, position) >= 0.7]
        if len(same) >= 2:
            pool = same
    ratings = [p.overall_rating for p in pool]
    return float(np.percentile(ratings, 25))


def relative_age_quartile(birth: date | None, selection_year_start_month: int = 1) -> int | None:
    """Birth quartile within the selection year. Q1 players are over-selected."""
    if birth is None:
        return None
    month = (birth.month - selection_year_start_month) % 12
    return month // 3 + 1


def _growth_rate(assessments: list) -> tuple[float, float]:
    """Least-squares ability growth per year, plus a 0-1 confidence.

    Needs three assessments spanning at least six months to be trusted; with
    less, it falls back to an age-typical prior.
    """
    points = [(a.assessed_on, a.ability) for a in assessments if a.assessed_on is not None]
    points.sort()
    if len(points) < 2:
        return 3.0, 0.15

    t0 = points[0][0]
    xs = np.array([(d - t0).days / 365.25 for d, _ in points])
    ys = np.array([v for _, v in points])
    span = xs.max() - xs.min()
    if span < 0.08:
        return 3.0, 0.15

    slope, intercept = np.polyfit(xs, ys, 1)
    noise = float(np.std(ys - (slope * xs + intercept)))

    confidence = min(0.92, 0.25 + 0.22 * min(len(points), 6) + 0.30 * min(span / 1.5, 1.0))
    confidence *= max(0.5, 1.0 - noise / 12.0)
    return float(np.clip(slope, -6.0, 14.0)), float(confidence)


def _level_multiplier(level: str) -> float:
    """A rating earned against senior opposition is worth more than the same
    rating earned in an age group."""
    return {"academy": 1.0, "reserves": 1.035, "senior": 1.08}.get(level, 1.0)


def project(
    player,
    *,
    senior_players: list | None = None,
    today: date | None = None,
) -> Projection:
    """Project one academy player."""
    today = today or date.today()
    senior_players = senior_players or []
    assessments = sorted(
        [a for a in (player.assessments or []) if a.assessed_on is not None],
        key=lambda a: a.assessed_on,
    )

    age = player.age
    growth, growth_conf = _growth_rate(assessments)

    latest = assessments[-1] if assessments else None
    current = float(latest.ability if latest else player.current_ability)

    drivers: list[str] = []
    warnings: list[str] = []

    # --- Corrections -------------------------------------------------------
    bio = float(player.biological_age_offset or 0.0)
    adjusted = current
    if bio < -0.4:
        # Late developer: current output understates the player.
        adjusted = current + min(6.0, -bio * 3.2)
        drivers.append(
            f"Late developer ({bio:+.1f} yr skeletal offset): ability adjusted "
            f"{current:.1f} → {adjusted:.1f} under bio-banding"
        )
    elif bio > 0.6:
        adjusted = current - min(5.0, bio * 2.6)
        warnings.append(
            f"Early maturer ({bio:+.1f} yr): current dominance is partly physical "
            "and will normalise as peers catch up"
        )

    if latest is not None:
        mult = _level_multiplier(latest.level)
        if mult > 1.0:
            adjusted *= mult
            drivers.append(
                f"Latest assessment earned at '{latest.level}' level (×{mult:.3f} weighting)"
            )

    quartile = relative_age_quartile(player.birth_date)
    if quartile == 1 and (age or 20) < 18:
        warnings.append(
            "Q1 birth date — relative age effect inflates age-group performance; "
            "weight the physical pillar down"
        )
    elif quartile == 4 and (age or 20) < 18:
        drivers.append("Q4 birth date — competing against peers up to 11 months older")

    bar = first_team_bar(senior_players, player.primary_position)

    # --- Model prediction --------------------------------------------------
    model = get_model("academy")
    minutes_ratio = 0.0
    if player.minutes_this_season:
        minutes_ratio = min(1.0, (player.senior_minutes or 0) / max(player.minutes_this_season, 1))

    row = {
        "current_ability": adjusted,
        "potential_ability": float(player.potential_ability),
        "age": float(age) if age is not None else 18.0,
        "growth_rate_per_year": growth,
        "biological_age_offset": bio,
        "minutes_ratio": minutes_ratio,
        "technical": float(latest.technical if latest else adjusted),
        "tactical": float(latest.tactical if latest else adjusted),
        "physical": float(latest.physical if latest else adjusted),
        "mental": float(latest.mental if latest else adjusted),
    }
    months = float(model.predict([row])[0])

    # The model is trained against a fixed 68 bar; shift for this club's actual
    # bar so a Championship academy and a Champions-League academy differ.
    bar_shift = (bar - DEFAULT_FIRST_TEAM_BAR) / max(growth, 0.5) * 12.0
    months = float(np.clip(months + bar_shift, 0.0, HORIZON_MONTHS))

    if adjusted >= bar:
        months = 0.0
        drivers.append(
            f"Already at or above the club's first-team bar at "
            f"{player.primary_position.value} of {bar:.1f}"
        )

    ceiling_pct = 100.0 * adjusted / max(player.potential_ability, 1.0)

    if player.potential_ability < bar - 1.0:
        months = None
        warnings.append(
            f"Projected ceiling ({player.potential_ability:.0f}) sits below the "
            f"first-team bar ({bar:.0f}) — this is a loan or sale profile, not a promotion"
        )

    ready_on = today + timedelta(days=int(months * 30.44)) if months is not None else None

    # --- Readiness right now ----------------------------------------------
    gap = bar - adjusted
    readiness = float(np.clip(100 - gap * 7.5, 0, 100))
    if player.senior_minutes:
        readiness = min(100.0, readiness + min(12.0, player.senior_minutes / 90.0 * 3.0))
        drivers.append(f"{player.senior_minutes}' of senior football already banked")

    pathway = _pathway(
        readiness=readiness, months=months, age=age, ceiling_pct=ceiling_pct,
        potential=player.potential_ability, bar=bar,
    )

    trajectory = _trajectory(adjusted, player.potential_ability, growth)

    if growth_conf < 0.4:
        warnings.append(
            f"Only {len(assessments)} assessment(s) on file — the growth rate is a "
            "prior, not a measurement. Three over six months makes this reliable."
        )
    if growth < 0.5 and (age or 20) < 20:
        warnings.append(
            f"Development has stalled ({growth:+.1f} pts/yr) despite the player's age"
        )

    drivers.append(f"Measured growth {growth:+.1f} ability points per year")
    if age is not None:
        drivers.append(f"Age curve multiplier {age_curve(age, player.primary_position):.2f}")

    confidence = float(np.clip(0.35 * growth_conf + 0.35 + 0.30 * min(len(assessments) / 5, 1.0), 0, 0.95))

    return Projection(
        academy_player_id=player.id,
        name=player.name,
        position=player.primary_position,
        age=age,
        current_ability=current,
        adjusted_ability=adjusted,
        potential_ability=float(player.potential_ability),
        growth_rate_per_year=growth,
        months_to_first_team=months,
        projected_ready_on=ready_on,
        readiness_score=readiness,
        first_team_bar=bar,
        pathway=pathway,
        ceiling_reached_pct=ceiling_pct,
        confidence=confidence,
        trajectory=trajectory,
        drivers=drivers,
        warnings=warnings,
    )


def _pathway(*, readiness: float, months: float | None, age: float | None,
             ceiling_pct: float, potential: float, bar: float) -> Pathway:
    age = age if age is not None else 18.0
    if potential < bar - 1.0:
        return Pathway.release if age >= 20 else Pathway.review
    if months is None:
        return Pathway.review
    if readiness >= 88 or months <= 2:
        return Pathway.promote_now
    if months <= 9:
        return Pathway.train_with_first_team
    if age >= 18.5 and ceiling_pct < 88 and months <= 30:
        return Pathway.loan_out
    return Pathway.continue_academy


def _trajectory(current: float, potential: float, growth: float,
                horizon_months: int = 48) -> list[dict]:
    """Bounded-growth approach to the ceiling, sampled every 6 months.

    Starts at ``current``, asymptotes at ``potential``; the initial slope is the
    measured growth rate, and it decays as the gap to the ceiling closes.
    """
    headroom = max(0.0, potential - current)
    out = []
    for m in range(0, horizon_months + 1, 6):
        years = m / 12.0
        value = potential - headroom * float(np.exp(-growth * years / max(headroom, 1.0) * 1.6))
        out.append({"months_ahead": m, "ability": round(min(value, potential), 2)})
    return out


def review_squad(
    academy_players: list, senior_players: list | None = None, *, today: date | None = None
) -> dict:
    """Project the whole academy and summarise the pipeline."""
    projections = [project(p, senior_players=senior_players, today=today) for p in academy_players]
    # `or 1e9` would push players ready *now* (months == 0.0) to the bottom.
    projections.sort(
        key=lambda p: (
            p.months_to_first_team is None,
            p.months_to_first_team if p.months_to_first_team is not None else 1e9,
        )
    )

    by_pathway: dict[str, int] = {}
    for p in projections:
        by_pathway[p.pathway.value] = by_pathway.get(p.pathway.value, 0) + 1

    ready_windows = {"0-6m": 0, "6-12m": 0, "12-24m": 0, "24m+": 0, "not projected": 0}
    for p in projections:
        m = p.months_to_first_team
        if m is None:
            ready_windows["not projected"] += 1
        elif m <= 6:
            ready_windows["0-6m"] += 1
        elif m <= 12:
            ready_windows["6-12m"] += 1
        elif m <= 24:
            ready_windows["12-24m"] += 1
        else:
            ready_windows["24m+"] += 1

    # Positions the pipeline does *not* cover — the link to the transfer engine.
    covered = {
        p.position for p in projections
        if p.months_to_first_team is not None and p.months_to_first_team <= 24
    }
    gaps = [pos.value for pos in Position if pos not in covered]

    return {
        "projections": [p.to_dict() for p in projections],
        "summary": {
            "count": len(projections),
            "by_pathway": by_pathway,
            "ready_windows": ready_windows,
            "first_team_bar": round(first_team_bar(senior_players or []), 1),
            "uncovered_positions": gaps,
            "model_version": get_model("academy").version,
        },
    }
