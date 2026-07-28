"""Possession and territory metrics.

Possession is reported three ways because the three disagree, and the
disagreement is itself the insight:

* **Time possession** — share of match time in control. Needs tracking, or an
  event feed dense enough to interpolate.
* **Pass possession** — share of completed passes. The number broadcasters show.
* **Field tilt** — share of *final-third* touches. The best single proxy for
  territorial dominance, and the one that predicts results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.services.analytics.pitch import Pitch

#: Event types that constitute "having the ball".
ON_BALL_TYPES = {"pass", "carry", "dribble", "shot", "cross", "take_on", "reception"}
#: Defensive actions counted in the PPDA denominator.
DEFENSIVE_ACTION_TYPES = {"tackle", "interception", "pressure", "foul", "challenge"}


@dataclass
class PossessionResult:
    time_possession_pct: float | None = None
    pass_possession_pct: float | None = None
    field_tilt_pct: float | None = None
    #: Average length of an uninterrupted own-team sequence, in passes.
    avg_sequence_passes: float = 0.0
    #: Sequences reaching the final third, per 100 possessions.
    direct_speed_m_per_s: float | None = None
    #: Passes Per Defensive Action allowed in the opponent's 60% of the pitch.
    #: Lower = more aggressive pressing. ~8 is elite, ~14 is passive.
    ppda: float | None = None
    #: Opponent's PPDA against us — how hard *they* pressed.
    opponent_ppda: float | None = None
    touches_by_third: dict[str, int] = field(default_factory=dict)
    opponent_touches_by_third: dict[str, int] = field(default_factory=dict)
    #: Rolling 5-minute possession share, for the timeline chart.
    timeline: list[dict] = field(default_factory=list)
    method: str = "events"

    def to_dict(self) -> dict:
        return asdict(self)


def _round(v: float | None, n: int = 1) -> float | None:
    return None if v is None else round(float(v), n)


def possession_from_events(
    events: list,
    *,
    pitch: Pitch | None = None,
    bucket_minutes: int = 5,
) -> PossessionResult:
    """Compute possession metrics from an event stream.

    ``events`` are objects with ``type``, ``outcome``, ``is_own_team``, ``x``,
    ``y``, ``minute``, ``second`` — i.e. :class:`~app.models.match.MatchEvent`
    rows, or anything duck-typed the same.
    """
    pitch = pitch or Pitch()
    res = PossessionResult(method="events")
    if not events:
        return res

    events = sorted(events, key=lambda e: (e.period, e.minute, e.second))

    own_passes = opp_passes = 0
    own_touches = opp_touches = 0
    thirds = {"defensive": 0, "middle": 0, "attacking": 0}
    opp_thirds = {"defensive": 0, "middle": 0, "attacking": 0}
    own_final_third = opp_final_third = 0

    # PPDA counters.
    own_def_actions_high = 0   # our defensive actions in the opponent's 60%
    opp_passes_in_own_60 = 0   # their passes in that same area
    opp_def_actions_high = 0
    own_passes_in_own_60 = 0

    # Sequence tracking.
    sequences: list[int] = []
    current_team: bool | None = None
    current_passes = 0

    # Time possession: attribute the gap between consecutive on-ball events to
    # whoever held the ball. Gaps over 12 s are dead-ball time and dropped.
    own_time = opp_time = 0.0
    last_t: float | None = None
    last_team: bool | None = None

    buckets: dict[int, list[int]] = {}

    for ev in events:
        t = ev.minute * 60 + ev.second
        etype = (ev.type or "").lower()
        own = bool(ev.is_own_team)
        x, y = pitch.clip(ev.x or 0.0, ev.y or 0.0)
        # Convention: `x` is always in the *acting* team's attacking frame, so
        # `third_of(x)` is that team's own third. `own_frame_x` mirrors the
        # opponent onto our frame, which is what the pressing zones need.
        own_frame_x = x if own else pitch.length - x

        if etype in ON_BALL_TYPES:
            third = pitch.third_of(x)
            if own:
                own_touches += 1
                thirds[third] += 1
                if third == "attacking":
                    own_final_third += 1
            else:
                opp_touches += 1
                opp_thirds[third] += 1
                if third == "attacking":
                    opp_final_third += 1

            b = ev.minute // bucket_minutes
            buckets.setdefault(b, [0, 0])
            buckets[b][0 if own else 1] += 1

            if last_t is not None and last_team is not None:
                gap = t - last_t
                if 0 <= gap <= 12:
                    if last_team:
                        own_time += gap
                    else:
                        opp_time += gap
            last_t, last_team = t, own

        # PPDA zones, both expressed in our frame:
        #   our pressing zone   = own_frame_x >= 0.4L (the opponent's own 60%)
        #   their pressing zone = own_frame_x <= 0.6L (our own 60%)
        in_our_press_zone = own_frame_x >= pitch.length * 0.4
        in_their_press_zone = own_frame_x <= pitch.length * 0.6

        if etype == "pass":
            completed = (ev.outcome or "success").lower() in {"success", "complete", "completed"}
            if own:
                if completed:
                    own_passes += 1
                if in_their_press_zone:
                    own_passes_in_own_60 += 1
            else:
                if completed:
                    opp_passes += 1
                if in_our_press_zone:
                    opp_passes_in_own_60 += 1

        if etype in DEFENSIVE_ACTION_TYPES:
            if own and in_our_press_zone:
                own_def_actions_high += 1
            elif not own and in_their_press_zone:
                opp_def_actions_high += 1

        # Sequences.
        if etype in ON_BALL_TYPES:
            if current_team is None:
                current_team, current_passes = own, 0
            elif own != current_team:
                sequences.append(current_passes)
                current_team, current_passes = own, 0
            if etype == "pass" and own == current_team:
                current_passes += 1
    if current_team is not None:
        sequences.append(current_passes)

    total_passes = own_passes + opp_passes
    if total_passes:
        res.pass_possession_pct = _round(100 * own_passes / total_passes)

    total_final = own_final_third + opp_final_third
    if total_final:
        res.field_tilt_pct = _round(100 * own_final_third / total_final)

    if own_time + opp_time > 0:
        res.time_possession_pct = _round(100 * own_time / (own_time + opp_time))

    if sequences:
        res.avg_sequence_passes = round(sum(sequences) / len(sequences), 2)

    if own_def_actions_high:
        res.ppda = _round(opp_passes_in_own_60 / own_def_actions_high, 2)
    if opp_def_actions_high:
        res.opponent_ppda = _round(own_passes_in_own_60 / opp_def_actions_high, 2)

    res.touches_by_third = thirds
    res.opponent_touches_by_third = opp_thirds

    res.timeline = [
        {
            "from_minute": b * bucket_minutes,
            "to_minute": (b + 1) * bucket_minutes,
            "possession_pct": round(100 * o / (o + p), 1) if (o + p) else 50.0,
            "touches": o + p,
        }
        for b, (o, p) in sorted(buckets.items())
    ]
    return res


def possession_from_tracking(frames: list, *, pitch: Pitch | None = None) -> PossessionResult:
    """Time possession straight from tracking frames.

    Uses the feed's ``possession_team`` when present; otherwise falls back to
    "closest player to the ball, held for at least 3 consecutive frames" so a
    ball flying past a defender does not flip control.
    """
    pitch = pitch or Pitch()
    res = PossessionResult(method="tracking")
    if not frames:
        return res

    frames = sorted(frames, key=lambda f: (f.period, f.timestamp_ms))

    labels: list[str | None] = []
    for f in frames:
        if f.possession_team in {"home", "away"}:
            labels.append(f.possession_team)
            continue
        if not f.ball:
            labels.append(None)
            continue
        bx, by = f.ball[0], f.ball[1]
        best, best_d = None, 1e9
        for side, positions in (("home", f.home_positions or {}), ("away", f.away_positions or {})):
            for pos in positions.values():
                d = (pos[0] - bx) ** 2 + (pos[1] - by) ** 2
                if d < best_d:
                    best, best_d = side, d
        labels.append(best if best_d <= 25.0 else None)  # within 5 m

    # Debounce: require 3 consecutive frames before switching.
    smoothed: list[str | None] = []
    run_label, run_len = None, 0
    current = None
    for lab in labels:
        if lab == run_label:
            run_len += 1
        else:
            run_label, run_len = lab, 1
        if run_len >= 3 and run_label is not None:
            current = run_label
        smoothed.append(current)

    home = sum(1 for s in smoothed if s == "home")
    away = sum(1 for s in smoothed if s == "away")
    if home + away:
        res.time_possession_pct = _round(100 * home / (home + away))

    own_final = opp_final = 0
    for f, lab in zip(frames, smoothed):
        if not f.ball or lab is None:
            continue
        bx = f.ball[0]
        if lab == "home" and bx >= 2 * pitch.length / 3:
            own_final += 1
        elif lab == "away" and bx <= pitch.length / 3:
            opp_final += 1
    if own_final + opp_final:
        res.field_tilt_pct = _round(100 * own_final / (own_final + opp_final))

    # Direct speed: metres of forward progress per second of possession.
    total_gain, total_time = 0.0, 0.0
    prev = None
    for f, lab in zip(frames, smoothed):
        if lab == "home" and f.ball:
            if prev is not None:
                dt = (f.timestamp_ms - prev[0]) / 1000.0
                if 0 < dt <= 1.0:
                    total_gain += f.ball[0] - prev[1]
                    total_time += dt
            prev = (f.timestamp_ms, f.ball[0])
        else:
            prev = None
    if total_time > 0:
        res.direct_speed_m_per_s = _round(total_gain / total_time, 2)

    return res


def merge(primary: PossessionResult, secondary: PossessionResult) -> PossessionResult:
    """Overlay two results, preferring non-null values from ``primary``."""
    out = PossessionResult(method=f"{primary.method}+{secondary.method}")
    for key in primary.__dataclass_fields__:
        if key == "method":
            continue
        pv = getattr(primary, key)
        sv = getattr(secondary, key)
        empty = pv is None or (isinstance(pv, (dict, list)) and not pv) or pv == 0.0
        setattr(out, key, sv if empty else pv)
    return out
