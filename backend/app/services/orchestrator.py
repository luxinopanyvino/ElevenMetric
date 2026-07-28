"""Analysis orchestrator.

Decides which model family runs based on **what data actually arrived**, runs
it, and assembles a report whose confidence is honest about the gaps.

The routing rule:

===================  ==========================================================
Input present        What runs
===================  ==========================================================
squad only           lineup optimisation, formation comparison, workload
+ event data         possession, xG/xT, tactical profile, substitution engine
+ tracking           time possession, true heatmaps, played-shape detection
+ video              CV pipeline first, producing tracking, then as above
===================  ==========================================================

Nothing is imputed across tiers. A report from event data alone reports
``time_possession_pct = null`` rather than guessing it, and every
recommendation is scaled by ``data_completeness``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.models.catalog import Position
from app.models.match import InputSource
from app.services.analytics import formation as formation_mod
from app.services.analytics import heatmap as heatmap_mod
from app.services.analytics import possession as possession_mod
from app.services.analytics import tactics as tactics_mod
from app.services.analytics.metrics import (
    XG_MODEL_VERSION,
    XT_MODEL_VERSION,
    expected_goals,
    progressive,
    xt_delta,
)
from app.services.analytics.pitch import Pitch
from app.services.ml import lineup_optimizer, substitution
from app.services.ml.registry import get_model

#: Weight of each input tier in the completeness score.
COMPLETENESS_WEIGHTS = {
    "squad": 0.20,
    "lineup": 0.15,
    "event_data": 0.35,
    "tracking": 0.25,
    "context": 0.05,
}


@dataclass
class AnalysisInput:
    """Everything the orchestrator can consume. All fields optional by design."""

    players: list = field(default_factory=list)
    starters: list = field(default_factory=list)   # (player, Position, minutes)
    bench: list = field(default_factory=list)
    events: list = field(default_factory=list)
    frames: list = field(default_factory=list)
    declared_formation: str | None = None
    minute: int = 90
    score_difference: int = 0
    subs_used: int = 0
    windows_used: int = 0
    source: InputSource = InputSource.manual
    pitch: Pitch = field(default_factory=Pitch)
    #: Passed through from the CV pipeline when the source was video.
    cv_meta: dict = field(default_factory=dict)


@dataclass
class AnalysisOutput:
    possession: dict = field(default_factory=dict)
    heatmaps: dict = field(default_factory=dict)
    formation: dict = field(default_factory=dict)
    tactics: dict = field(default_factory=dict)
    player_metrics: dict = field(default_factory=dict)
    zones: dict = field(default_factory=dict)
    phases: dict = field(default_factory=dict)
    recommendations: list[dict] = field(default_factory=list)
    inputs_used: list[str] = field(default_factory=list)
    data_completeness: float = 0.0
    confidence: float = 0.0
    summary: str = ""
    model_versions: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def analyse(inp: AnalysisInput) -> AnalysisOutput:
    out = AnalysisOutput()
    pitch = inp.pitch

    has_events = len(inp.events) > 0
    has_tracking = len(inp.frames) > 0
    has_squad = len(inp.players) > 0
    has_lineup = len(inp.starters) > 0

    if has_squad:
        out.inputs_used.append("squad")
    if has_lineup:
        out.inputs_used.append("lineup")
    if has_events:
        out.inputs_used.append("event_data")
    if has_tracking:
        out.inputs_used.append("tracking")
    if inp.cv_meta:
        out.inputs_used.append("video")

    completeness = sum(
        w for k, w in COMPLETENESS_WEIGHTS.items()
        if (k == "squad" and has_squad)
        or (k == "lineup" and has_lineup)
        or (k == "event_data" and has_events)
        or (k == "tracking" and has_tracking)
        or (k == "context" and inp.declared_formation is not None)
    )
    out.data_completeness = round(completeness, 3)

    if inp.cv_meta.get("engine") == "simulated":
        out.warnings.append(
            "This report was produced from SIMULATED tracking, not from the "
            "uploaded video. Install the CV extras for real analysis."
        )

    # --- Possession -------------------------------------------------------
    if has_events or has_tracking:
        ev_res = (
            possession_mod.possession_from_events(inp.events, pitch=pitch)
            if has_events else possession_mod.PossessionResult()
        )
        tr_res = (
            possession_mod.possession_from_tracking(inp.frames, pitch=pitch)
            if has_tracking else possession_mod.PossessionResult()
        )
        # Tracking wins for time-based numbers; events win for on-ball ones.
        merged = possession_mod.merge(tr_res, ev_res) if has_tracking else ev_res
        out.possession = merged.to_dict()

    # --- Heatmaps and zones -----------------------------------------------
    if has_tracking:
        out.heatmaps, out.zones = _heatmaps_from_tracking(inp.frames, pitch)
    elif has_events:
        out.heatmaps, out.zones = _heatmaps_from_events(inp.events, pitch)

    # --- Formation --------------------------------------------------------
    detected = formation_mod.FormationResult()
    if has_tracking:
        from app.services.cv.pipeline import frames_to_average_positions

        avg = frames_to_average_positions(inp.frames, "home")
        if avg:
            detected = formation_mod.detect_formation(avg, pitch=pitch)
    elif has_events:
        avg = _average_positions_from_events(inp.events)
        if len(avg) >= 7:
            detected = formation_mod.detect_formation(avg, pitch=pitch)

    out.formation = detected.to_dict()
    if inp.declared_formation:
        out.formation["deviation"] = formation_mod.shape_deviation(
            inp.declared_formation, detected
        )

    # --- Tactical profile --------------------------------------------------
    if has_events:
        profile = tactics_mod.build_profile(
            inp.events,
            pitch=pitch,
            formation_line_height=detected.defensive_line_height or None,
            vertical_compactness=detected.vertical_compactness or None,
        )
        out.tactics = profile.to_dict()
        vulnerabilities = profile.vulnerabilities
    else:
        vulnerabilities = []

    # --- Per-player metrics ------------------------------------------------
    if has_events:
        out.player_metrics = _player_metrics(inp.events, pitch)

    # --- Recommendations ---------------------------------------------------
    recs: list[dict] = []

    if has_lineup and inp.bench:
        subs = substitution.recommend_substitutions(
            starters=inp.starters,
            bench=inp.bench,
            minute=inp.minute,
            score_difference=inp.score_difference,
            vulnerabilities=vulnerabilities,
            events=inp.events,
            subs_used=inp.subs_used,
            windows_used=inp.windows_used,
        )
        for s in subs:
            d = s.to_dict()
            recs.append({
                "kind": "substitution",
                "title": f"{d['player_out']} → {d['player_in']} ({d['position']})",
                "detail": " · ".join(d["drivers"]) if d["drivers"] else "",
                "priority": d["priority"],
                "confidence": d["confidence"] * max(0.35, completeness),
                "expected_gain": d["expected_gain"],
                "expected_gain_unit": d["expected_gain_unit"],
                "minute_window": d["minute_window"],
                "player_out_id": d["player_out_id"],
                "player_in_id": d["player_in_id"],
                "drivers": d["drivers"],
                "evidence": d["evidence"],
            })

    if has_lineup:
        # Cap the list: eleven near-identical alerts bury the findings that
        # actually differ.
        alerts = substitution.workload_alerts(inp.starters, minute=inp.minute)[:3]
        for a in alerts:
            recs.append({
                "kind": "workload",
                "title": f"Workload alert: {a['player']}",
                "detail": a["recommendation"],
                "priority": min(95.0, 40 + a["injury_hazard"] * 180),
                "confidence": 0.6 * max(0.35, completeness),
                "expected_gain": 0.0,
                "expected_gain_unit": "matches available",
                "player_out_id": a["player_id"],
                "drivers": [
                    f"Injury hazard {a['injury_hazard']:.1%}",
                    f"{a['minutes_last_7d']}' in the last 7 days",
                ],
                "evidence": a,
            })

    if has_squad:
        alternatives = lineup_optimizer.compare_formations(inp.players, minute=0, top_n=3)
        if alternatives and inp.declared_formation:
            current = next(
                (a for a in alternatives if a["formation"] == inp.declared_formation), None
            )
            best = alternatives[0]
            if best["formation"] != inp.declared_formation:
                gain = best["mean_effective_level"] - (
                    current["mean_effective_level"] if current else 0.0
                )
                if gain > 0.6:
                    recs.append({
                        "kind": "formation_change",
                        "title": f"Consider {best['formation']} over {inp.declared_formation}",
                        "detail": (
                            f"The squad fills {best['formation']} at a mean effective level of "
                            f"{best['mean_effective_level']:.1f}"
                            + (f" against {current['mean_effective_level']:.1f} for "
                               f"{inp.declared_formation}" if current else "")
                            + f" — {best['out_of_position_count']} players out of position."
                        ),
                        "priority": min(90.0, 45 + gain * 8),
                        "confidence": 0.55 * max(0.35, completeness),
                        "expected_gain": round(gain, 2),
                        "expected_gain_unit": "effective-level points",
                        "drivers": [
                            f"{a['formation']}: {a['mean_effective_level']:.1f} mean effective level"
                            for a in alternatives
                        ],
                        "evidence": {"alternatives": alternatives},
                    })

    for v in vulnerabilities[:3]:
        recs.append({
            "kind": "instruction_change",
            "title": v["title"],
            "detail": v["detail"],
            "priority": float(v["severity"]),
            "confidence": 0.65 * max(0.35, completeness),
            "expected_gain": 0.0,
            "expected_gain_unit": "xGD/90",
            "drivers": [v["detail"]],
            "evidence": v.get("evidence", {}),
        })

    recs.sort(key=lambda r: -r["priority"])
    out.recommendations = recs

    out.confidence = round(
        float(np.clip(0.25 + 0.65 * completeness - (0.25 if inp.cv_meta.get("engine") == "simulated" else 0.0), 0, 0.95)),
        3,
    )
    out.model_versions = {
        "xg": XG_MODEL_VERSION,
        "xt": XT_MODEL_VERSION,
        "impact": get_model("impact").version,
    }
    out.summary = _summarise(out)
    return out


# --- Helpers ---------------------------------------------------------------

def _heatmaps_from_tracking(frames: list, pitch: Pitch) -> tuple[dict, dict]:
    per_player: dict[str, list[tuple[float, float]]] = {}
    team_points: list[tuple[float, float]] = []
    opp_points: list[tuple[float, float]] = []

    for f in frames:
        for pid, pos in (f.home_positions or {}).items():
            per_player.setdefault(pid, []).append((pos[0], pos[1]))
            team_points.append((pos[0], pos[1]))
        for pos in (f.away_positions or {}).values():
            opp_points.append((pos[0], pos[1]))

    heatmaps = {
        "team": heatmap_mod.build_heatmap(team_points, pitch=pitch, method="histogram").to_dict(),
        "players": {
            pid: heatmap_mod.build_heatmap(pts, pitch=pitch).to_dict()
            for pid, pts in per_player.items()
        },
        "source": "tracking",
    }
    zones = heatmap_mod.zone_control(team_points, opp_points, pitch=pitch)
    return heatmaps, zones


def _heatmaps_from_events(events: list, pitch: Pitch) -> tuple[dict, dict]:
    per_player: dict[str, list[tuple[float, float]]] = {}
    team_points: list[tuple[float, float]] = []
    opp_points: list[tuple[float, float]] = []

    for e in events:
        x, y = pitch.clip(e.x or 0.0, e.y or 0.0)
        if e.is_own_team:
            team_points.append((x, y))
            if e.player_id:
                per_player.setdefault(e.player_id, []).append((x, y))
        else:
            # Mirror the opponent into our frame so the zone grid is comparable.
            opp_points.append((pitch.length - x, pitch.width - y))

    heatmaps = {
        "team": heatmap_mod.build_heatmap(team_points, pitch=pitch).to_dict(),
        "players": {
            pid: heatmap_mod.build_heatmap(pts, pitch=pitch).to_dict()
            for pid, pts in per_player.items()
        },
        "source": "event_data",
        "note": "Touch density, not time occupancy — event data has no off-ball positions.",
    }
    zones = heatmap_mod.zone_control(team_points, opp_points, pitch=pitch)
    return heatmaps, zones


def _average_positions_from_events(events: list) -> dict[str, tuple[float, float]]:
    acc: dict[str, list[tuple[float, float]]] = {}
    for e in events:
        if not e.is_own_team or not e.player_id:
            continue
        acc.setdefault(e.player_id, []).append((e.x or 0.0, e.y or 0.0))
    return {
        pid: (float(np.mean([p[0] for p in pts])), float(np.mean([p[1] for p in pts])))
        for pid, pts in acc.items()
        if len(pts) >= 8
    }


def _player_metrics(events: list, pitch: Pitch) -> dict:
    stats: dict[str, dict] = {}
    for e in events:
        if not e.is_own_team or not e.player_id:
            continue
        s = stats.setdefault(e.player_id, {
            "touches": 0, "passes": 0, "passes_completed": 0, "shots": 0,
            "xg": 0.0, "xt": 0.0, "progressive_passes": 0, "progressive_carries": 0,
            "defensive_actions": 0, "losses": 0,
        })
        etype = (e.type or "").lower()
        completed = (e.outcome or "success").lower() in {"success", "complete", "completed", "goal"}
        x, y = pitch.clip(e.x or 0.0, e.y or 0.0)

        if etype in possession_mod.ON_BALL_TYPES:
            s["touches"] += 1
        if etype in {"pass", "cross"}:
            s["passes"] += 1
            if completed:
                s["passes_completed"] += 1
            else:
                s["losses"] += 1
        if etype == "shot":
            s["shots"] += 1
            s["xg"] += expected_goals(
                x, y, pitch=pitch,
                situation=(e.qualifiers or {}).get("situation", "open_play"),
                body_part=(e.qualifiers or {}).get("body_part", "foot"),
            )
        if etype in possession_mod.DEFENSIVE_ACTION_TYPES or etype == "recovery":
            s["defensive_actions"] += 1

        if completed and e.end_x is not None and e.end_y is not None:
            ex, ey = pitch.clip(e.end_x, e.end_y)
            s["xt"] += max(0.0, xt_delta((x, y), (ex, ey), pitch))
            if progressive((x, y), (ex, ey), pitch):
                if etype in {"pass", "cross"}:
                    s["progressive_passes"] += 1
                elif etype in {"carry", "dribble"}:
                    s["progressive_carries"] += 1

    for s in stats.values():
        s["xg"] = round(s["xg"], 4)
        s["xt"] = round(s["xt"], 4)
        s["pass_accuracy_pct"] = (
            round(100 * s["passes_completed"] / s["passes"], 1) if s["passes"] else None
        )
    return stats


def _summarise(out: AnalysisOutput) -> str:
    parts: list[str] = []
    p = out.possession
    if p:
        bits = []
        if p.get("time_possession_pct") is not None:
            bits.append(f"{p['time_possession_pct']}% of the ball by time")
        if p.get("field_tilt_pct") is not None:
            bits.append(f"field tilt {p['field_tilt_pct']}%")
        if p.get("ppda") is not None:
            bits.append(f"PPDA {p['ppda']}")
        if bits:
            parts.append("Territory: " + ", ".join(bits) + ".")

    f = out.formation
    if f.get("formation") and f["formation"] != "unknown":
        line = f"Played shape read as {f['formation']}"
        dev = f.get("deviation") or {}
        if dev.get("declared") and not dev.get("matches"):
            line += f" against a declared {dev['declared']}"
        parts.append(line + ".")

    t = out.tactics
    if t.get("identity") and t["identity"] != "unknown":
        parts.append(f"Style profile: {t['identity']}.")
    vulns = (t.get("vulnerabilities") or [])[:2]
    if vulns:
        parts.append("Main exposures: " + "; ".join(v["title"].lower() for v in vulns) + ".")

    subs = [r for r in out.recommendations if r["kind"] == "substitution"]
    if subs:
        top = subs[0]
        parts.append(
            f"Highest-value change: {top['title']} in the {top['minute_window']} window "
            f"({top['expected_gain']:+.3f} {top['expected_gain_unit']})."
        )

    parts.append(
        f"Report built from {', '.join(out.inputs_used) or 'no inputs'}; "
        f"data completeness {out.data_completeness:.0%}, confidence {out.confidence:.0%}."
    )
    return " ".join(parts)
