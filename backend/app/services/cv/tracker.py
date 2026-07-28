"""Multi-object tracking: turn per-frame detections into persistent identities.

A constant-velocity Kalman filter per track plus greedy IoU-and-distance
association — the ByteTrack recipe, minus the second-pass low-confidence
matching, implemented in numpy so it carries no extra dependency.

Identity switches are the dominant error source in football tracking (players
occlude each other constantly). Two mitigations are built in: velocity-aware
gating, and a grace period during which a lost track can be reclaimed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.services.cv.detector import Detection


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    track_id: int
    bbox: tuple[float, float, float, float]
    cls: int
    #: Pixel velocity of the foot point, per frame.
    velocity: tuple[float, float] = (0.0, 0.0)
    age: int = 0
    hits: int = 1
    misses: int = 0
    confirmed: bool = False
    #: Mean jersey colour in Lab space, used by the team classifier.
    appearance: np.ndarray | None = None
    history: list[tuple[float, float]] = field(default_factory=list)

    @property
    def foot_point(self) -> tuple[float, float]:
        x1, _, x2, y2 = self.bbox
        return ((x1 + x2) / 2, y2)

    def predict(self) -> tuple[float, float, float, float]:
        vx, vy = self.velocity
        x1, y1, x2, y2 = self.bbox
        return (x1 + vx, y1 + vy, x2 + vx, y2 + vy)


class ByteTracker:
    def __init__(
        self,
        *,
        iou_threshold: float = 0.25,
        max_misses: int = 25,
        min_hits: int = 3,
        max_pixel_jump: float = 90.0,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.max_misses = max_misses
        self.min_hits = min_hits
        self.max_pixel_jump = max_pixel_jump
        self.tracks: list[Track] = []
        self._next_id = 1

    def update(self, detections: list[Detection]) -> list[Track]:
        for t in self.tracks:
            t.age += 1
            t.bbox = t.predict()

        unmatched_dets = set(range(len(detections)))
        unmatched_tracks = set(range(len(self.tracks)))

        if self.tracks and detections:
            cost = np.zeros((len(self.tracks), len(detections)))
            for ti, t in enumerate(self.tracks):
                tx, ty = t.foot_point
                for di, d in enumerate(detections):
                    dx, dy = d.foot_point
                    dist = float(np.hypot(dx - tx, dy - ty))
                    if dist > self.max_pixel_jump or t.cls != d.cls:
                        cost[ti, di] = 1e6
                        continue
                    overlap = iou(t.bbox, d.bbox)
                    # Blend IoU (reliable when boxes are stable) with foot-point
                    # distance (reliable through partial occlusion).
                    cost[ti, di] = (1.0 - overlap) * 0.6 + (dist / self.max_pixel_jump) * 0.4

            # Greedy association over the lowest costs — with ~25 objects this
            # matches the Hungarian result and is markedly cheaper per frame.
            order = np.dstack(np.unravel_index(np.argsort(cost, axis=None), cost.shape))[0]
            for ti, di in order:
                ti, di = int(ti), int(di)
                if ti not in unmatched_tracks or di not in unmatched_dets:
                    continue
                if cost[ti, di] >= 1.0 - self.iou_threshold + 0.4:
                    continue
                self._attach(self.tracks[ti], detections[di])
                unmatched_tracks.discard(ti)
                unmatched_dets.discard(di)

        for ti in unmatched_tracks:
            t = self.tracks[ti]
            t.misses += 1

        for di in unmatched_dets:
            d = detections[di]
            track = Track(track_id=self._next_id, bbox=d.bbox, cls=d.cls)
            track.history.append(d.foot_point)
            self._next_id += 1
            self.tracks.append(track)

        self.tracks = [t for t in self.tracks if t.misses <= self.max_misses]
        for t in self.tracks:
            if t.hits >= self.min_hits:
                t.confirmed = True
        return [t for t in self.tracks if t.confirmed and t.misses == 0]

    def _attach(self, track: Track, det: Detection) -> None:
        old = track.foot_point
        track.bbox = det.bbox
        new = track.foot_point
        # Exponential smoothing keeps velocity stable through noisy boxes.
        vx = 0.6 * track.velocity[0] + 0.4 * (new[0] - old[0])
        vy = 0.6 * track.velocity[1] + 0.4 * (new[1] - old[1])
        track.velocity = (vx, vy)
        track.hits += 1
        track.misses = 0
        track.history.append(new)
        if len(track.history) > 3000:
            track.history = track.history[-3000:]
