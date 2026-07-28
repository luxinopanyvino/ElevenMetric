"""Pitch geometry, coordinate normalisation and zone grids.

Every provider ships a different coordinate convention (StatsBomb 120x80 with a
flipped y-axis, Opta 0-100 percentages, tracking feeds in metres centred on the
halfway line). Everything downstream of this module works in a single frame:

    metres, origin at the bottom-left corner, x along the attacking direction
    of the team being analysed, 0 <= x <= length, 0 <= y <= width.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DEFAULT_LENGTH = 105.0
DEFAULT_WIDTH = 68.0

#: Zone grid used for control/heat summaries. 6 columns x 5 rows is the
#: granularity coaching staff read comfortably; the KDE heatmap is finer.
ZONE_COLS = 6
ZONE_ROWS = 5

#: Finer grid for expected-threat and heatmaps.
XT_COLS = 16
XT_ROWS = 12


@dataclass(frozen=True)
class Pitch:
    length: float = DEFAULT_LENGTH
    width: float = DEFAULT_WIDTH

    @property
    def area(self) -> float:
        return self.length * self.width

    @property
    def goal_center(self) -> tuple[float, float]:
        """Centre of the goal being attacked."""
        return (self.length, self.width / 2)

    @property
    def goal_width(self) -> float:
        return 7.32

    def thirds(self) -> tuple[float, float]:
        return self.length / 3, 2 * self.length / 3

    def clip(self, x: float, y: float) -> tuple[float, float]:
        return (
            float(min(max(x, 0.0), self.length)),
            float(min(max(y, 0.0), self.width)),
        )

    def zone_of(self, x: float, y: float) -> tuple[int, int]:
        """Return ``(col, row)`` in the coarse zone grid."""
        x, y = self.clip(x, y)
        col = min(int(x / self.length * ZONE_COLS), ZONE_COLS - 1)
        row = min(int(y / self.width * ZONE_ROWS), ZONE_ROWS - 1)
        return col, row

    def third_of(self, x: float) -> str:
        low, high = self.thirds()
        if x < low:
            return "defensive"
        if x < high:
            return "middle"
        return "attacking"

    def in_penalty_area(self, x: float, y: float) -> bool:
        return x >= self.length - 16.5 and abs(y - self.width / 2) <= 20.16

    def distance_to_goal(self, x: float, y: float) -> float:
        gx, gy = self.goal_center
        return float(np.hypot(gx - x, gy - y))

    def angle_to_goal(self, x: float, y: float) -> float:
        """Visible goal angle in radians. Zero on the goal line outside the posts."""
        gx, gy = self.goal_center
        half = self.goal_width / 2
        p1 = np.array([gx, gy - half])
        p2 = np.array([gx, gy + half])
        p = np.array([x, y])
        v1, v2 = p1 - p, p2 - p
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 < 1e-6 or n2 < 1e-6:
            return 0.0
        cos = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
        return float(np.arccos(cos))


# --- Provider adapters -----------------------------------------------------

#: (x_max, y_max, y_is_flipped) per known provider convention.
PROVIDER_FRAMES: dict[str, tuple[float, float, bool]] = {
    "elevenmetric": (105.0, 68.0, False),
    "statsbomb": (120.0, 80.0, True),
    "opta": (100.0, 100.0, False),
    "wyscout": (100.0, 100.0, False),
    "skillcorner": (105.0, 68.0, False),
    "second_spectrum": (105.0, 68.0, False),
}


def to_metres(
    x: float,
    y: float,
    provider: str = "elevenmetric",
    pitch: Pitch | None = None,
) -> tuple[float, float]:
    """Convert a provider coordinate into the canonical metre frame."""
    pitch = pitch or Pitch()
    frame = PROVIDER_FRAMES.get(provider.lower())
    if frame is None:
        raise ValueError(
            f"Unknown provider frame '{provider}'. Known: {sorted(PROVIDER_FRAMES)}"
        )
    x_max, y_max, flipped = frame
    mx = x / x_max * pitch.length
    my = y / y_max * pitch.width
    if flipped:
        my = pitch.width - my
    return pitch.clip(mx, my)


def flip_for_direction(x: float, y: float, pitch: Pitch, attacking_right: bool) -> tuple[float, float]:
    """Normalise so the analysed team always attacks towards +x.

    Second-half coordinates arrive mirrored; forgetting this is the classic way
    to end up with a heatmap that shows a striker defending his own box.
    """
    if attacking_right:
        return x, y
    return pitch.length - x, pitch.width - y


#: x-band names, defensive → attacking.
ZONE_X_NAMES = ["own box", "own third", "mid-def", "mid-att", "final third", "opp box"]
#: y-band names, y=0 → y=width (right wing → left wing when attacking towards +x).
ZONE_Y_NAMES = ["right wing", "right half-space", "centre", "left half-space", "left wing"]


def zone_labels() -> list[list[str]]:
    """Human names for the coarse grid, indexed ``[row][col]``."""
    return [
        [f"{ZONE_X_NAMES[c]} · {ZONE_Y_NAMES[r]}" for c in range(ZONE_COLS)]
        for r in range(ZONE_ROWS)
    ]
