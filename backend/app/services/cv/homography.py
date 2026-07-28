"""Camera-to-pitch mapping.

Detections live in pixels; every metric downstream needs metres. The bridge is
a homography from the image plane to the pitch plane, estimated from pitch
landmarks (line intersections, penalty-box corners, the centre circle).

The homography is re-estimated continuously — a broadcast camera pans and zooms
constantly, and a stale matrix silently corrupts every distance in the report.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.services.analytics.pitch import Pitch

#: Canonical pitch landmarks in metres on a 105x68 pitch, keyed by name.
#: A model that predicts these keypoints in image space gives the correspondences.
PITCH_LANDMARKS: dict[str, tuple[float, float]] = {
    "corner_bl": (0.0, 0.0),
    "corner_tl": (0.0, 68.0),
    "corner_br": (105.0, 0.0),
    "corner_tr": (105.0, 68.0),
    "halfway_bottom": (52.5, 0.0),
    "halfway_top": (52.5, 68.0),
    "centre_spot": (52.5, 34.0),
    "left_box_tl": (0.0, 54.16),
    "left_box_tr": (16.5, 54.16),
    "left_box_br": (16.5, 13.84),
    "left_box_bl": (0.0, 13.84),
    "right_box_tl": (88.5, 54.16),
    "right_box_tr": (105.0, 54.16),
    "right_box_br": (105.0, 13.84),
    "right_box_bl": (88.5, 13.84),
    "left_six_tl": (0.0, 43.16),
    "left_six_tr": (5.5, 43.16),
    "left_six_br": (5.5, 24.84),
    "left_six_bl": (0.0, 24.84),
    "right_six_tl": (99.5, 43.16),
    "right_six_tr": (105.0, 43.16),
    "right_six_br": (105.0, 24.84),
    "right_six_bl": (99.5, 24.84),
    "left_penalty_spot": (11.0, 34.0),
    "right_penalty_spot": (94.0, 34.0),
}


def estimate_homography(
    image_points: list[tuple[float, float]],
    pitch_points: list[tuple[float, float]],
) -> np.ndarray | None:
    """Direct Linear Transform with Hartley normalisation.

    Four correspondences are the minimum; more are least-squared. Normalising
    the two point sets first is not optional — without it the DLT is badly
    conditioned and the result drifts at the edges of the frame.
    """
    if len(image_points) < 4 or len(image_points) != len(pitch_points):
        return None

    src = np.asarray(image_points, dtype=float)
    dst = np.asarray(pitch_points, dtype=float)

    def _normalise(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        centroid = pts.mean(axis=0)
        shifted = pts - centroid
        mean_dist = np.mean(np.linalg.norm(shifted, axis=1))
        if mean_dist < 1e-9:
            return np.eye(3), pts
        scale = np.sqrt(2) / mean_dist
        T = np.array([
            [scale, 0, -scale * centroid[0]],
            [0, scale, -scale * centroid[1]],
            [0, 0, 1],
        ])
        homo = np.column_stack([pts, np.ones(len(pts))])
        return T, (T @ homo.T).T[:, :2]

    T_src, src_n = _normalise(src)
    T_dst, dst_n = _normalise(dst)

    A = []
    for (x, y), (u, v) in zip(src_n, dst_n):
        A.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        A.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    A = np.asarray(A)

    try:
        _, _, Vt = np.linalg.svd(A)
    except np.linalg.LinAlgError:
        return None

    H_n = Vt[-1].reshape(3, 3)
    H = np.linalg.inv(T_dst) @ H_n @ T_src
    if abs(H[2, 2]) < 1e-12:
        return None
    return H / H[2, 2]


def project(H: np.ndarray, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Apply a homography to image points, returning pitch metres."""
    if not points:
        return []
    pts = np.column_stack([np.asarray(points, dtype=float), np.ones(len(points))])
    out = (H @ pts.T).T
    w = out[:, 2:3]
    w[np.abs(w) < 1e-12] = 1e-12
    out = out[:, :2] / w
    return [(float(x), float(y)) for x, y in out]


def reprojection_error(
    H: np.ndarray,
    image_points: list[tuple[float, float]],
    pitch_points: list[tuple[float, float]],
) -> float:
    """Mean reprojection error in metres. Above ~1.5 m the frame is unusable."""
    if H is None or not image_points:
        return float("inf")
    projected = project(H, image_points)
    errs = [
        float(np.hypot(px - tx, py - ty))
        for (px, py), (tx, ty) in zip(projected, pitch_points)
    ]
    return float(np.mean(errs)) if errs else float("inf")


@dataclass
class CalibrationState:
    """Rolling homography with validity gating."""

    pitch: Pitch
    H: np.ndarray | None = None
    error_m: float = float("inf")
    frames_since_update: int = 0
    max_error_m: float = 1.5
    max_stale_frames: int = 50

    @property
    def is_valid(self) -> bool:
        return (
            self.H is not None
            and self.error_m <= self.max_error_m
            and self.frames_since_update <= self.max_stale_frames
        )

    def update(
        self,
        landmarks: dict[str, tuple[float, float]],
    ) -> bool:
        """Feed detected landmarks (name → image point). Returns success."""
        pairs = [(img, PITCH_LANDMARKS[name])
                 for name, img in landmarks.items() if name in PITCH_LANDMARKS]
        if len(pairs) < 4:
            self.frames_since_update += 1
            return False

        img_pts = [p[0] for p in pairs]
        pitch_pts = [p[1] for p in pairs]
        H = estimate_homography(img_pts, pitch_pts)
        if H is None:
            self.frames_since_update += 1
            return False

        err = reprojection_error(H, img_pts, pitch_pts)
        if err > self.max_error_m:
            self.frames_since_update += 1
            return False

        self.H, self.error_m, self.frames_since_update = H, err, 0
        return True

    def to_pitch(self, image_points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not self.is_valid:
            return []
        return [self.pitch.clip(x, y) for x, y in project(self.H, image_points)]
