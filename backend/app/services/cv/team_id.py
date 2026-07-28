"""Team assignment from kit colour.

Crops the torso region of each detection, reduces it to a dominant colour in a
perceptually uniform space, and clusters into two teams. Torso-only matters:
including shorts and socks blurs kits that differ only above the waist, and
including the grass background makes every player look green.

Goalkeepers are excluded — their kit is deliberately unlike either outfield
strip and would otherwise anchor a cluster on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (0-255) → CIE Lab (D65). Distances in Lab track perception, so a
    threshold set on one fixture transfers to another."""
    arr = np.asarray(rgb, dtype=float).reshape(-1, 3) / 255.0
    mask = arr > 0.04045
    arr = np.where(mask, ((arr + 0.055) / 1.055) ** 2.4, arr / 12.92)

    M = np.array([
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ])
    xyz = arr @ M.T
    white = np.array([0.95047, 1.0, 1.08883])
    xyz = xyz / white

    eps = 216 / 24389
    kappa = 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)

    L = 116 * f[:, 1] - 16
    a = 500 * (f[:, 0] - f[:, 1])
    b = 200 * (f[:, 1] - f[:, 2])
    return np.column_stack([L, a, b])


def torso_colour(frame, bbox: tuple[float, float, float, float]) -> np.ndarray | None:
    """Dominant Lab colour of the torso band of a detection box.

    Uses the middle 50% horizontally and the 20-55% band vertically — shoulders
    to waist — and drops pitch-green pixels before averaging.
    """
    x1, y1, x2, y2 = (int(v) for v in bbox)
    h, w = frame.shape[:2]
    bw, bh = x2 - x1, y2 - y1
    if bw < 6 or bh < 12:
        return None

    cx1 = max(0, x1 + int(0.25 * bw))
    cx2 = min(w, x1 + int(0.75 * bw))
    cy1 = max(0, y1 + int(0.20 * bh))
    cy2 = min(h, y1 + int(0.55 * bh))
    if cx2 <= cx1 or cy2 <= cy1:
        return None

    crop = frame[cy1:cy2, cx1:cx2]
    if crop.size == 0:
        return None

    # Frames arrive BGR from OpenCV.
    pixels = crop.reshape(-1, 3)[:, ::-1].astype(float)
    lab = _srgb_to_lab(pixels)

    # Drop grass: green-dominant pixels have strongly negative a*.
    keep = lab[:, 1] > -12
    if keep.sum() < 12:
        keep = np.ones(len(lab), dtype=bool)
    return lab[keep].mean(axis=0)


@dataclass
class TeamClassifier:
    """Two-cluster kit classifier with a running fit."""

    centroids: np.ndarray | None = None
    #: Lab distance beyond which a sample is called "unknown" rather than forced
    #: into a cluster — referees and keepers land here.
    outlier_distance: float = 34.0
    samples: list[np.ndarray] = field(default_factory=list)
    labels_seen: dict[int, int] = field(default_factory=dict)

    def observe(self, colour: np.ndarray | None) -> None:
        if colour is not None and np.all(np.isfinite(colour)):
            self.samples.append(colour)

    def fit(self, k: int = 2, iters: int = 40) -> bool:
        """k-means on collected samples. Seeded on the two most distant points,
        which is deterministic and avoids a random restart landing on one kit."""
        if len(self.samples) < 2 * k:
            return False
        X = np.vstack(self.samples)

        d = np.linalg.norm(X[:, None, :] - X[None, :, :], axis=2)
        i, j = np.unravel_index(np.argmax(d), d.shape)
        centroids = X[[i, j]].astype(float)
        if k > 2:
            for _ in range(k - 2):
                dist = np.min(np.linalg.norm(X[:, None, :] - centroids[None, :, :], axis=2), axis=1)
                centroids = np.vstack([centroids, X[int(np.argmax(dist))]])

        for _ in range(iters):
            assign = np.argmin(np.linalg.norm(X[:, None, :] - centroids[None, :, :], axis=2), axis=1)
            new = np.array([
                X[assign == c].mean(axis=0) if np.any(assign == c) else centroids[c]
                for c in range(k)
            ])
            if np.allclose(new, centroids, atol=1e-4):
                centroids = new
                break
            centroids = new

        self.centroids = centroids
        return True

    def classify(self, colour: np.ndarray | None) -> int | None:
        """Return the cluster index, or ``None`` for an outlier."""
        if colour is None or self.centroids is None:
            return None
        d = np.linalg.norm(self.centroids - colour, axis=1)
        idx = int(np.argmin(d))
        if d[idx] > self.outlier_distance:
            return None
        return idx

    def separation(self) -> float:
        """Lab distance between the two kits. Below ~18 the kits are too alike
        to separate reliably, and the report should say so."""
        if self.centroids is None or len(self.centroids) < 2:
            return 0.0
        return float(np.linalg.norm(self.centroids[0] - self.centroids[1]))

    def assign_side(self, cluster: int, home_colour_lab: np.ndarray) -> str:
        """Map a cluster onto "home"/"away" using the known home kit colour."""
        if self.centroids is None:
            return "unknown"
        d = np.linalg.norm(self.centroids - home_colour_lab, axis=1)
        home_cluster = int(np.argmin(d))
        return "home" if cluster == home_cluster else "away"


def hex_to_lab(hex_colour: str) -> np.ndarray:
    h = hex_colour.lstrip("#")
    rgb = np.array([int(h[i : i + 2], 16) for i in (0, 2, 4)], dtype=float)
    return _srgb_to_lab(rgb)[0]
