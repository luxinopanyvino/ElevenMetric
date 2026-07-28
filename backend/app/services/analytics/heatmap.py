"""Heatmap generation from event or tracking positions.

Two estimators, chosen by data density:

* **Tracking** (thousands of samples per player): a plain 2-D histogram on the
  analysis grid, then a Gaussian blur. Cheap and, at that density, unbiased.
* **Events** (tens to low hundreds of touches): kernel density estimation with a
  Silverman bandwidth. A histogram of 40 touches is mostly noise; KDE is what
  makes a touch map readable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.services.analytics.pitch import Pitch

#: Analysis grid. Finer than the coarse zone grid, coarse enough to stay small
#: over the wire (a 32x21 float grid is ~5 KB as JSON).
GRID_COLS = 32
GRID_ROWS = 21


@dataclass
class Heatmap:
    grid: np.ndarray                      # [rows, cols], sums to 1.0
    cols: int = GRID_COLS
    rows: int = GRID_ROWS
    method: str = "kde"
    sample_count: int = 0
    #: Centre of mass in metres, i.e. the player's average position.
    centroid: tuple[float, float] = (0.0, 0.0)
    #: Positional spread (std-dev along x and y) in metres.
    spread: tuple[float, float] = (0.0, 0.0)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "grid": [[round(float(v), 6) for v in row] for row in self.grid],
            "cols": self.cols,
            "rows": self.rows,
            "method": self.method,
            "samples": self.sample_count,
            "centroid": [round(self.centroid[0], 2), round(self.centroid[1], 2)],
            "spread": [round(self.spread[0], 2), round(self.spread[1], 2)],
            "max": round(float(self.grid.max()) if self.grid.size else 0.0, 6),
            **self.meta,
        }


def _gaussian_blur(grid: np.ndarray, sigma_cells: float) -> np.ndarray:
    """Separable Gaussian blur. Hand-rolled so scipy stays an optional dep."""
    if sigma_cells <= 0:
        return grid
    radius = max(1, int(3 * sigma_cells))
    ax = np.arange(-radius, radius + 1, dtype=float)
    kernel = np.exp(-(ax**2) / (2 * sigma_cells**2))
    kernel /= kernel.sum()

    padded = np.pad(grid, ((0, 0), (radius, radius)), mode="reflect")
    out = np.apply_along_axis(lambda m: np.convolve(m, kernel, mode="valid"), 1, padded)
    padded = np.pad(out, ((radius, radius), (0, 0)), mode="reflect")
    out = np.apply_along_axis(lambda m: np.convolve(m, kernel, mode="valid"), 0, padded)
    return out


def _silverman_bandwidth(values: np.ndarray) -> float:
    """Silverman's rule of thumb, floored so a static player still renders."""
    n = len(values)
    if n < 2:
        return 4.0
    std = float(np.std(values))
    iqr = float(np.subtract(*np.percentile(values, [75, 25])))
    spread = min(std, iqr / 1.349) if iqr > 0 else std
    if spread <= 0:
        return 4.0
    return max(1.06 * spread * n ** (-1 / 5), 2.5)


def build_heatmap(
    points: list[tuple[float, float]],
    *,
    pitch: Pitch | None = None,
    weights: list[float] | None = None,
    method: str = "auto",
    cols: int = GRID_COLS,
    rows: int = GRID_ROWS,
) -> Heatmap:
    """Build a normalised occupancy grid from ``points`` in metres."""
    pitch = pitch or Pitch()
    if not points:
        return Heatmap(grid=np.zeros((rows, cols)), cols=cols, rows=rows,
                       method="empty", sample_count=0)

    arr = np.asarray(points, dtype=float)
    arr[:, 0] = np.clip(arr[:, 0], 0, pitch.length)
    arr[:, 1] = np.clip(arr[:, 1], 0, pitch.width)
    w = np.asarray(weights, dtype=float) if weights is not None else np.ones(len(arr))
    w = np.clip(w, 0, None)
    if w.sum() <= 0:
        w = np.ones(len(arr))

    if method == "auto":
        method = "histogram" if len(arr) >= 400 else "kde"

    cell_w = pitch.length / cols
    cell_h = pitch.width / rows

    if method == "histogram":
        grid, _, _ = np.histogram2d(
            arr[:, 1], arr[:, 0], bins=[rows, cols],
            range=[[0, pitch.width], [0, pitch.length]], weights=w,
        )
        grid = _gaussian_blur(grid, sigma_cells=1.2)
    else:
        bw_x = _silverman_bandwidth(arr[:, 0])
        bw_y = _silverman_bandwidth(arr[:, 1])
        xs = (np.arange(cols) + 0.5) * cell_w
        ys = (np.arange(rows) + 0.5) * cell_h
        # [rows, cols, n] would blow up on tracking data; events are small, and
        # `method="histogram"` covers the dense case.
        dx = (xs[None, :, None] - arr[None, None, :, 0]) / bw_x
        dy = (ys[:, None, None] - arr[None, None, :, 1]) / bw_y
        grid = np.sum(w * np.exp(-0.5 * (dx**2 + dy**2)), axis=2)

    total = grid.sum()
    if total > 0:
        grid = grid / total

    centroid = (
        float(np.average(arr[:, 0], weights=w)),
        float(np.average(arr[:, 1], weights=w)),
    )
    spread = (float(np.std(arr[:, 0])), float(np.std(arr[:, 1])))

    return Heatmap(
        grid=grid, cols=cols, rows=rows, method=method,
        sample_count=len(arr), centroid=centroid, spread=spread,
    )


def zone_control(
    own_points: list[tuple[float, float]],
    opp_points: list[tuple[float, float]],
    *,
    pitch: Pitch | None = None,
    zone_cols: int = 6,
    zone_rows: int = 5,
) -> dict:
    """Per-zone share of presence, own team minus opponent.

    Values run -1 (zone owned by the opponent) to +1 (owned by us). This is the
    grid the UI paints with the diverging ramp.
    """
    pitch = pitch or Pitch()

    def _counts(pts: list[tuple[float, float]]) -> np.ndarray:
        if not pts:
            return np.zeros((zone_rows, zone_cols))
        arr = np.asarray(pts, dtype=float)
        # Vectorised: tracking data reaches hundreds of thousands of points, and
        # a Python loop over them dominates the whole report's runtime.
        g, _, _ = np.histogram2d(
            np.clip(arr[:, 1], 0, pitch.width),
            np.clip(arr[:, 0], 0, pitch.length),
            bins=[zone_rows, zone_cols],
            range=[[0, pitch.width], [0, pitch.length]],
        )
        return g

    own = _counts(own_points)
    opp = _counts(opp_points)
    total = own + opp
    with np.errstate(divide="ignore", invalid="ignore"):
        diff = np.where(total > 0, (own - opp) / total, 0.0)

    own_share = own.sum() / total.sum() if total.sum() else 0.0
    return {
        "cols": zone_cols,
        "rows": zone_rows,
        "control": [[round(float(v), 4) for v in row] for row in diff],
        "own_counts": [[int(v) for v in row] for row in own],
        "opp_counts": [[int(v) for v in row] for row in opp],
        "own_share": round(float(own_share), 4),
    }
