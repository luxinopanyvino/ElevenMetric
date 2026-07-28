"""Value models: expected goals and expected threat.

Both are deliberately small, transparent and self-contained. They are *baseline*
models — a club with its own shot database should refit the coefficients — and
every report carries the model version so numbers stay comparable over time.
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from app.services.analytics.pitch import XT_COLS, XT_ROWS, Pitch

XG_MODEL_VERSION = "xg-logistic-1.0"
XT_MODEL_VERSION = "xt-grid-1.0"

# Logistic coefficients on (distance_m, visible_angle_rad). Fitted shape follows
# the well-established open-play relationship: conversion decays roughly
# exponentially with distance and rises with the visible goal angle.
_XG_INTERCEPT = -0.85
_XG_B_DISTANCE = -0.115
_XG_B_ANGLE = 1.90

#: Multiplicative situation adjustments applied after the logistic core.
_XG_SITUATION: dict[str, float] = {
    "open_play": 1.00,
    "counter": 1.20,
    "set_piece": 0.80,
    "corner": 0.75,
    "free_kick": 0.65,
    "penalty": 1.00,     # handled separately — fixed 0.76
    "rebound": 1.35,
    "big_chance": 1.60,
}

_XG_BODY_PART: dict[str, float] = {
    "foot": 1.00,
    "strong_foot": 1.05,
    "weak_foot": 0.85,
    "head": 0.62,
    "other": 0.55,
}

PENALTY_XG = 0.76


def expected_goals(
    x: float,
    y: float,
    *,
    pitch: Pitch | None = None,
    situation: str = "open_play",
    body_part: str = "foot",
    defenders_in_cone: int | None = None,
    is_penalty: bool = False,
) -> float:
    """Shot quality in [0, 1].

    ``defenders_in_cone`` is optional; when tracking data is available it is the
    single most informative extra feature, so it is used when present and
    ignored (rather than imputed) when absent.
    """
    if is_penalty or situation == "penalty":
        return PENALTY_XG

    pitch = pitch or Pitch()
    dist = pitch.distance_to_goal(x, y)
    angle = pitch.angle_to_goal(x, y)

    z = _XG_INTERCEPT + _XG_B_DISTANCE * dist + _XG_B_ANGLE * angle
    p = 1.0 / (1.0 + math.exp(-z))

    p *= _XG_SITUATION.get(situation, 1.0)
    p *= _XG_BODY_PART.get(body_part, 1.0)

    if defenders_in_cone is not None:
        # Each body in the shooting cone removes roughly a fifth of the value,
        # saturating — a wall of six is not thirty times worse than one.
        p *= math.exp(-0.22 * max(0, defenders_in_cone))

    return float(min(max(p, 0.0), 0.99))


#: Probability an action ends the possession without a shot. Without this term
#: the MDP has no absorbing failure state, the ball can be recycled for free,
#: and value iteration converges to an almost flat surface — every cell inherits
#: the value of the best shot anywhere on the pitch.
_XT_BASE_TURNOVER = 0.26
#: Extra turnover risk in the final third, where the defence is compressed.
_XT_FINAL_THIRD_TURNOVER = 0.13


@lru_cache(maxsize=4)
def xt_grid(cols: int = XT_COLS, rows: int = XT_ROWS) -> np.ndarray:
    """Expected-threat surface: P(goal before possession ends) per cell.

    Value iteration over a shoot / move / lose-it MDP, rather than a shipped
    magic table, so the grid can be regenerated at any resolution and every
    assumption stays inspectable:

    * **shoot** with probability rising as the ball nears goal, paying ``xG``;
    * **move** to another cell, drawn from a distance-decaying, forward-biased
      kernel;
    * **lose possession**, paying nothing — the term that makes deep positions
      genuinely less valuable than advanced ones.

    Returned array is indexed ``[row, col]``, row 0 = y near 0.
    """
    pitch = Pitch()
    cell_w = pitch.length / cols
    cell_h = pitch.width / rows

    centres = np.zeros((rows, cols, 2))
    for r in range(rows):
        for c in range(cols):
            centres[r, c] = ((c + 0.5) * cell_w, (r + 0.5) * cell_h)

    shoot_p = np.zeros((rows, cols))
    goal_p = np.zeros((rows, cols))
    lose_p = np.zeros((rows, cols))
    for r in range(rows):
        for c in range(cols):
            x, y = centres[r, c]
            dist = pitch.distance_to_goal(x, y)
            goal_p[r, c] = expected_goals(x, y, pitch=pitch)
            # Players shoot more the closer and more central they are.
            shoot_p[r, c] = 0.92 / (1.0 + math.exp((dist - 17.0) / 4.0))
            final_third = max(0.0, (x - 2 * pitch.length / 3) / (pitch.length / 3))
            lose_p[r, c] = _XT_BASE_TURNOVER + _XT_FINAL_THIRD_TURNOVER * final_third

    # Normalise so the three options are a proper distribution.
    total = shoot_p + lose_p
    over = total > 0.97
    scale = np.where(over, 0.97 / np.maximum(total, 1e-9), 1.0)
    shoot_p *= scale
    lose_p *= scale
    move_p = np.clip(1.0 - shoot_p - lose_p, 0.0, 1.0)

    # Transition kernel. The scale is a typical pass/carry length, not the pitch
    # — a diffuse kernel mixes the whole surface and flattens the result.
    flat_centres = centres.reshape(-1, 2)
    diff = flat_centres[None, :, :] - flat_centres[:, None, :]
    dist = np.hypot(diff[..., 0], diff[..., 1])
    forward = diff[..., 0]
    kernel = np.exp(-dist / 9.0) * np.exp(np.clip(forward, -40, 40) / 26.0)
    np.fill_diagonal(kernel, 0.0)
    kernel /= kernel.sum(axis=1, keepdims=True)

    shoot_flat = shoot_p.reshape(-1)
    move_flat = move_p.reshape(-1)
    goal_flat = goal_p.reshape(-1)

    value = (shoot_flat * goal_flat).copy()
    for _ in range(400):
        new = shoot_flat * goal_flat + move_flat * (kernel @ value)
        if np.max(np.abs(new - value)) < 1e-9:
            value = new
            break
        value = new

    return value.reshape(rows, cols)


def xt_value(x: float, y: float, pitch: Pitch | None = None) -> float:
    """Threat value of a single location."""
    pitch = pitch or Pitch()
    grid = xt_grid()
    rows, cols = grid.shape
    x, y = pitch.clip(x, y)
    c = min(int(x / pitch.length * cols), cols - 1)
    r = min(int(y / pitch.width * rows), rows - 1)
    return float(grid[r, c])


def xt_delta(
    start: tuple[float, float], end: tuple[float, float], pitch: Pitch | None = None
) -> float:
    """Threat added by moving the ball from ``start`` to ``end``.

    Only successful actions should be passed in; a failed pass adds nothing.
    """
    return xt_value(*end, pitch=pitch) - xt_value(*start, pitch=pitch)


def packing(
    start: tuple[float, float],
    end: tuple[float, float],
    opponents: list[tuple[float, float]],
) -> int:
    """How many opponents the action played through.

    An opponent counts when they sit between the two points along the goal-ward
    axis and within a 12 m corridor of the pass line — the standard
    approximation when no defensive-shape model is available.
    """
    (sx, sy), (ex, ey) = start, end
    if ex <= sx:
        return 0
    dx, dy = ex - sx, ey - sy
    seg_len = math.hypot(dx, dy)
    if seg_len < 1e-6:
        return 0
    count = 0
    for ox, oy in opponents:
        t = ((ox - sx) * dx + (oy - sy) * dy) / (seg_len**2)
        if not (0.0 <= t <= 1.0):
            continue
        px, py = sx + t * dx, sy + t * dy
        if math.hypot(ox - px, oy - py) <= 6.0:
            count += 1
    return count


def progressive(start: tuple[float, float], end: tuple[float, float],
                pitch: Pitch | None = None) -> bool:
    """UEFA-style definition: moves the ball at least 25% closer to goal, or
    ends inside the box having started outside it."""
    pitch = pitch or Pitch()
    d0 = pitch.distance_to_goal(*start)
    d1 = pitch.distance_to_goal(*end)
    if d0 < 1e-6:
        return False
    if pitch.in_penalty_area(*end) and not pitch.in_penalty_area(*start):
        return True
    return (d0 - d1) / d0 >= 0.25 and (d0 - d1) >= 5.0
