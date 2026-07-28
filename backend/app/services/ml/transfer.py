"""Transfer market recommender.

Three stages:

1. **Need detection** — where the squad is thin or weak, from the current roster
   plus (when available) the tactical vulnerabilities the match analysis found.
2. **Scoring** — every market player is scored against every need on quality,
   fit, value and risk.
3. **Bundle selection** — a 0/1 knapsack under two simultaneous constraints
   (transfer fee and annual wage bill), so the output is a signing *plan* that
   the club can actually afford, not a wishlist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app.models.catalog import POSITION_LINE, Position
from app.services.ml.features import age_curve, effective_level, position_fit

#: Minimum bodies per position group for a season. Below this is a depth need.
DEPTH_TARGETS: dict[str, int] = {"GK": 3, "DEF": 8, "MID": 6, "ATT": 5}

#: League strength multiplier applied when projecting production upward.
LEAGUE_TIER_FACTOR = {1: 1.00, 2: 0.93, 3: 0.86, 4: 0.78, 5: 0.70}


@dataclass
class SquadNeed:
    position: Position
    #: 0-1. 1 = the squad has nothing here.
    severity: float
    reason: str
    current_best: float
    depth: int
    drivers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "position": self.position.value,
            "line": POSITION_LINE.get(self.position, "MID"),
            "severity": round(self.severity, 3),
            "reason": self.reason,
            "current_best_rating": round(self.current_best, 1),
            "depth": self.depth,
            "drivers": self.drivers,
        }


@dataclass
class ScoredTarget:
    market_player: object
    position: Position
    quality: float          # 0-100 projected level in our shirt
    fit: float              # 0-100
    value: float            # 0-100 quality per euro
    risk: float             # 0-100, higher = riskier
    composite: float        # 0-100
    projected_upgrade: float
    effective_cost: int
    wage: int
    rationale: list[str] = field(default_factory=list)
    selected: bool = False

    def to_dict(self) -> dict:
        mp = self.market_player
        return {
            "market_player_id": getattr(mp, "id", None),
            "name": mp.name,
            "current_club": getattr(mp, "current_club", ""),
            "league": getattr(mp, "league", ""),
            "age": round(mp.age, 1) if getattr(mp, "age", None) else None,
            "primary_position": mp.primary_position.value,
            "target_position": self.position.value,
            "overall_rating": mp.overall_rating,
            "potential_rating": mp.potential_rating,
            "quality_score": round(self.quality, 1),
            "fit_score": round(self.fit, 1),
            "value_score": round(self.value, 1),
            "risk_score": round(self.risk, 1),
            "composite_score": round(self.composite, 1),
            "projected_upgrade": round(self.projected_upgrade, 2),
            "effective_cost_eur": self.effective_cost,
            "wage_eur_per_year": self.wage,
            "deal_type": getattr(mp, "deal_type", None).value if getattr(mp, "deal_type", None) else None,
            "availability": getattr(mp, "availability", None),
            "selected": self.selected,
            "rationale": self.rationale,
        }


# --- Stage 1: needs --------------------------------------------------------

def positions_for_formations(formations: list[str]) -> set[Position]:
    """Positions a club actually fields, given the shapes it plays.

    Without this the engine reports a screaming need for wing-backs at a club
    that has never played with a back three — technically true, tactically
    irrelevant.
    """
    from app.services.ml.lineup_optimizer import FORMATION_SLOTS

    out: set[Position] = set()
    for f in formations:
        out.update(FORMATION_SLOTS.get(f, []))
    return out or set(Position)


def detect_needs(
    players: list,
    *,
    vulnerabilities: list[dict] | None = None,
    horizon_years: float = 2.0,
    relevant_positions: set[Position] | None = None,
    min_severity: float = 0.18,
) -> list[SquadNeed]:
    """Find where the squad is weak, thin, or about to age out.

    ``relevant_positions`` restricts the scan to the positions the club's
    formations actually use.
    """
    vulnerabilities = vulnerabilities or []
    by_position: dict[Position, list] = {}
    by_line: dict[str, list] = {"GK": [], "DEF": [], "MID": [], "ATT": []}

    # A player who cannot play is not depth. Long-term absentees are the single
    # most common reason a squad that looks fine on paper needs a signing.
    unavailable = [p for p in players if not getattr(p, "is_available", True)]
    unavailable_by_position: dict[Position, int] = {}
    for p in unavailable:
        unavailable_by_position[p.primary_position] = (
            unavailable_by_position.get(p.primary_position, 0) + 1
        )
    players = [p for p in players if getattr(p, "is_available", True)]
    if not players:
        return []

    for p in players:
        by_position.setdefault(p.primary_position, []).append(p)
        line = POSITION_LINE.get(p.primary_position)
        if line in by_line:
            by_line[line].append(p)
        for sec in (getattr(p, "secondary_positions", None) or []):
            try:
                by_position.setdefault(Position(sec), []).append(p)
            except ValueError:
                continue

    # Benchmark: the level a starter should reach at this club. Raw ratings on
    # both sides — `overall_rating` is current level, and the ageing concern is
    # handled separately by the ageing term below.
    starter_bar = (
        float(np.percentile([p.overall_rating for p in players], 70)) if players else 72.0
    )

    needs: list[SquadNeed] = []
    scan_positions = relevant_positions if relevant_positions else set(Position)

    for position in Position:
        if position not in scan_positions:
            continue
        pool = by_position.get(position, [])
        line = POSITION_LINE.get(position, "MID")
        drivers: list[str] = []

        ratings = sorted((p.overall_rating for p in pool), reverse=True)
        best = ratings[0] if ratings else 0.0
        depth = len(pool)

        quality_gap = max(0.0, (starter_bar - best) / max(starter_bar, 1.0))
        if quality_gap > 0:
            drivers.append(
                f"Best option rates {best:.1f} against a starter bar of {starter_bar:.1f}"
            )

        line_pool = by_line.get(line, [])
        line_target = DEPTH_TARGETS.get(line, 5)
        depth_gap = max(0.0, (line_target - len(line_pool)) / line_target)
        if depth_gap > 0:
            drivers.append(
                f"{len(line_pool)} available players cover {line} against a target of {line_target}"
            )

        # Two capable bodies per starting slot is the working minimum; one is a
        # single injury away from an emergency.
        capable = sum(1 for p in players if position_fit(p, position) >= 0.78)
        slot_gap = max(0.0, (2 - capable) / 2)
        if slot_gap > 0:
            drivers.append(
                f"Only {capable} player(s) in the squad rate above a 78% fit at {position.value}"
            )

        out_count = unavailable_by_position.get(position, 0)
        if out_count:
            drivers.append(f"{out_count} natural option(s) currently unavailable")

        # Ageing: everyone in this slot past peak within the horizon.
        ages = [p.age for p in pool if p.age is not None]
        aging_gap = 0.0
        if ages and min(ages) + horizon_years > 31:
            aging_gap = 0.35
            drivers.append(
                f"Youngest option is {min(ages):.0f}; the position ages out inside {horizon_years:.0f} years"
            )

        # Contract risk.
        expiring = [p for p in pool if getattr(p, "contract_until", None) is not None]
        contract_gap = 0.0
        if pool and expiring:
            from datetime import date
            soon = [p for p in expiring if (p.contract_until - date.today()).days < 400]
            if soon and len(soon) >= max(1, len(pool) // 2):
                contract_gap = 0.25
                drivers.append(f"{len(soon)} of {len(pool)} contracts expire within 13 months")

        # Tactical: a diagnosed leak on this flank raises the need.
        tactical_gap = 0.0
        for v in vulnerabilities:
            vid = v.get("id", "")
            sev = float(v.get("severity", 50)) / 100.0
            if vid.startswith("lane_leak"):
                lane = vid.replace("lane_leak_", "").replace("_", " ")
                if ("left" in lane and position.value.startswith("L")) or (
                    "right" in lane and position.value.startswith("R")
                ):
                    tactical_gap = max(tactical_gap, 0.4 * sev)
                    drivers.append(f"Match analysis: {v.get('title')}")
            elif vid == "final_third_stall" and line == "ATT":
                tactical_gap = max(tactical_gap, 0.35 * sev)
                drivers.append(f"Match analysis: {v.get('title')}")

        severity = min(
            1.0,
            0.99 * quality_gap + 0.20 * depth_gap + 0.30 * slot_gap
            + 0.14 * aging_gap + 0.13 * contract_gap + tactical_gap,
        )
        if depth == 0:
            severity = max(severity, 0.75)
            drivers.insert(0, "No natural option available in the squad")

        if severity >= min_severity:
            reason = (
                "no cover" if depth == 0
                else "single-body risk" if slot_gap > 0
                else "quality gap" if quality_gap > 0.06
                else "thin depth" if depth_gap > 0
                else "ageing profile" if aging_gap > 0
                else "tactical requirement"
            )
            needs.append(SquadNeed(
                position=position, severity=severity, reason=reason,
                current_best=best, depth=depth, drivers=drivers,
            ))

    return sorted(needs, key=lambda n: -n.severity)


# --- Stage 2: scoring ------------------------------------------------------

def _projected_level(mp, target: Position) -> float:
    """Level the player would perform at in our shirt.

    Adjusts the raw rating for league strength, positional fit and the age
    curve, and blends in potential for young players. Fit is applied through
    :func:`effective_level` so the penalty is damped the same way it is when
    ranking our own squad — otherwise market players are systematically
    undervalued against incumbents and the engine never recommends anyone.
    """
    tier = LEAGUE_TIER_FACTOR.get(getattr(mp, "league_tier", 3), 0.86)
    age = getattr(mp, "age", None)

    rating = mp.overall_rating
    # Under 23, part of what you buy is what they become over the contract.
    # Weighted modestly — the level you get in year one is the current rating,
    # not the ceiling — and never applied on top of an age discount, which would
    # give and take away the same thing twice.
    if age is not None and age < 23:
        w = 0.35 * (23 - age) / 6.0
        rating = rating * (1 - w) + mp.potential_rating * w
    elif age is not None and age > 32:
        # Past 32 the decline inside a typical contract is real and near-certain.
        decline = age_curve(age + 1.5, mp.primary_position) / max(
            age_curve(age, mp.primary_position), 1e-6
        )
        rating *= min(1.0, decline)

    league_adjusted = rating * (0.62 + 0.38 * tier)

    # Reuse the squad-side fit damping by proxying the adjusted rating.
    class _Proxy:
        overall_rating = league_adjusted
        primary_position = mp.primary_position
        secondary_positions = getattr(mp, "secondary_positions", []) or []
        attributes = getattr(mp, "attributes", {}) or {}

    return float(effective_level(_Proxy(), target))


def _risk(mp) -> tuple[float, list[str]]:
    notes: list[str] = []
    risk = 0.0
    inj = getattr(mp, "injury_history_days_2y", 0) or 0
    if inj > 60:
        risk += min(35.0, inj / 6.0)
        notes.append(f"{inj} days lost to injury in two seasons")
    minutes = getattr(mp, "minutes_last_season", 0) or 0
    if minutes < 900:
        risk += (900 - minutes) / 60.0
        notes.append(f"Only {minutes} minutes last season — a small sample to judge")
    avail = getattr(mp, "availability", 0.6) or 0.6
    risk += (1 - avail) * 30
    if avail < 0.4:
        notes.append(f"Deal likelihood assessed at {avail:.0%}")
    age = getattr(mp, "age", None)
    if age is not None and age >= 31:
        risk += (age - 30) * 5
        notes.append(f"Aged {age:.0f} — resale value close to zero")
    tier = getattr(mp, "league_tier", 3)
    if tier >= 4:
        risk += 10
        notes.append("Step-up risk from a lower-tier league")
    return float(min(100.0, risk)), notes


def score_targets(
    market: list,
    needs: list[SquadNeed],
    *,
    squad_players: list | None = None,
    max_per_need: int = 12,
    style_weights: dict[str, float] | None = None,
) -> list[ScoredTarget]:
    """Score every market player against every open need."""
    squad_players = squad_players or []
    out: list[ScoredTarget] = []

    def _incumbent_level(position: Position) -> float:
        """The best level the squad can currently field at ``position``.

        Evaluated across *every available* player at that position rather than
        only the natural one, because that is what a signing actually competes
        with. With the first-choice centre-back injured, the incumbent is
        whoever shuffles across — not the absent starter, and not zero.
        """
        levels = [
            effective_level(p, position)
            for p in squad_players
            if getattr(p, "is_available", True) and position_fit(p, position) >= 0.5
        ]
        return max(levels) if levels else 0.0

    incumbent: dict[Position, float] = {}

    costs = [max(1, getattr(m, "total_cost_eur", 0) or 1) for m in market] or [1]
    cost_ref = float(np.percentile(costs, 75))

    for need in needs:
        if need.position not in incumbent:
            incumbent[need.position] = _incumbent_level(need.position)
        scored: list[ScoredTarget] = []
        for mp in market:
            fit = position_fit(mp, need.position)
            if fit < 0.55:
                continue

            projected = _projected_level(mp, need.position)
            current_best = incumbent.get(need.position, need.current_best)
            upgrade = projected - current_best
            if upgrade <= 0.4:
                continue

            risk, risk_notes = _risk(mp)

            cost = getattr(mp, "total_cost_eur", 0) or 0
            clause = getattr(mp, "release_clause_eur", None)
            if clause and clause < cost:
                cost = int(clause * (1 + (getattr(mp, "agent_fee_pct", 0.05) or 0.05)))

            # Quality: projected level mapped onto 0-100 around a 60-90 band.
            quality = float(np.clip((projected - 58) / 32 * 100, 0, 100))
            # Value: upgrade per unit cost, log-scaled so free transfers do not
            # dominate purely by having a tiny denominator.
            value = float(np.clip(
                100 * upgrade / (2.0 + 8.0 * math.log10(1 + cost / max(cost_ref, 1))) / 6.0,
                0, 100,
            ))
            fit_score = fit * 100

            weights = style_weights or {"quality": 0.36, "fit": 0.22, "value": 0.27, "risk": 0.15}
            composite = (
                weights["quality"] * quality
                + weights["fit"] * fit_score
                + weights["value"] * value
                - weights["risk"] * risk
            )
            # Scale by how badly the squad needs this position.
            composite = float(np.clip(composite * (0.6 + 0.4 * need.severity), 0, 100))

            rationale = [
                f"Projects at {projected:.1f} in our shirt, "
                f"{upgrade:+.1f} on the current best option at {need.position.value}",
                f"Positional fit {fit:.0%} ({mp.primary_position.value} → {need.position.value})",
                f"Fee {cost / 1e6:.1f}M€ + {getattr(mp, 'wage_demand_eur_per_year', 0) / 1e6:.1f}M€/yr wages",
            ]
            if getattr(mp, "league_tier", 3) > 1:
                rationale.append(
                    f"League-strength adjustment applied "
                    f"(tier {getattr(mp, 'league_tier', 3)}, factor "
                    f"{LEAGUE_TIER_FACTOR.get(getattr(mp, 'league_tier', 3), 0.86):.2f})"
                )
            rationale.extend(risk_notes)

            scored.append(ScoredTarget(
                market_player=mp, position=need.position,
                quality=quality, fit=fit_score, value=value, risk=risk,
                composite=composite, projected_upgrade=upgrade,
                effective_cost=int(cost),
                wage=int(getattr(mp, "wage_demand_eur_per_year", 0) or 0),
                rationale=rationale,
            ))

        scored.sort(key=lambda t: -t.composite)
        out.extend(scored[:max_per_need])

    return sorted(out, key=lambda t: -t.composite)


# --- Stage 3: bundle selection --------------------------------------------

def select_bundle(
    targets: list[ScoredTarget],
    *,
    budget_eur: int,
    wage_budget_eur: int,
    max_signings: int = 4,
    one_per_position: bool = True,
) -> tuple[list[ScoredTarget], dict]:
    """Pick the affordable set that maximises total composite score.

    Two budget dimensions make this a multi-dimensional knapsack, which is
    NP-hard; with a handful of signings and a discretised fee axis, exact DP is
    both feasible and fast. Fees are bucketed to 250k€ to keep the table small.
    """
    if not targets or budget_eur <= 0:
        return [], {"reason": "no budget or no candidates"}

    bucket = 250_000
    cap = max(1, budget_eur // bucket)

    pool = [t for t in targets if t.effective_cost <= budget_eur and t.wage <= wage_budget_eur]

    # A player scored against several needs appears several times. Keep only
    # their best slot — you cannot sign the same footballer twice.
    best_by_player: dict[str, ScoredTarget] = {}
    for t in sorted(pool, key=lambda x: -x.composite):
        best_by_player.setdefault(getattr(t.market_player, "id", t.market_player.name), t)
    pool = list(best_by_player.values())

    if one_per_position:
        best_by_pos: dict[Position, ScoredTarget] = {}
        for t in sorted(pool, key=lambda x: -x.composite):
            best_by_pos.setdefault(t.position, t)
        pool = list(best_by_pos.values())

    pool = sorted(pool, key=lambda t: -t.composite)[:40]
    if not pool:
        return [], {"reason": "nothing within the fee and wage limits"}

    # dp[k][c] = (score, wage, chosen indices) for k signings and c fee buckets.
    NEG = -1e18
    dp: list[list[tuple[float, int, tuple[int, ...]]]] = [
        [(NEG, 0, ()) for _ in range(cap + 1)] for _ in range(max_signings + 1)
    ]
    dp[0][0] = (0.0, 0, ())

    for idx, t in enumerate(pool):
        cost_b = max(1, math.ceil(t.effective_cost / bucket))
        if cost_b > cap:
            continue
        for k in range(max_signings - 1, -1, -1):
            for c in range(cap - cost_b, -1, -1):
                score, wage, chosen = dp[k][c]
                if score == NEG:
                    continue
                new_wage = wage + t.wage
                if new_wage > wage_budget_eur:
                    continue
                new_score = score + t.composite
                cur = dp[k + 1][c + cost_b]
                if new_score > cur[0]:
                    dp[k + 1][c + cost_b] = (new_score, new_wage, chosen + (idx,))

    best = (NEG, 0, ())
    best_k = 0
    for k in range(1, max_signings + 1):
        for c in range(cap + 1):
            if dp[k][c][0] > best[0]:
                best, best_k = dp[k][c], k

    if best[0] == NEG:
        return [], {"reason": "no combination fits both budgets"}

    selected = [pool[i] for i in best[2]]
    for t in selected:
        t.selected = True

    total_fee = sum(t.effective_cost for t in selected)
    total_wage = sum(t.wage for t in selected)
    return selected, {
        "signings": best_k,
        "total_fee_eur": total_fee,
        "total_wage_eur_per_year": total_wage,
        "budget_eur": budget_eur,
        "wage_budget_eur_per_year": wage_budget_eur,
        "budget_remaining_eur": budget_eur - total_fee,
        "wage_remaining_eur_per_year": wage_budget_eur - total_wage,
        "total_composite": round(best[0], 1),
        "fee_bucket_eur": bucket,
        "method": "2-D knapsack, exact DP over bucketed fees",
    }
