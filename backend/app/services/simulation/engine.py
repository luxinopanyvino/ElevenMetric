"""Match engine.

Plays a fixture between two real squads. Unlike the fixture generator it grew
out of, every outcome here is driven by the players actually on the pitch: a
pass completes according to the passer's passing attributes and the pressure
from the nearest opponent, a shot converts according to the striker's finishing,
and everything degrades as the match wears on according to each player's own
fatigue curve.

The output is designed to be *watched*: positions are sampled for playback,
per-player condition is tracked minute by minute, and the whole thing can be
persisted as an ordinary match so the analysis pipeline consumes it like any
other fixture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from app.models.catalog import Position
from app.services.analytics.metrics import expected_goals
from app.services.analytics.pitch import Pitch
from app.services.ml.features import attribute, fatigue_state, position_fit
from app.services.ml.lineup_optimizer import FORMATION_SLOTS, formation_anchors

#: How the block behaves. Derived in the fixture generator this grew from; see
#: docs/MODELS.md for why each of these is clamped the way it is.
BLOCK_COMPRESSION = 0.46
WIDTH_COMPRESSION = 1.00
OUTFIELD_MIN_X, OUTFIELD_MAX_X = 12.0, 86.0

#: A side spreads out when it has the ball and squeezes up when it does not.
#: `BLOCK_COMPRESSION` alone models a defending block and applied it in both
#: phases, which left the whole attack sitting too deep: only the centre-forward
#: was ever within shooting range, so he took every shot in the match.
IN_POSSESSION_STRETCH = 1.15

#: Metres the whole block shifts toward the goal it is attacking (on the ball) or
#: away from it (off the ball), relative to the ball.
PHASE_BIAS_ON_BALL = 12.0
PHASE_BIAS_OFF_BALL = -7.0

#: How far the block slides across with the ball, as a share of the ball's own
#: offset from the centre line. A side that never shifts laterally covers about
#: 7 km a man; the real figure for an outfielder is 10-12.
BLOCK_LATERAL_SHIFT = 0.45

#: Attacking runs into the box: how far a full-appetite role breaks forward, how
#: much of its width it gives up doing so, and how close the ball has to be to
#: the goal before anyone sets off.
BOX_ARRIVAL_M = 22.0
BOX_ARRIVAL_INFIELD = 0.55
BOX_ARRIVAL_TRIGGER_M = 32.0

#: Match minutes between condition recalculations. Recomputing the fatigue curve
#: every tick costs more than it tells us — condition moves on the scale of
#: minutes, not tenths of a second.
CONDITION_INTERVAL_S = 30.0

#: Positional noise per tick, in metres. Small on purpose: at 5 Hz over 90
#: minutes even a modest jitter integrates into kilometres of phantom running.
JITTER_M = 0.12

#: Minutes at which the substitution engine is consulted when auto-subs are on.
SUB_WINDOWS = (46, 61, 71, 81)

#: Seconds between on-ball actions. A side makes roughly 450-550 passes in a
#: match; at a 2.7 s mean the engine produced twice that, and the per-player
#: touch counts read as nonsense next to a real match report.
ACTION_INTERVAL_S = (3.4, 7.2)

#: How readily each role shoots when it is on. Block compression squeezes the
#: whole side into a ~48 m band, which left the centre-forward as the only player
#: ever inside a fixed 32 m gate — so he took every shot in the match. Gating on
#: role as well as distance restores a normal spread of shooters without
#: loosening the shape that made the movement realistic in the first place.
SHOT_APPETITE = {
    Position.ST: 1.00, Position.CF: 1.00, Position.SS: 1.00,
    Position.LW: 0.72, Position.RW: 0.72,
    Position.AM: 0.80, Position.LM: 0.48, Position.RM: 0.48,
    Position.CM: 0.42, Position.DM: 0.22,
    Position.LB: 0.10, Position.RB: 0.10, Position.LWB: 0.12, Position.RWB: 0.12,
    Position.CB: 0.06, Position.LCB: 0.06, Position.RCB: 0.06,
    Position.GK: 0.0,
}
SHOT_RANGE_M = 38.0
SHOT_BASE_P = 0.115
SHOT_DECAY_M = 30.0

#: How far a carrier drives before striking the ball, as a share of the distance
#: left to goal and in absolute metres, and how much of his lateral offset he
#: gives up on the way in.
CARRY_FRACTION = 0.17
CARRY_MAX_M = 10.0
CARRY_CENTRING = 0.35

#: Pass selection. The forward preference is what makes a side progress; it is
#: bounded (a tanh, not an exponential) because metres gained have diminishing
#: returns — the tenth metre forward is worth far more than the fortieth. The
#: distance decay stops every ball going long, and the marking penalty stops the
#: most closely guarded player being the most passed-to.
PASS_FORWARD_GAIN = 0.85
PASS_FORWARD_SCALE_M = 14.0
PASS_DISTANCE_DECAY_M = 11.0
MARKING_PENALTY = 0.85


@dataclass
class SimPlayer:
    """A player as the engine sees them, with their running match state."""

    id: str
    name: str
    position: Position
    rating: float
    attributes: dict
    age: float | None = None
    start_fatigue: float = 0.0
    minutes_last_7d: int = 0

    # --- Runtime -----------------------------------------------------------
    on_pitch: bool = True
    minutes_played: float = 0.0
    condition: float = 1.0          # performance multiplier, 0.55-1.0
    fatigue: float = 0.0            # 0-100
    injury_hazard: float = 0.0
    distance_m: float = 0.0
    touches: int = 0
    passes: int = 0
    passes_completed: int = 0
    shots: int = 0
    goals: int = 0
    came_on_at: int | None = None
    came_off_at: int | None = None

    @property
    def stamina(self) -> float:
        return attribute(self, "stamina", self.rating)

    @property
    def overall_rating(self) -> float:      # for `attribute` and `position_fit`
        return self.rating

    @property
    def primary_position(self) -> Position:
        return self.position

    @property
    def secondary_positions(self) -> list:
        return []

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "position": self.position.value,
            "rating": round(self.rating, 1), "on_pitch": self.on_pitch,
            "minutes_played": round(self.minutes_played, 1),
            "condition": round(self.condition, 3),
            "fatigue": round(self.fatigue, 1),
            "injury_hazard": round(self.injury_hazard, 4),
            "distance_km": round(self.distance_m / 1000, 2),
            "touches": self.touches, "passes": self.passes,
            "passes_completed": self.passes_completed,
            "pass_accuracy_pct": (
                round(100 * self.passes_completed / self.passes, 1) if self.passes else None
            ),
            "shots": self.shots, "goals": self.goals,
            "came_on_at": self.came_on_at, "came_off_at": self.came_off_at,
        }


@dataclass
class TeamSetup:
    name: str
    formation: str
    starters: list[SimPlayer]
    bench: list[SimPlayer] = field(default_factory=list)
    team_id: str | None = None
    colour: str = "#2a78d6"
    #: 0-1; how high the side presses.
    press_height: float = 0.55

    def all_players(self) -> list[SimPlayer]:
        """Everyone in the squad, each exactly once.

        Both lists describe kick-off and are never re-shuffled by substitutions,
        so they are already disjoint; the de-dupe is a guard, not the mechanism.
        """
        seen: set[str] = set()
        out: list[SimPlayer] = []
        for p in self.starters + self.bench:
            if p.id not in seen:
                seen.add(p.id)
                out.append(p)
        return out


@dataclass
class SimEvent:
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

    def to_dict(self) -> dict:
        return {
            "period": self.period, "minute": self.minute, "second": self.second,
            "type": self.type, "outcome": self.outcome,
            "is_own_team": self.is_own_team, "player_id": self.player_id,
            "x": round(self.x, 1), "y": round(self.y, 1),
            "end_x": round(self.end_x, 1) if self.end_x is not None else None,
            "end_y": round(self.end_y, 1) if self.end_y is not None else None,
            "xg": round(self.xg, 3) if self.xg is not None else None,
            "qualifiers": self.qualifiers,
        }


@dataclass
class SimFrame:
    period: int
    timestamp_ms: int
    home_positions: dict
    away_positions: dict
    ball: list | None
    possession_team: str | None


@dataclass
class SimResult:
    home: TeamSetup
    away: TeamSetup
    events: list[SimEvent]
    frames: list[SimFrame]
    #: Compact positional stream for playback, see :meth:`playback`.
    playback_frames: list[list]
    playback_roster: list[dict]
    playback_hz: float
    score: tuple[int, int]
    xg: tuple[float, float]
    shots: tuple[int, int]
    possession_pct: float
    #: Per-player condition every five minutes: {player_id: [{minute, condition}]}
    condition_timeline: dict[str, list[dict]]
    substitutions: list[dict]
    minutes: int
    seed: int

    def summary(self) -> dict:
        return {
            "home": {"name": self.home.name, "formation": self.home.formation,
                     "colour": self.home.colour, "team_id": self.home.team_id},
            "away": {"name": self.away.name, "formation": self.away.formation,
                     "colour": self.away.colour, "team_id": self.away.team_id},
            "score": list(self.score),
            "xg": [round(self.xg[0], 2), round(self.xg[1], 2)],
            "shots": list(self.shots),
            "possession_pct": round(self.possession_pct, 1),
            "minutes": self.minutes,
            "seed": self.seed,
            "substitutions": self.substitutions,
            "players": {
                "home": [p.to_dict() for p in self.home.all_players()],
                "away": [p.to_dict() for p in self.away.all_players()],
            },
            "condition_timeline": self.condition_timeline,
            "goals": [e.to_dict() for e in self.events
                      if e.type == "shot" and e.outcome == "goal"],
        }

    def playback(self) -> dict:
        """Positions as a compact stream.

        One flat array per frame — ``[clock_seconds, x0, y0, x1, y1, …, bx, by]``
        against a fixed roster order — rather than an object per player per
        frame. A 90-minute match is thousands of frames; the object form is
        roughly six times larger over the wire for exactly the same numbers.
        """
        return {
            "hz": self.playback_hz,
            "roster": self.playback_roster,
            "frames": self.playback_frames,
            "minutes": self.minutes,
            "scale": 2,
            "layout": "[clock_s, home x/y x11, away x/y x11, ball x, ball y, "
                      "possession(0=home)] — coordinates are half-metres",
        }


# --- Engine ----------------------------------------------------------------

def _anchors(formation: str, attacking_right: bool, pitch: Pitch) -> list[tuple[float, float]]:
    out = []
    for ax, ay in formation_anchors(formation, pitch.length, pitch.width):
        if not attacking_right:
            ax, ay = pitch.length - ax, pitch.width - ay
        out.append((ax, ay))
    return out


def _outfield_band(attacking_right: bool, pitch: Pitch) -> tuple[float, float]:
    """The x-range an outfielder may occupy, for a side attacking this way.

    `OUTFIELD_MIN_X` / `MAX_X` are written for a side attacking to the right, so
    the other side needs them mirrored. Applying the same absolute band to both
    let the away side attack seven metres closer to goal than the home side —
    which showed up as a 19-7 shot count between evenly matched teams.
    """
    if attacking_right:
        return OUTFIELD_MIN_X, OUTFIELD_MAX_X
    return pitch.length - OUTFIELD_MAX_X, pitch.length - OUTFIELD_MIN_X


def _block_bounds(anchors: list[tuple[float, float]], pitch: Pitch,
                  attacking_right: bool) -> tuple[float, float]:
    offsets = [(ax - pitch.length / 2) * BLOCK_COMPRESSION * IN_POSSESSION_STRETCH
               for ax, _ in anchors[1:]]
    low, high = _outfield_band(attacking_right, pitch)
    lo = low - min(offsets)
    hi = high - max(offsets)
    return (lo, hi) if lo <= hi else ((lo + hi) / 2, (lo + hi) / 2)


def _pass_skill(player: SimPlayer, length: float) -> float:
    """0-1 passing quality for a pass of this length, before pressure."""
    key = "short_passing" if length < 25 else "long_passing"
    return attribute(player, key, player.rating) / 100.0


def _finishing_skill(player: SimPlayer) -> float:
    return attribute(player, "finishing", player.rating) / 100.0


def simulate(
    home: TeamSetup,
    away: TeamSetup,
    *,
    minutes: int = 90,
    seed: int = 20260728,
    tick_hz: float = 5.0,
    playback_hz: float = 0.5,
    auto_subs: bool = True,
    pitch: Pitch | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> SimResult:
    """Play the fixture.

    ``tick_hz`` is the physics rate; ``playback_hz`` is what gets shipped to the
    client to animate. They are separate because the engine needs fine steps to
    move players smoothly while the browser only needs enough frames to
    interpolate between.
    """
    pitch = pitch or Pitch()
    rng = np.random.default_rng(seed)

    sides = {"home": home, "away": away}
    anchors = {
        "home": _anchors(home.formation, True, pitch)[: len(home.starters)],
        "away": _anchors(away.formation, False, pitch)[: len(away.starters)],
    }
    bounds = {k: _block_bounds(anchors[k], pitch, k == "home") for k in sides}
    band = {k: _outfield_band(k == "home", pitch) for k in sides}
    positions = {k: np.array(anchors[k], dtype=float) for k in sides}
    #: Which SimPlayer occupies each slot; substitutions swap these in place.
    slots: dict[str, list[SimPlayer]] = {
        "home": list(home.starters), "away": list(away.starters),
    }

    # Anchor offsets from the block centre, precomputed once.
    anchor_off = {
        side: np.array([[(ax - pitch.length / 2) * BLOCK_COMPRESSION,
                         (ay - pitch.width / 2) * WIDTH_COMPRESSION]
                        for ax, ay in anchors[side]])
        for side in sides
    }
    #: How hard each slot breaks for the box when the ball gets there. Keyed on
    #: the *slot's* role from the formation, not the occupant's, so a substitution
    #: does not change how the shape behaves.
    arrival = {
        side: np.array([
            0.0 if i == 0 else SHOT_APPETITE.get(pos, 0.4)
            for i, pos in enumerate(FORMATION_SLOTS.get(
                setup.formation, FORMATION_SLOTS["4-3-3"])[: len(setup.starters)])
        ])
        for side, setup in sides.items()
    }
    condition = {side: np.ones(len(anchors[side])) for side in sides}
    distance = {side: np.zeros(len(anchors[side])) for side in sides}

    ball = np.array([pitch.length / 2, pitch.width / 2])
    possession = "home"
    holder = len(home.starters) // 2

    dt = 1.0 / tick_hz
    total_ticks = int(minutes * 60 * tick_hz)
    playback_every = max(1, int(round(tick_hz / playback_hz)))

    events: list[SimEvent] = []
    frames: list[SimFrame] = []
    playback_frames: list[list] = []
    condition_timeline: dict[str, list[dict]] = {}
    substitutions: list[dict] = []

    possession_ticks = {"home": 0, "away": 0}
    next_action_in = int(tick_hz * 2.6)
    next_condition_at = 0.0
    next_timeline_minute = 0
    subs_used = {"home": 0, "away": 0}
    sub_windows_done: set[int] = set()

    for tick in range(total_ticks):
        t_s = tick * dt
        minute = int(t_s // 60)
        second = int(t_s % 60)
        period = 1 if t_s < minutes * 30 else 2
        possession_ticks[possession] += 1

        # --- Condition ------------------------------------------------------
        if t_s >= next_condition_at:
            next_condition_at = t_s + CONDITION_INTERVAL_S
            for side in sides:
                for p in slots[side]:
                    p.minutes_played = max(p.minutes_played, minute - (p.came_on_at or 0))
                    fs = fatigue_state(
                        minutes_played=int(p.minutes_played),
                        age=p.age,
                        stamina=p.stamina,
                        minutes_last_7d=p.minutes_last_7d,
                        baseline_fatigue=p.start_fatigue,
                    )
                    p.condition = fs.performance_multiplier
                    p.fatigue = fs.fatigue_index
                    p.injury_hazard = fs.injury_hazard
                condition[side] = np.array([p.condition for p in slots[side]])

        if minute >= next_timeline_minute:
            next_timeline_minute += 5
            for side in sides:
                for p in slots[side]:
                    condition_timeline.setdefault(p.id, []).append(
                        {"minute": minute, "condition": round(p.condition, 3),
                         "fatigue": round(p.fatigue, 1)})

        # --- Shape ----------------------------------------------------------
        for side, setup in sides.items():
            attacking_right = side == "home"
            direction = 1.0 if attacking_right else -1.0
            has_ball = possession == side
            phase_bias = (PHASE_BIAS_ON_BALL if has_ball
                          else PHASE_BIAS_OFF_BALL) * direction
            press_bias = (setup.press_height - 0.5) * 14.0 * direction
            centre = float(np.clip(ball[0] + phase_bias + press_bias, *bounds[side]))
            lateral = (ball[1] - pitch.width / 2) * BLOCK_LATERAL_SHIFT

            arr = positions[side]
            targets = np.empty_like(arr)
            stretch = IN_POSSESSION_STRETCH if has_ball else 1.0
            targets[:, 0] = centre + anchor_off[side][:, 0] * stretch
            targets[:, 1] = pitch.width / 2 + anchor_off[side][:, 1] + lateral

            # Attacking runs. When the ball reaches the final third the side's
            # forward players break for the box, hardest for the roles that shoot
            # most. Without this the shape is static and the centre-forward is the
            # only man ever near goal — he took three quarters of his side's
            # shots, because the wingers and midfielders simply never arrived.
            if has_ball:
                goal_x = pitch.length if attacking_right else 0.0
                to_goal = abs(goal_x - ball[0])
                if to_goal < BOX_ARRIVAL_TRIGGER_M:
                    urgency = 1.0 - to_goal / BOX_ARRIVAL_TRIGGER_M
                    pull = arrival[side] * urgency
                    targets[:, 0] += pull * BOX_ARRIVAL_M * direction
                    targets[:, 1] += (pitch.width / 2 - targets[:, 1]) * (
                        pull * BOX_ARRIVAL_INFIELD)
                    # The run is bounded by the same band as the block. Left
                    # unclamped it stacked on top of the block's own advance and
                    # parked the centre-forward eight metres from goal, where the
                    # xG of any shot he took was three times a real one's.
                    np.clip(targets[1:, 0], *band[side], out=targets[1:, 0])

            # The keeper holds his line rather than sliding with the block.
            targets[0] = (6.0 if attacking_right else pitch.length - 6.0,
                          pitch.width / 2 + (ball[1] - pitch.width / 2) * 0.22)

            # A tired player closes down more slowly — the whole point of
            # modelling condition.
            speed = (0.055 * (0.55 + 0.45 * condition[side]))[:, None]
            step = np.clip((targets - arr) * speed, -2.2, 2.2)
            arr += step + rng.normal(0, JITTER_M, arr.shape)
            np.clip(arr[:, 0], 1.5, pitch.length - 1.5, out=arr[:, 0])
            np.clip(arr[:, 1], 1.0, pitch.width - 1.0, out=arr[:, 1])
            # Distance covered counts the deliberate movement only. Including
            # the positional jitter adds ~10 km of pure noise over 90 minutes
            # and turns every keeper into a marathon runner.
            distance[side] += np.linalg.norm(step, axis=1)

        carrier = positions[possession][holder]
        ball += (carrier - ball) * 0.45

        # --- Action ---------------------------------------------------------
        next_action_in -= 1
        if next_action_in <= 0:
            own = possession == "home"
            mates = positions[possession]
            squad = slots[possession]
            opp_side = "away" if own else "home"
            opp = positions[opp_side]
            passer = squad[holder]
            passer.touches += 1

            start = mates[holder].copy()
            ev_start = (start[0], start[1]) if own else (
                pitch.length - start[0], pitch.width - start[1])

            goal_dist = float(np.hypot(pitch.length - ev_start[0],
                                       pitch.width / 2 - ev_start[1]))
            # Nobody shoots from where the shape parks them: they carry the ball
            # in first. Modelling the carry is what lets a winger whose average
            # position is 36 m out shoot from 22 m, and it keeps the recorded
            # location — and therefore the xG — honest about where the attempt
            # was actually struck.
            carry = min(CARRY_MAX_M, goal_dist * CARRY_FRACTION) * (
                0.45 + 0.55 * attribute(passer, "dribbling", passer.rating) / 100.0
            ) * passer.condition
            shot_dist = max(4.0, goal_dist - carry)

            appetite = SHOT_APPETITE.get(passer.position, 0.35)
            shoot_p = (SHOT_BASE_P * appetite
                       * math.exp(-(shot_dist - 9.0) / SHOT_DECAY_M)
                       if shot_dist < SHOT_RANGE_M else 0.0)
            shoot_p = min(shoot_p, 0.45)

            if rng.random() < shoot_p:
                # Move the attempt `carry` metres nearer goal. Interpolating
                # straight at the centre of the goal put every shot dead central,
                # where xG is at its highest — which is why the engine reported
                # nearly twice the xG per shot a real match produces. A carrier
                # drifts infield, he does not teleport onto the penalty spot.
                lam = carry / goal_dist if goal_dist > 0 else 0.0
                ev_start = (
                    ev_start[0] + lam * (pitch.length - ev_start[0]),
                    ev_start[1] + lam * CARRY_CENTRING * (pitch.width / 2 - ev_start[1]),
                )
                xg = expected_goals(*pitch.clip(*ev_start), pitch=pitch)
                # Conversion is anchored on xG, so over many matches goals and
                # xG agree — which is the whole basis on which the analysis
                # pipeline is allowed to read one against the other. Finishing
                # shifts a shot either side of the model; it does not replace it.
                # An average finisher (0.75) lands exactly on xG.
                quality = _finishing_skill(passer) * passer.condition
                is_goal = rng.random() < min(0.92, xg * (0.40 + 0.80 * quality))
                on_target = is_goal or rng.random() < 0.34 + 0.30 * quality
                passer.shots += 1
                if is_goal:
                    passer.goals += 1
                events.append(SimEvent(
                    period=period, minute=minute, second=second, type="shot",
                    outcome="goal" if is_goal else ("on_target" if on_target else "off_target"),
                    x=ev_start[0], y=ev_start[1], is_own_team=own,
                    player_id=passer.id, xg=xg,
                    qualifiers={"situation": "open_play", "body_part": "foot"},
                ))
                possession = opp_side
                holder = 0
                ball = positions[possession][0].copy()
                next_action_in = int(tick_hz * (5.0 if is_goal else 3.0))
                continue

            forward = mates[:, 0] if own else pitch.length - mates[:, 0]
            # Progression is incremental. An exponential in "metres gained" made
            # a 40 m ball to the centre-forward twenty times likelier than a 10 m
            # one to a midfielder, so a fifth of the side's touches — and nearly
            # all of its shots — went through one player. A bounded preference
            # keeps the side playing forward without turning every possession
            # into a hopeful long ball.
            weights = np.clip(
                1.0 + PASS_FORWARD_GAIN * np.tanh(
                    (forward - forward[holder]) / PASS_FORWARD_SCALE_M),
                0.08, None)
            dist = np.linalg.norm(mates - start, axis=1)
            weights *= np.exp(-dist / PASS_DISTANCE_DECAY_M)
            # Marking. A receiver with an opponent on him is a worse option, and
            # the centre-forward is the most tightly marked player on the pitch.
            # Without this term the forward bias made him the most passed-to man
            # in the side, with a fifth of its touches — about double a real one.
            opp_gap = np.min(
                np.linalg.norm(mates[:, None, :] - opp[None, :, :], axis=2), axis=1)
            weights *= 1.0 - MARKING_PENALTY * np.exp(-opp_gap / 7.0)
            weights[holder] = 0.0
            weights[0] *= 0.10 if forward[holder] > 45 else 0.45
            if weights.sum() <= 0:
                weights = np.ones(len(mates))
                weights[holder] = 0.0
            weights /= weights.sum()
            receiver = int(rng.choice(len(mates), p=weights))

            end = mates[receiver].copy()
            ev_end = (end[0], end[1]) if own else (
                pitch.length - end[0], pitch.width - end[1])
            length = float(np.linalg.norm(end - start))

            # Pressure from the closest opponent, weighted by their defending.
            nearest = int(np.argmin(np.linalg.norm(opp - start, axis=1)))
            gap = float(np.linalg.norm(opp[nearest] - start))
            presser = slots[opp_side][nearest]
            pressure = math.exp(-gap / 6.0) * (
                attribute(presser, "defensive_awareness", presser.rating) / 100.0
            ) * presser.condition

            skill = _pass_skill(passer, length) * passer.condition
            complete_p = float(np.clip(
                0.80 + 0.26 * skill - length / 160.0 - 0.20 * pressure, 0.25, 0.98))
            completed = rng.random() < complete_p

            passer.passes += 1
            if completed:
                passer.passes_completed += 1

            events.append(SimEvent(
                period=period, minute=minute, second=second, type="pass",
                outcome="success" if completed else "incomplete",
                x=ev_start[0], y=ev_start[1], end_x=ev_end[0], end_y=ev_end[1],
                is_own_team=own, player_id=passer.id,
                qualifiers={"length_m": round(length, 1)},
            ))

            if completed:
                holder = receiver
                ball = end.copy()
            else:
                idx = int(np.argmin(np.linalg.norm(opp - end, axis=1)))
                winner = slots[opp_side][idx]
                winner.touches += 1
                rec = opp[idx]
                ev_rec = (rec[0], rec[1]) if not own else (
                    pitch.length - rec[0], pitch.width - rec[1])
                events.append(SimEvent(
                    period=period, minute=minute, second=second,
                    type=str(rng.choice(["interception", "tackle", "recovery"],
                                        p=[0.4, 0.25, 0.35])),
                    outcome="success", x=ev_rec[0], y=ev_rec[1],
                    is_own_team=not own, player_id=winner.id,
                ))
                possession = opp_side
                holder = idx
                ball = rec.copy()

            next_action_in = max(1, int(tick_hz * rng.uniform(*ACTION_INTERVAL_S)))

        # --- Substitutions ---------------------------------------------------
        if auto_subs and minute in SUB_WINDOWS and minute not in sub_windows_done:
            sub_windows_done.add(minute)
            for side, setup in sides.items():
                made = _maybe_substitute(setup, slots[side], distance[side], minute,
                                         subs_used[side])
                if made:
                    subs_used[side] += 1
                    substitutions.append({"side": side, "minute": minute, **made})

        # --- Recording -------------------------------------------------------
        if tick % playback_every == 0:
            home_pos = {slots["home"][i].id: [round(float(v[0]), 1), round(float(v[1]), 1)]
                        for i, v in enumerate(positions["home"])}
            away_pos = {slots["away"][i].id: [round(float(v[0]), 1), round(float(v[1]), 1)]
                        for i, v in enumerate(positions["away"])}
            frames.append(SimFrame(
                period=period, timestamp_ms=int(t_s * 1000),
                home_positions=home_pos, away_positions=away_pos,
                ball=[round(float(ball[0]), 1), round(float(ball[1]), 1)],
                possession_team=possession,
            ))
            flat: list[int] = [int(t_s)]
            for side in ("home", "away"):
                flat.extend((positions[side] * 2).round().astype(int).ravel().tolist())
            flat.append(int(round(ball[0] * 2)))
            flat.append(int(round(ball[1] * 2)))
            flat.append(0 if possession == "home" else 1)
            playback_frames.append(flat)

        if on_progress and tick % (total_ticks // 20 or 1) == 0:
            on_progress(tick / total_ticks)

    # --- Wrap up ------------------------------------------------------------
    for side in sides:
        for i, p in enumerate(slots[side]):
            p.minutes_played = minutes - (p.came_on_at or 0)
            p.distance_m += float(distance[side][i])

    home_goals = sum(1 for e in events if e.type == "shot" and e.outcome == "goal" and e.is_own_team)
    away_goals = sum(1 for e in events if e.type == "shot" and e.outcome == "goal" and not e.is_own_team)
    home_shots = sum(1 for e in events if e.type == "shot" and e.is_own_team)
    away_shots = sum(1 for e in events if e.type == "shot" and not e.is_own_team)
    home_xg = sum(e.xg or 0 for e in events if e.type == "shot" and e.is_own_team)
    away_xg = sum(e.xg or 0 for e in events if e.type == "shot" and not e.is_own_team)
    total_poss = possession_ticks["home"] + possession_ticks["away"]

    # The roster describes each slot as it was at kick-off, not as it ended.
    # Playback frames are per-slot, so a token has to start life as the player
    # who actually took the field; `substitutions` carries the slot index, which
    # is what lets the client relabel that token at the right minute.
    roster = []
    for side in ("home", "away"):
        starters = home.starters if side == "home" else away.starters
        for p in starters:
            roster.append({"id": p.id, "side": side, "name": p.name,
                           "position": p.position.value, "number": None})

    return SimResult(
        home=home, away=away, events=events, frames=frames,
        playback_frames=playback_frames, playback_roster=roster,
        playback_hz=playback_hz,
        score=(home_goals, away_goals), xg=(home_xg, away_xg),
        shots=(home_shots, away_shots),
        possession_pct=100 * possession_ticks["home"] / total_poss if total_poss else 50.0,
        condition_timeline=condition_timeline, substitutions=substitutions,
        minutes=minutes, seed=seed,
    )


def _maybe_substitute(setup: TeamSetup, on_pitch: list[SimPlayer],
                      slot_distance, minute: int, used: int) -> dict | None:
    """Swap the most-degraded outfielder for the best available replacement.

    Deliberately simple next to the full substitution engine: during a live
    simulation the question is only "who is spent and who can replace them",
    and the engine's tactical reasoning needs match data that does not exist
    until the fixture is over.
    """
    if used >= 5 or not setup.bench:
        return None

    candidates = [
        (i, p) for i, p in enumerate(on_pitch)
        if i != 0 and p.condition < 0.90 and p.came_on_at is None
    ]
    if not candidates:
        return None

    slot_index, worst = min(candidates, key=lambda t: t[1].condition)
    target = worst.position

    best = None
    for sub in setup.bench:
        if not sub.on_pitch and sub.came_on_at is None:
            fit = position_fit(sub, target)
            if fit < 0.55:
                continue
            projected = sub.rating * (1 - 0.6 * (1 - fit))
            current = worst.rating * worst.condition
            if projected > current and (best is None or projected > best[1]):
                best = (sub, projected, fit)

    if best is None:
        return None

    sub, _projected, fit = best
    reason = f"{worst.name} down to {worst.condition:.0%} of level"

    # Hand the slot's accumulated distance to the player leaving it.
    worst.distance_m += float(slot_distance[slot_index])
    slot_distance[slot_index] = 0.0

    sub.on_pitch = True
    sub.came_on_at = minute
    sub.condition = 1.0
    worst.on_pitch = False
    worst.came_off_at = minute
    on_pitch[slot_index] = sub
    # `starters` and `bench` stay exactly as they were named at kick-off:
    # eligibility is decided by `came_on_at` / `on_pitch` above, not by list
    # membership. Moving players between the lists dropped whoever came on out
    # of the squad entirely (they were in neither list any more) and made a
    # substituted player eligible to come back on.

    return {
        "off": {"id": worst.id, "name": worst.name,
                "condition": round(worst.condition, 3)},
        "on": {"id": sub.id, "name": sub.name, "position_fit": round(fit, 3)},
        "position": target.value,
        "slot_index": slot_index,
        "reason": reason,
    }
