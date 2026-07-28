"""Formation detection and shape description.

The declared formation and the played formation are routinely different — a
nominal 4-3-3 with an inverted full-back plays as a 3-2-5 in possession. This
module reports both, and the gap between them is a first-class finding.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.services.analytics.pitch import Pitch

#: Formations the detector will name, as counts per line behind the striker(s).
KNOWN_FORMATIONS: dict[str, tuple[int, ...]] = {
    "4-4-2": (4, 4, 2),
    "4-4-1-1": (4, 4, 1, 1),
    "4-3-3": (4, 3, 3),
    "4-2-3-1": (4, 2, 3, 1),
    "4-1-4-1": (4, 1, 4, 1),
    "4-3-1-2": (4, 3, 1, 2),
    "4-5-1": (4, 5, 1),
    "3-5-2": (3, 5, 2),
    "3-4-3": (3, 4, 3),
    "3-4-2-1": (3, 4, 2, 1),
    "5-3-2": (5, 3, 2),
    "5-4-1": (5, 4, 1),
    "3-2-5": (3, 2, 5),
    "4-2-4": (4, 2, 4),
    "3-6-1": (3, 6, 1),
}


@dataclass
class FormationResult:
    formation: str = "unknown"
    line_counts: tuple[int, ...] = ()
    confidence: float = 0.0
    #: Mean position per player, ``{player_id: [x, y]}`` in metres.
    average_positions: dict[str, list[float]] = field(default_factory=dict)
    #: Mean x of each detected line.
    line_depths: list[float] = field(default_factory=list)
    #: Distance from the deepest outfielder to the highest, in metres.
    vertical_compactness: float = 0.0
    horizontal_compactness: float = 0.0
    #: Mean x of the back line — how high the team defends.
    defensive_line_height: float = 0.0
    #: Only present when phase-split data exists.
    in_possession: str | None = None
    out_of_possession: str | None = None

    def to_dict(self) -> dict:
        return {
            "formation": self.formation,
            "line_counts": list(self.line_counts),
            "confidence": round(self.confidence, 3),
            "average_positions": {
                k: [round(v[0], 2), round(v[1], 2)] for k, v in self.average_positions.items()
            },
            "line_depths": [round(d, 2) for d in self.line_depths],
            "vertical_compactness": round(self.vertical_compactness, 2),
            "horizontal_compactness": round(self.horizontal_compactness, 2),
            "defensive_line_height": round(self.defensive_line_height, 2),
            "in_possession": self.in_possession,
            "out_of_possession": self.out_of_possession,
        }


def _cluster_1d(values: np.ndarray, k: int, iters: int = 60) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic 1-D k-means. Seeds on quantiles, so no random restarts."""
    if k >= len(values):
        order = np.argsort(values)
        labels = np.empty(len(values), dtype=int)
        labels[order] = np.arange(len(values))
        return labels, values[order]

    qs = np.linspace(0, 100, k + 2)[1:-1]
    centres = np.percentile(values, qs)
    labels = np.zeros(len(values), dtype=int)
    for _ in range(iters):
        d = np.abs(values[:, None] - centres[None, :])
        new_labels = np.argmin(d, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for i in range(k):
            members = values[labels == i]
            if len(members):
                centres[i] = members.mean()
    return labels, centres


def _inertia(values: np.ndarray, labels: np.ndarray, centres: np.ndarray) -> float:
    return float(np.sum((values - centres[labels]) ** 2))


def detect_formation(
    positions: dict[str, tuple[float, float]],
    *,
    pitch: Pitch | None = None,
    goalkeeper_id: str | None = None,
) -> FormationResult:
    """Infer the shape from average positions.

    ``positions`` maps player id → mean ``(x, y)`` in metres. Eleven entries are
    expected; fewer still works but lowers the confidence.
    """
    pitch = pitch or Pitch()
    res = FormationResult(average_positions={k: [v[0], v[1]] for k, v in positions.items()})
    if len(positions) < 7:
        return res

    ids = list(positions.keys())
    xs = np.array([positions[i][0] for i in ids])
    ys = np.array([positions[i][1] for i in ids])

    # Drop the keeper: the deepest player, unless one was named.
    gk = goalkeeper_id if goalkeeper_id in positions else ids[int(np.argmin(xs))]
    out_ids = [i for i in ids if i != gk]
    out_x = np.array([positions[i][0] for i in out_ids])
    out_y = np.array([positions[i][1] for i in out_ids])

    res.vertical_compactness = float(out_x.max() - out_x.min())
    res.horizontal_compactness = float(out_y.max() - out_y.min())

    # Try 3 and 4 lines; pick by inertia with a penalty for the extra line, so
    # a genuine 4-2-3-1 wins but noise does not manufacture a fourth band.
    best = None
    for k in (3, 4):
        if k > len(out_x):
            continue
        labels, centres = _cluster_1d(out_x, k)
        counts = np.bincount(labels, minlength=k)
        if np.any(counts == 0):
            continue
        inertia = _inertia(out_x, labels, centres) + 45.0 * (k - 3)
        if best is None or inertia < best[0]:
            best = (inertia, k, labels, centres, counts)

    if best is None:
        return res

    inertia, k, labels, centres, counts = best
    order = np.argsort(centres)
    line_counts = tuple(int(counts[i]) for i in order)
    line_depths = [float(centres[i]) for i in order]

    res.line_counts = line_counts
    res.line_depths = line_depths
    res.defensive_line_height = line_depths[0]

    name, conf = _name_formation(line_counts)
    res.formation = name
    # Confidence blends shape-match quality with cluster separation.
    separation = float(np.min(np.diff(sorted(centres)))) if k > 1 else 0.0
    res.confidence = round(min(1.0, conf * (0.55 + 0.45 * min(separation / 12.0, 1.0))), 3)
    return res


def _name_formation(counts: tuple[int, ...]) -> tuple[str, float]:
    """Match a line-count tuple to a known name, tolerating one player off."""
    exact = {v: k for k, v in KNOWN_FORMATIONS.items()}
    if counts in exact:
        return exact[counts], 1.0

    best_name, best_dist = None, 1e9
    for name, ref in KNOWN_FORMATIONS.items():
        if len(ref) != len(counts):
            continue
        dist = sum(abs(a - b) for a, b in zip(ref, counts))
        if dist < best_dist:
            best_name, best_dist = name, dist
    if best_name is not None and best_dist <= 2:
        return best_name, max(0.4, 1.0 - 0.3 * best_dist)

    return "-".join(str(c) for c in counts), 0.5


def formation_from_slots(slots: list) -> str:
    """Derive the label from declared positions, for the manual-entry path."""
    from app.models.catalog import POSITION_LINE

    starters = [s for s in slots if s.is_starter]
    lines = {"DEF": 0, "MID": 0, "ATT": 0}
    for s in starters:
        line = POSITION_LINE.get(s.position)
        if line in lines:
            lines[line] += 1
    if sum(lines.values()) == 0:
        return "unknown"
    return f"{lines['DEF']}-{lines['MID']}-{lines['ATT']}"


def shape_deviation(declared: str, detected: FormationResult) -> dict:
    """Quantify the gap between the shape on the teamsheet and the one played."""
    if detected.formation in {"unknown", ""} or not declared:
        return {"deviation": None, "note": "insufficient data to compare shapes"}

    def _parts(label: str) -> list[int]:
        try:
            return [int(p) for p in label.split("-")]
        except ValueError:
            return []

    # Compare the two *labels*, not the label against the raw cluster counts:
    # `_name_formation` snaps near-misses to the closest known shape, so the
    # counts and the name it produced can legitimately differ by a player.
    a, b = _parts(declared), _parts(detected.formation)
    if not a or not b:
        return {"deviation": None, "note": "unparseable formation label"}

    # Compare line-by-line where the depths align; otherwise compare totals.
    if len(a) == len(b):
        delta = sum(abs(x - y) for x, y in zip(a, b))
    else:
        delta = abs(len(a) - len(b)) * 2

    return {
        "declared": declared,
        "played": detected.formation,
        "detected_line_counts": list(detected.line_counts),
        "deviation": delta,
        "matches": delta == 0,
        "note": (
            "Played shape matches the teamsheet."
            if delta == 0
            else f"Played shape drifts from the teamsheet by {delta} positional units — "
            "usually a full-back inverting or a midfielder dropping into the build-up."
        ),
    }
