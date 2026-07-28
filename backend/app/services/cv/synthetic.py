"""Deterministic match simulator.

Two jobs:

* **Demo and test fixture.** Every analytics path — possession, heatmaps,
  formation detection, tactical profiling — needs a match's worth of data to
  produce anything. This generates one without depending on a licensed feed.
* **Fallback for the video pipeline** when the CV extras are not installed. In
  that case the resulting job is labelled ``engine="simulated"`` end to end, and
  the API says so. It is never presented as measurement.

The simulation is seeded, so the same inputs always give the same match.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app.services.analytics.pitch import Pitch
from app.services.ml.lineup_optimizer import formation_anchors


@dataclass
class SimEvent:
    """Duck-typed to match :class:`~app.models.match.MatchEvent`."""

    period: int
    minute: int
    second: int
    type: str
    outcome: str
    x: float
    y: float
    end_x: float | None = None
    end_y: float | None = None
    is_own_team: bool = True
    player_id: str | None = None
    team_id: str | None = None
    qualifiers: dict = field(default_factory=dict)
    xg: float | None = None
    xt_delta: float | None = None


@dataclass
class SimFrame:
    """Duck-typed to match :class:`~app.models.match.TrackingFrame`."""

    period: int
    timestamp_ms: int
    home_positions: dict
    away_positions: dict
    ball: list | None
    possession_team: str | None


@dataclass
class SimulatedMatch:
    events: list[SimEvent]
    frames: list[SimFrame]
    home_player_ids: list[str]
    away_player_ids: list[str]
    home_formation: str
    away_formation: str
    meta: dict = field(default_factory=dict)


def _anchors(formation: str, attacking_right: bool, pitch: Pitch) -> list[tuple[float, float]]:
    pts = []
    for ax, ay in formation_anchors(formation, pitch.length, pitch.width):
        if not attacking_right:
            ax, ay = pitch.length - ax, pitch.width - ay
        pts.append((ax, ay))
    return pts


def simulate_match(
    *,
    home_player_ids: list[str] | None = None,
    away_player_ids: list[str] | None = None,
    home_formation: str = "4-3-3",
    away_formation: str = "4-4-2",
    minutes: int = 90,
    frame_hz: float = 5.0,
    seed: int = 20260728,
    #: 0-1. Higher gives the home side more of the ball and more territory.
    home_strength: float = 0.56,
    home_press_height: float = 0.62,
    pitch: Pitch | None = None,
) -> SimulatedMatch:
    """Run the simulation and return events plus tracking frames."""
    pitch = pitch or Pitch()
    rng = np.random.default_rng(seed)

    home_ids = home_player_ids or [f"H{i:02d}" for i in range(11)]
    away_ids = away_player_ids or [f"A{i:02d}" for i in range(11)]
    home_ids, away_ids = home_ids[:11], away_ids[:11]

    home_anchor = _anchors(home_formation, True, pitch)[: len(home_ids)]
    away_anchor = _anchors(away_formation, False, pitch)[: len(away_ids)]

    home_pos = np.array(home_anchor, dtype=float)
    away_pos = np.array(away_anchor, dtype=float)

    ball = np.array([pitch.length / 2, pitch.width / 2])
    possession = "home"
    holder = len(home_ids) // 2

    dt = 1.0 / frame_hz
    total_frames = int(minutes * 60 * frame_hz)

    events: list[SimEvent] = []
    frames: list[SimFrame] = []

    # Ticks between on-ball actions; a pass roughly every 2.6 s.
    next_action_in = int(frame_hz * 2.6)

    # A real block occupies 30-40 m front to back, not the whole pitch. The
    # anchors span ~87 m, so they are compressed around the ball rather than
    # used as absolute positions — without this the "team" is strung out over
    # the entire field and every compactness metric reads as broken.
    BLOCK_COMPRESSION = 0.46
    WIDTH_COMPRESSION = 1.00
    # The block centre follows the ball, the ball follows the carrier, and the
    # carrier is pushed forward by the block — an unclamped loop that walks the
    # whole team onto the goal line and produces 90 shots a side. Clamping the
    # block centre breaks the feedback.
    #
    # The bounds must be derived per team from that team's own anchor offsets:
    # the two sides' offsets are mirror images, so a single shared clamp
    # over-restricts one side and lets the other shoot from the six-yard box.
    OUTFIELD_MIN_X, OUTFIELD_MAX_X = 12.0, 93.0

    def _block_bounds(anchors: list[tuple[float, float]]) -> tuple[float, float]:
        offsets = [(ax - pitch.length / 2) * BLOCK_COMPRESSION for ax, _ in anchors[1:]]
        lo = OUTFIELD_MIN_X - min(offsets)
        hi = OUTFIELD_MAX_X - max(offsets)
        return (lo, hi) if lo <= hi else ((lo + hi) / 2, (lo + hi) / 2)

    home_bounds = _block_bounds(home_anchor)
    away_bounds = _block_bounds(away_anchor)

    for f in range(total_frames):
        t_s = f * dt
        period = 1 if t_s < minutes * 30 else 2
        minute = int(t_s // 60)
        second = int(t_s % 60)

        # --- Team shape follows the ball ----------------------------------
        for pos_arr, anchors, attacking_right, press, bounds in (
            (home_pos, home_anchor, True, home_press_height, home_bounds),
            (away_pos, away_anchor, False, 1.0 - home_press_height * 0.6, away_bounds),
        ):
            side_has_ball = (possession == "home") == attacking_right
            direction = 1.0 if attacking_right else -1.0
            # The block centres near the ball, pushed up in possession and
            # dropped off when defending.
            phase_bias = (9.0 if side_has_ball else -7.0) * direction
            press_bias = (press - 0.5) * 14.0 * direction
            block_centre_x = float(np.clip(
                ball[0] + phase_bias + press_bias, bounds[0], bounds[1]))
            lateral_pull = (ball[1] - pitch.width / 2) * 0.16

            for i, (ax, ay) in enumerate(anchors):
                if i == 0:
                    # The keeper holds his line; he does not slide with the block.
                    target = np.array([
                        6.0 if attacking_right else pitch.length - 6.0,
                        pitch.width / 2 + (ball[1] - pitch.width / 2) * 0.22,
                    ])
                else:
                    target = np.array([
                        block_centre_x + (ax - pitch.length / 2) * BLOCK_COMPRESSION,
                        pitch.width / 2 + (ay - pitch.width / 2) * WIDTH_COMPRESSION + lateral_pull,
                    ])
                delta = target - pos_arr[i]
                step = np.clip(delta * 0.07, -2.2, 2.2)
                pos_arr[i] += step + rng.normal(0, 0.26, 2)
                pos_arr[i][0] = np.clip(pos_arr[i][0], 1.5, pitch.length - 1.5)
                pos_arr[i][1] = np.clip(pos_arr[i][1], 1.0, pitch.width - 1.0)

        # Ball carrier drags the ball along.
        carrier = home_pos[holder] if possession == "home" else away_pos[holder]
        ball += (carrier - ball) * 0.45

        # --- On-ball action ------------------------------------------------
        next_action_in -= 1
        if next_action_in <= 0:
            own = possession == "home"
            mates = home_pos if own else away_pos
            ids = home_ids if own else away_ids
            attacking_right = own

            start = mates[holder].copy()
            # Event-frame x: always the acting team's attacking direction.
            ev_start = (start[0], start[1]) if attacking_right else (
                pitch.length - start[0], pitch.width - start[1]
            )

            skill = home_strength if own else 1 - home_strength

            # Shot selection by distance to the goal being attacked, not by a
            # flat "in the final third" rate — the flat version produces around
            # 70 shots a side, five times a real match.
            goal_dist = float(np.hypot(pitch.length - ev_start[0], pitch.width / 2 - ev_start[1]))
            shoot_p = 0.115 * math.exp(-(goal_dist - 9.0) / 9.0) if goal_dist < 32 else 0.0
            shoot_p = min(shoot_p, 0.22)

            if rng.random() < shoot_p:
                on_target = rng.random() < 0.35 + 0.2 * skill
                is_goal = on_target and rng.random() < 0.30
                events.append(SimEvent(
                    period=period, minute=minute, second=second,
                    type="shot",
                    outcome="goal" if is_goal else ("on_target" if on_target else "off_target"),
                    x=ev_start[0], y=ev_start[1],
                    is_own_team=own, player_id=ids[holder],
                    qualifiers={"situation": "open_play", "body_part": "foot"},
                ))
                # Possession turns over after a shot; the opposing keeper
                # restarts from his own area.
                possession = "away" if own else "home"
                holder = 0
                opp_pos = away_pos if own else home_pos
                ball = opp_pos[0].copy()
                next_action_in = int(frame_hz * (5.0 if is_goal else 3.0))
                continue

            # Pick a receiver: a sharp distance decay keeps the average pass
            # near 18 m rather than launching it half the length of the pitch.
            forward_axis = mates[:, 0] if attacking_right else pitch.length - mates[:, 0]
            weights = np.exp((forward_axis - forward_axis[holder]) / 18.0)
            dist = np.linalg.norm(mates - start, axis=1)
            weights *= np.exp(-dist / 17.0)
            weights[holder] = 0.0
            # Back to the keeper only from deep, and rarely.
            weights[0] *= 0.10 if forward_axis[holder] > 45 else 0.45
            if weights.sum() <= 0:
                weights = np.ones(len(mates))
                weights[holder] = 0.0
            weights /= weights.sum()
            receiver = int(rng.choice(len(mates), p=weights))

            end = mates[receiver].copy()
            ev_end = (end[0], end[1]) if attacking_right else (
                pitch.length - end[0], pitch.width - end[1]
            )

            # Completion odds fall with pass length and rise with team skill.
            length = float(np.linalg.norm(end - start))
            complete_p = np.clip(0.95 - length / 95.0 + (skill - 0.5) * 0.22, 0.35, 0.97)
            completed = rng.random() < complete_p

            events.append(SimEvent(
                period=period, minute=minute, second=second,
                type="pass", outcome="success" if completed else "incomplete",
                x=ev_start[0], y=ev_start[1], end_x=ev_end[0], end_y=ev_end[1],
                is_own_team=own, player_id=ids[holder],
                qualifiers={"length_m": round(length, 1)},
            ))

            if completed:
                holder = receiver
                ball = end.copy()
            else:
                # Turnover — the nearest opponent picks it up and registers a
                # defensive action, which is what PPDA is computed from.
                opp = away_pos if own else home_pos
                opp_ids = away_ids if own else home_ids
                idx = int(np.argmin(np.linalg.norm(opp - end, axis=1)))
                recovery_x = opp[idx][0], opp[idx][1]
                ev_rec = recovery_x if not attacking_right else (
                    pitch.length - recovery_x[0], pitch.width - recovery_x[1]
                )
                events.append(SimEvent(
                    period=period, minute=minute, second=second,
                    type=rng.choice(["interception", "tackle", "recovery"], p=[0.4, 0.25, 0.35]),
                    outcome="success",
                    x=ev_rec[0], y=ev_rec[1],
                    is_own_team=not own, player_id=opp_ids[idx],
                ))
                possession = "away" if own else "home"
                holder = idx
                ball = opp[idx].copy()

            next_action_in = max(1, int(frame_hz * rng.uniform(1.6, 3.8)))

        if f % max(1, int(frame_hz / 5)) == 0:
            frames.append(SimFrame(
                period=period,
                timestamp_ms=int(t_s * 1000),
                home_positions={pid: [round(float(p[0]), 2), round(float(p[1]), 2)]
                                for pid, p in zip(home_ids, home_pos)},
                away_positions={pid: [round(float(p[0]), 2), round(float(p[1]), 2)]
                                for pid, p in zip(away_ids, away_pos)},
                ball=[round(float(ball[0]), 2), round(float(ball[1]), 2)],
                possession_team=possession,
            ))

    return SimulatedMatch(
        events=events,
        frames=frames,
        home_player_ids=home_ids,
        away_player_ids=away_ids,
        home_formation=home_formation,
        away_formation=away_formation,
        meta={
            "seed": seed,
            "minutes": minutes,
            "frame_hz": frame_hz,
            "engine": "simulated",
            "home_strength": home_strength,
            "note": "Synthetic data — not a measurement of a real match.",
        },
    )
