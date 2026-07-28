"""Tactical profiling: how a side defends, how it attacks, and where it leaks.

Everything here returns a 0-100 index plus the raw quantity it came from, so a
coach can see both the score and the number behind it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from app.services.analytics.metrics import expected_goals, progressive, xt_delta
from app.services.analytics.pitch import Pitch


def _index(value: float, low: float, high: float) -> float:
    """Map a raw quantity onto 0-100, clamped. ``low`` maps to 0."""
    if high == low:
        return 50.0
    return round(float(np.clip((value - low) / (high - low), 0, 1) * 100), 1)


@dataclass
class DefensiveProfile:
    #: 0-100; higher = pressing higher and more aggressively.
    press_intensity_index: float = 50.0
    line_height_index: float = 50.0
    compactness_index: float = 50.0
    #: Where defensive actions actually happen.
    avg_defensive_action_x: float | None = None
    #: Recoveries within 5 s of losing the ball, per 100 losses.
    counterpress_recovery_pct: float | None = None
    #: Opponent shots and xG conceded, split by build-up route.
    xg_conceded: float = 0.0
    shots_conceded: int = 0
    #: Zones the opponent progressed through most, ordered.
    leak_zones: list[dict] = field(default_factory=list)
    style: str = "unknown"
    notes: list[str] = field(default_factory=list)


@dataclass
class OffensiveProfile:
    directness_index: float = 50.0
    width_index: float = 50.0
    build_up_index: float = 50.0     # 0 = long, 100 = short build-up
    #: Share of attacks by flank/centre.
    channel_share: dict[str, float] = field(default_factory=dict)
    xg_created: float = 0.0
    shots: int = 0
    xt_created: float = 0.0
    progressive_passes: int = 0
    progressive_carries: int = 0
    #: Passes into the box per 100 final-third entries.
    box_entry_rate: float | None = None
    style: str = "unknown"
    notes: list[str] = field(default_factory=list)


@dataclass
class TacticalProfile:
    defensive: DefensiveProfile = field(default_factory=DefensiveProfile)
    offensive: OffensiveProfile = field(default_factory=OffensiveProfile)
    #: Free-text label combining both, e.g. "high press · vertical".
    identity: str = "unknown"
    #: Named weaknesses, each with evidence, ordered by severity.
    vulnerabilities: list[dict] = field(default_factory=list)
    strengths: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def analyse_defence(
    events: list,
    *,
    pitch: Pitch | None = None,
    formation_line_height: float | None = None,
    vertical_compactness: float | None = None,
) -> DefensiveProfile:
    pitch = pitch or Pitch()
    prof = DefensiveProfile()

    def_actions = [
        e for e in events
        if bool(e.is_own_team) and (e.type or "").lower()
        in {"tackle", "interception", "pressure", "clearance", "recovery", "challenge"}
    ]
    if def_actions:
        avg_x = float(np.mean([pitch.clip(e.x or 0, e.y or 0)[0] for e in def_actions]))
        prof.avg_defensive_action_x = round(avg_x, 2)
        # 28 m = deep block, 58 m = aggressive high press. Bands come from the
        # observed spread of average defensive-action height in senior football;
        # too narrow a band pegs every side at 0 or 100.
        prof.press_intensity_index = _index(avg_x, 28.0, 58.0)

    if formation_line_height is not None:
        # 20 m = a back line camped on its own box, 50 m = a high line.
        prof.line_height_index = _index(formation_line_height, 20.0, 50.0)
    if vertical_compactness is not None:
        # 45 m stretched → 0; 25 m compact → 100.
        prof.compactness_index = _index(-vertical_compactness, -45.0, -25.0)

    # Counterpressing: possession losses followed by a recovery within 5 s.
    losses = 0
    quick_recoveries = 0
    ordered = sorted(events, key=lambda e: (e.period, e.minute, e.second))
    for i, ev in enumerate(ordered):
        etype = (ev.type or "").lower()
        lost = bool(ev.is_own_team) and (
            (etype == "pass" and (ev.outcome or "").lower() not in {"success", "complete", "completed"})
            or etype in {"dispossessed", "miscontrol", "turnover"}
        )
        if not lost:
            continue
        losses += 1
        t0 = ev.minute * 60 + ev.second
        for nxt in ordered[i + 1 : i + 12]:
            if (nxt.minute * 60 + nxt.second) - t0 > 5:
                break
            if bool(nxt.is_own_team) and (nxt.type or "").lower() in {
                "recovery", "interception", "tackle"
            }:
                quick_recoveries += 1
                break
    if losses:
        prof.counterpress_recovery_pct = round(100 * quick_recoveries / losses, 1)

    # xG conceded and the zones it came through.
    opp_shots = [e for e in events if not e.is_own_team and (e.type or "").lower() == "shot"]
    prof.shots_conceded = len(opp_shots)
    prof.xg_conceded = round(
        sum(
            expected_goals(
                *pitch.clip(e.x or 0, e.y or 0),
                pitch=pitch,
                situation=(e.qualifiers or {}).get("situation", "open_play"),
                body_part=(e.qualifiers or {}).get("body_part", "foot"),
            )
            for e in opp_shots
        ),
        3,
    )

    prof.leak_zones = _leak_zones(events, pitch)
    prof.style = _defensive_style(prof)
    return prof


def _leak_zones(events: list, pitch: Pitch) -> list[dict]:
    """Where the opponent generated threat against us, by lane and third."""
    lanes = {"left": 0.0, "left half-space": 0.0, "centre": 0.0,
             "right half-space": 0.0, "right": 0.0}
    bounds = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    names = ["right", "right half-space", "centre", "left half-space", "left"]

    for e in events:
        if e.is_own_team or (e.type or "").lower() not in {"pass", "carry", "cross", "shot"}:
            continue
        if (e.outcome or "success").lower() not in {"success", "complete", "completed", "goal", "on_target"}:
            continue
        sx, sy = pitch.clip(e.x or 0, e.y or 0)
        ex, ey = pitch.clip(e.end_x if e.end_x is not None else sx,
                            e.end_y if e.end_y is not None else sy)
        gain = max(0.0, xt_delta((sx, sy), (ex, ey), pitch))
        if gain <= 0:
            continue
        frac = ey / pitch.width
        for i in range(5):
            if bounds[i] <= frac < bounds[i + 1] or (i == 4 and frac == 1.0):
                lanes[names[i]] += gain
                break

    total = sum(lanes.values())
    out = [
        {
            "lane": k,
            "threat_conceded": round(v, 4),
            "share_pct": round(100 * v / total, 1) if total else 0.0,
        }
        for k, v in lanes.items()
    ]
    return sorted(out, key=lambda d: -d["threat_conceded"])


def _defensive_style(p: DefensiveProfile) -> str:
    if p.press_intensity_index >= 65:
        base = "high press"
    elif p.press_intensity_index <= 35:
        base = "low block"
    else:
        base = "mid block"
    if p.compactness_index >= 65:
        return f"{base} · compact"
    if p.compactness_index <= 35:
        return f"{base} · stretched"
    return base


def analyse_attack(events: list, *, pitch: Pitch | None = None) -> OffensiveProfile:
    pitch = pitch or Pitch()
    prof = OffensiveProfile()

    own = [e for e in events if bool(e.is_own_team)]
    passes = [e for e in own if (e.type or "").lower() in {"pass", "cross"}]
    carries = [e for e in own if (e.type or "").lower() in {"carry", "dribble"}]
    shots = [e for e in own if (e.type or "").lower() == "shot"]

    prof.shots = len(shots)
    prof.xg_created = round(
        sum(
            expected_goals(
                *pitch.clip(e.x or 0, e.y or 0),
                pitch=pitch,
                situation=(e.qualifiers or {}).get("situation", "open_play"),
                body_part=(e.qualifiers or {}).get("body_part", "foot"),
            )
            for e in shots
        ),
        3,
    )

    forward_gain, total_len, xt_sum = [], [], 0.0
    lanes = {"left": 0, "centre": 0, "right": 0}
    box_entries, final_third_entries = 0, 0

    for e in passes + carries:
        sx, sy = pitch.clip(e.x or 0, e.y or 0)
        if e.end_x is None or e.end_y is None:
            continue
        ex, ey = pitch.clip(e.end_x, e.end_y)
        completed = (e.outcome or "success").lower() in {"success", "complete", "completed", "goal"}
        length = float(np.hypot(ex - sx, ey - sy))
        total_len.append(length)
        forward_gain.append(ex - sx)
        if completed:
            xt_sum += max(0.0, xt_delta((sx, sy), (ex, ey), pitch))
            if progressive((sx, sy), (ex, ey), pitch):
                if (e.type or "").lower() in {"pass", "cross"}:
                    prof.progressive_passes += 1
                else:
                    prof.progressive_carries += 1
            if pitch.third_of(ex) == "attacking" and pitch.third_of(sx) != "attacking":
                final_third_entries += 1
            if pitch.in_penalty_area(ex, ey) and not pitch.in_penalty_area(sx, sy):
                box_entries += 1
        frac = ey / pitch.width
        lanes["right" if frac < 1 / 3 else "centre" if frac < 2 / 3 else "left"] += 1

    prof.xt_created = round(xt_sum, 4)

    if total_len:
        mean_len = float(np.mean(total_len))
        mean_fwd = float(np.mean(forward_gain))
        # 8 m sideways → patient; 22 m forward-heavy → direct.
        prof.directness_index = _index(mean_fwd * 0.6 + mean_len * 0.4, 4.0, 20.0)
        prof.build_up_index = _index(-mean_len, -28.0, -12.0)

    lane_total = sum(lanes.values())
    if lane_total:
        prof.channel_share = {k: round(100 * v / lane_total, 1) for k, v in lanes.items()}
        wing = prof.channel_share["left"] + prof.channel_share["right"]
        # Two of three lanes are flanks, so a perfectly even spread is ~67%.
        # Senior sides sit between roughly 30% (very narrow) and 70% (very wide)
        # wing share; the band is set on that observed range, not on an even
        # split, so the index has resolution where teams actually differ.
        prof.width_index = _index(wing, 30.0, 70.0)

    if final_third_entries:
        prof.box_entry_rate = round(100 * box_entries / final_third_entries, 1)

    prof.style = _offensive_style(prof)
    return prof


def _offensive_style(p: OffensiveProfile) -> str:
    tempo = "direct" if p.directness_index >= 62 else "patient" if p.directness_index <= 38 else "measured"
    shape = "wide" if p.width_index >= 62 else "central" if p.width_index <= 38 else "balanced"
    return f"{tempo} · {shape}"


def build_profile(
    events: list,
    *,
    pitch: Pitch | None = None,
    formation_line_height: float | None = None,
    vertical_compactness: float | None = None,
) -> TacticalProfile:
    pitch = pitch or Pitch()
    prof = TacticalProfile(
        defensive=analyse_defence(
            events,
            pitch=pitch,
            formation_line_height=formation_line_height,
            vertical_compactness=vertical_compactness,
        ),
        offensive=analyse_attack(events, pitch=pitch),
    )
    prof.identity = f"{prof.defensive.style} · {prof.offensive.style}"
    prof.vulnerabilities = _find_vulnerabilities(prof)
    prof.strengths = _find_strengths(prof)
    return prof


def _find_vulnerabilities(p: TacticalProfile) -> list[dict]:
    out: list[dict] = []
    d, o = p.defensive, p.offensive

    if d.press_intensity_index >= 65 and d.compactness_index <= 40:
        out.append({
            "id": "press_without_compactness",
            "title": "High press with a stretched block",
            "severity": 82,
            "detail": (
                "The side presses high but the distance from the back line to the "
                "front line is large, leaving the space in behind exposed to one pass."
            ),
            "evidence": {
                "press_intensity_index": d.press_intensity_index,
                "compactness_index": d.compactness_index,
            },
        })

    if d.counterpress_recovery_pct is not None and d.counterpress_recovery_pct < 22:
        out.append({
            "id": "weak_counterpress",
            "title": "Slow reaction to losing the ball",
            "severity": 68,
            "detail": (
                f"Only {d.counterpress_recovery_pct}% of losses are recovered within "
                "five seconds; the reference band for a pressing side is 30-40%."
            ),
            "evidence": {"counterpress_recovery_pct": d.counterpress_recovery_pct},
        })

    if d.leak_zones:
        top = d.leak_zones[0]
        if top["share_pct"] >= 32:
            out.append({
                "id": f"lane_leak_{top['lane'].replace(' ', '_')}",
                "title": f"Threat concentrated down the {top['lane']}",
                "severity": min(95, 45 + top["share_pct"]),
                "detail": (
                    f"{top['share_pct']}% of the threat conceded came through the "
                    f"{top['lane']}, against an even split of 20%."
                ),
                "evidence": top,
            })

    if o.box_entry_rate is not None and o.box_entry_rate < 18 and o.progressive_passes > 0:
        out.append({
            "id": "final_third_stall",
            "title": "Reaches the final third but not the box",
            "severity": 64,
            "detail": (
                f"Only {o.box_entry_rate} box entries per 100 final-third entries. "
                "Progression is fine; the last pass is the bottleneck."
            ),
            "evidence": {"box_entry_rate": o.box_entry_rate,
                         "progressive_passes": o.progressive_passes},
        })

    if o.shots and o.xg_created / max(o.shots, 1) < 0.07:
        out.append({
            "id": "low_shot_quality",
            "title": "Shot volume without shot quality",
            "severity": 58,
            "detail": (
                f"Average shot is worth {o.xg_created / o.shots:.3f} xG; below 0.07 "
                "indicates the side is settling for range efforts."
            ),
            "evidence": {"shots": o.shots, "xg": o.xg_created},
        })

    return sorted(out, key=lambda d_: -d_["severity"])


def _find_strengths(p: TacticalProfile) -> list[dict]:
    out: list[dict] = []
    d, o = p.defensive, p.offensive
    if d.counterpress_recovery_pct is not None and d.counterpress_recovery_pct >= 35:
        out.append({
            "id": "strong_counterpress",
            "title": "Excellent counterpressing",
            "score": min(100, d.counterpress_recovery_pct * 2),
            "evidence": {"counterpress_recovery_pct": d.counterpress_recovery_pct},
        })
    if o.xt_created >= 1.5:
        out.append({
            "id": "high_threat_creation",
            "title": "Consistent threat generation in open play",
            "score": min(100, o.xt_created * 30),
            "evidence": {"xt_created": o.xt_created},
        })
    if d.compactness_index >= 68:
        out.append({
            "id": "compact_block",
            "title": "Compact defensive block",
            "score": d.compactness_index,
            "evidence": {"compactness_index": d.compactness_index},
        })
    if o.box_entry_rate is not None and o.box_entry_rate >= 30:
        out.append({
            "id": "efficient_final_ball",
            "title": "Turns final-third entries into box entries",
            "score": min(100, o.box_entry_rate * 2),
            "evidence": {"box_entry_rate": o.box_entry_rate},
        })
    return sorted(out, key=lambda d_: -d_["score"])
