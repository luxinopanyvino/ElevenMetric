"""Best-XI selection and formation comparison.

Assigning players to positions is a linear assignment problem: maximise total
effective level subject to one player per slot. Solved exactly with the
Hungarian algorithm — with eleven slots the exact answer is instant, and greedy
assignment is genuinely worse (it strands a versatile player in the wrong slot).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.models.catalog import POSITION_ANCHOR, Position
from app.services.ml.features import (
    UNRANKED_FALLBACK as UNRANKED,
    attribute,
    effective_level,
    fatigue_state,
    position_fit,
    rating_or,
    split_rankable,
)

#: Slot templates per formation, ordered GK → back → middle → front.
FORMATION_SLOTS: dict[str, list[Position]] = {
    "4-3-3": [Position.GK, Position.LB, Position.LCB, Position.RCB, Position.RB,
              Position.CM, Position.DM, Position.CM, Position.LW, Position.ST, Position.RW],
    "4-2-3-1": [Position.GK, Position.LB, Position.LCB, Position.RCB, Position.RB,
                Position.DM, Position.DM, Position.LW, Position.AM, Position.RW, Position.ST],
    "4-4-2": [Position.GK, Position.LB, Position.LCB, Position.RCB, Position.RB,
              Position.LM, Position.CM, Position.CM, Position.RM, Position.ST, Position.ST],
    "4-1-4-1": [Position.GK, Position.LB, Position.LCB, Position.RCB, Position.RB,
                Position.DM, Position.LM, Position.CM, Position.CM, Position.RM, Position.ST],
    "3-5-2": [Position.GK, Position.LCB, Position.CB, Position.RCB,
              Position.LWB, Position.CM, Position.DM, Position.CM, Position.RWB,
              Position.ST, Position.ST],
    "3-4-3": [Position.GK, Position.LCB, Position.CB, Position.RCB,
              Position.LWB, Position.CM, Position.CM, Position.RWB,
              Position.LW, Position.ST, Position.RW],
    "5-3-2": [Position.GK, Position.LWB, Position.LCB, Position.CB, Position.RCB, Position.RWB,
              Position.CM, Position.DM, Position.CM, Position.ST, Position.ST],
    "3-4-2-1": [Position.GK, Position.LCB, Position.CB, Position.RCB,
                Position.LWB, Position.CM, Position.CM, Position.RWB,
                Position.AM, Position.AM, Position.ST],
    "4-3-1-2": [Position.GK, Position.LB, Position.LCB, Position.RCB, Position.RB,
                Position.CM, Position.DM, Position.CM, Position.AM, Position.ST, Position.ST],
}


#: Lateral separation, in metres, between players sharing a position slot.
DUPLICATE_SPREAD_M = 18.0


def formation_anchors(formation: str, pitch_length: float = 105.0,
                      pitch_width: float = 68.0) -> list[tuple[float, float]]:
    """Pitch anchors for a formation's slots, in metres.

    Positions that appear more than once in a shape — the two central
    midfielders in a 4-3-3, the two strikers in a 4-4-2 — share a single
    canonical anchor, so they are spread laterally around it. Without this they
    stack on the same point: the UI draws two names on top of each other, and
    the simulator gets two strikers standing in the same high-value spot, which
    roughly doubles that team's shot volume.
    """
    slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-3-3"])
    counts: dict[Position, int] = {}
    for pos in slots:
        counts[pos] = counts.get(pos, 0) + 1

    seen: dict[Position, int] = {}
    out: list[tuple[float, float]] = []
    for pos in slots:
        ax, ay = POSITION_ANCHOR[pos]
        n = counts[pos]
        if n > 1:
            k = seen.get(pos, 0)
            seen[pos] = k + 1
            offset = (k - (n - 1) / 2) * DUPLICATE_SPREAD_M
            ay = min(max(ay + offset, 6.0), pitch_width - 6.0)
        out.append((ax * pitch_length / 105.0, ay * pitch_width / 68.0))
    return out


def hungarian(cost: np.ndarray) -> list[tuple[int, int]]:
    """Exact minimum-cost assignment (Jonker-Volgenant style shortest paths).

    ``cost`` is ``[n_rows, n_cols]`` with ``n_rows <= n_cols``. Returns
    ``[(row, col), ...]``. Implemented here so scipy stays an optional dep.
    """
    cost = np.asarray(cost, dtype=float)
    n, m = cost.shape
    if n > m:
        raise ValueError("hungarian expects n_rows <= n_cols")

    INF = float("inf")
    u = np.zeros(n + 1)
    v = np.zeros(m + 1)
    p = np.zeros(m + 1, dtype=int)   # p[j] = row assigned to column j
    way = np.zeros(m + 1, dtype=int)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = np.full(m + 1, INF)
        used = np.zeros(m + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta, j1 = INF, -1
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1, j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    return [(int(p[j] - 1), j - 1) for j in range(1, m + 1) if p[j] > 0]


@dataclass
class XIAssignment:
    formation: str
    slots: list[dict] = field(default_factory=list)
    bench: list[dict] = field(default_factory=list)
    total_effective_level: float = 0.0
    #: Mean level of the XI, comparable across formations.
    mean_effective_level: float = 0.0
    balance: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: Players the optimiser refused to rank, and why. See `features.split_rankable`.
    excluded: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "formation": self.formation,
            "slots": self.slots,
            "bench": self.bench,
            "total_effective_level": round(self.total_effective_level, 2),
            "mean_effective_level": round(self.mean_effective_level, 2),
            "balance": self.balance,
            "warnings": self.warnings,
            "excluded": self.excluded,
        }


def _player_slot_value(
    player, position: Position, *, minute: int = 0, ignore_load: bool = False
) -> float:
    """Value of putting ``player`` in ``position``.

    ``ignore_load`` gives the shape at full freshness — the question "who is our
    best XI" as opposed to "who should start this Saturday". Both are useful and
    they routinely differ during a congested run.
    """
    fit = position_fit(player, position)
    if fit < 0.2:
        return 0.0
    availability = 1.0 if getattr(player, "is_available", True) else 0.0

    if ignore_load:
        return float(effective_level(player, position) * availability)

    fs = fatigue_state(
        minutes_played=minute,
        age=getattr(player, "age", None),
        stamina=attribute(player, "stamina", rating_or(player, UNRANKED)),
        minutes_last_7d=getattr(player, "minutes_last_7d", 0) or 0,
        baseline_fatigue=getattr(player, "fatigue", 0.0) or 0.0,
    )
    # Fatigue already carries most of the condition signal; a second full-weight
    # fitness multiplier would double-count it.
    fitness = (getattr(player, "fitness", 100.0) or 100.0) / 100.0
    return float(
        effective_level(player, position, fs.performance_multiplier)
        * (0.85 + 0.15 * fitness)
        * availability
    )


def best_xi(
    players: list,
    formation: str = "4-3-3",
    *,
    minute: int = 0,
    locked: dict[int, str] | None = None,
    bench_size: int = 7,
    ignore_load: bool = False,
) -> XIAssignment:
    """Pick the strongest XI for ``formation``.

    ``locked`` pins ``slot_index -> player_id`` (the manager's non-negotiables).
    ``ignore_load`` ranks on raw quality, ignoring accumulated fatigue.
    """
    slots = FORMATION_SLOTS.get(formation)
    if slots is None:
        raise ValueError(f"Unknown formation '{formation}'. Known: {sorted(FORMATION_SLOTS)}")

    # An ungraded player cannot be ordered against a graded one, and inventing a
    # rating to make the maths work is exactly what this product must not do.
    rankable, excluded = split_rankable(
        [p for p in players if getattr(p, "is_available", True)])
    available = rankable
    result = XIAssignment(formation=formation, excluded=excluded)
    if excluded:
        result.warnings.append(
            f"{len(excluded)} available player(s) were left out of selection "
            "because no rating is on file for them."
        )

    if len(available) < len(slots):
        result.warnings.append(
            f"Only {len(available)} available players for {len(slots)} slots — "
            "the XI is incomplete."
        )
        if not available:
            return result

    locked = locked or {}
    locked_players = {pid for pid in locked.values()}
    free_slots = [i for i in range(len(slots)) if i not in locked]
    free_players = [p for p in available if p.id not in locked_players]

    n_slots, n_players = len(free_slots), len(free_players)
    assignment: dict[int, object] = {}

    if n_slots and n_players:
        value = np.zeros((n_slots, max(n_players, n_slots)))
        for si, slot_idx in enumerate(free_slots):
            for pi, player in enumerate(free_players):
                value[si, pi] = _player_slot_value(
                    player, slots[slot_idx], minute=minute, ignore_load=ignore_load
                )
        # Hungarian minimises; assignment maximises.
        pairs = hungarian(value.max() - value)
        for si, pi in pairs:
            if si < n_slots and pi < n_players and value[si, pi] > 0:
                assignment[free_slots[si]] = free_players[pi]

    by_id = {p.id: p for p in available}
    for slot_idx, pid in locked.items():
        if pid in by_id and 0 <= slot_idx < len(slots):
            assignment[slot_idx] = by_id[pid]

    anchors = formation_anchors(formation)
    total = 0.0
    for idx, position in enumerate(slots):
        player = assignment.get(idx)
        anchor = anchors[idx]
        entry = {
            "slot_index": idx,
            "position": position.value,
            "x": round(anchor[0] / 105.0, 4),
            "y": round(anchor[1] / 68.0, 4),
            "locked": idx in locked,
        }
        if player is None:
            entry.update({"player_id": None, "player": None, "effective_level": 0.0})
            result.warnings.append(f"No player assigned to slot {idx} ({position.value})")
        else:
            val = _player_slot_value(player, position, minute=minute, ignore_load=ignore_load)
            fit = position_fit(player, position)
            total += val
            entry.update({
                "player_id": player.id,
                "player": player.display_name,
                "shirt_number": getattr(player, "shirt_number", None),
                "overall_rating": player.overall_rating,
                "natural_position": player.primary_position.value,
                "position_fit": round(fit, 3),
                "effective_level": round(val, 2),
                "out_of_position": fit < 0.8,
            })
            if fit < 0.7:
                result.warnings.append(
                    f"{player.display_name} at {position.value} is a {fit:.0%} fit "
                    f"(natural: {player.primary_position.value})"
                )
        result.slots.append(entry)

    used = {e["player_id"] for e in result.slots if e.get("player_id")}
    remaining = sorted(
        (p for p in available if p.id not in used),
        key=lambda p: -_player_slot_value(p, p.primary_position, ignore_load=ignore_load),
    )
    result.bench = [
        {
            "player_id": p.id,
            "player": p.display_name,
            "position": p.primary_position.value,
            "overall_rating": p.overall_rating,
            "fitness": getattr(p, "fitness", 100.0),
            "effective_level": round(
                _player_slot_value(p, p.primary_position, ignore_load=ignore_load), 2
            ),
        }
        for p in remaining[:bench_size]
    ]

    result.total_effective_level = total
    filled = sum(1 for e in result.slots if e.get("player_id"))
    result.mean_effective_level = total / filled if filled else 0.0
    result.balance = _balance(result.slots, players)
    return result


def _balance(slots: list[dict], players: list) -> dict:
    """Squad-shape sanity checks the coach would run by eye."""
    by_id = {p.id: p for p in players}
    lefties = righties = 0
    pace_vals, defend_vals = [], []
    for e in slots:
        p = by_id.get(e.get("player_id") or "")
        if p is None:
            continue
        foot = getattr(getattr(p, "preferred_foot", None), "value", "right")
        if foot == "left":
            lefties += 1
        elif foot == "right":
            righties += 1
        pace_vals.append(attribute(p, "pace", rating_or(p, UNRANKED)))
        defend_vals.append(attribute(p, "defending", rating_or(p, UNRANKED)))

    return {
        "left_footed": lefties,
        "right_footed": righties,
        "avg_pace": round(float(np.mean(pace_vals)), 1) if pace_vals else None,
        "avg_defending": round(float(np.mean(defend_vals)), 1) if defend_vals else None,
        "out_of_position_count": sum(1 for e in slots if e.get("out_of_position")),
    }


def compare_formations(
    players: list, formations: list[str] | None = None, *, minute: int = 0,
    top_n: int = 5, ignore_load: bool = False,
) -> list[dict]:
    """Rank formations by the strength of the XI each one unlocks.

    This is the honest way to answer "should we change shape": the best shape is
    the one this squad fills best, not the one that sounds most modern.
    """
    formations = formations or list(FORMATION_SLOTS)
    out = []
    for f in formations:
        if f not in FORMATION_SLOTS:
            continue
        xi = best_xi(players, f, minute=minute, ignore_load=ignore_load)
        out.append({
            "formation": f,
            "mean_effective_level": round(xi.mean_effective_level, 2),
            "total_effective_level": round(xi.total_effective_level, 2),
            "out_of_position_count": xi.balance.get("out_of_position_count", 0),
            "warnings": len(xi.warnings),
            "xi": [
                {"position": s["position"], "player": s.get("player"),
                 "position_fit": s.get("position_fit")}
                for s in xi.slots
            ],
        })
    out.sort(key=lambda d: (-d["mean_effective_level"], d["out_of_position_count"]))
    return out[:top_n]
