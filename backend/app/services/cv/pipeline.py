"""Video → tracking pipeline.

Stages, in order:

1. **Decode** the video, sampling at a fixed rate (default 5 Hz — enough for
   positional analytics, and 5x cheaper than decoding every frame).
2. **Detect** players, goalkeepers, the ball and referees.
3. **Track** detections into persistent identities.
4. **Calibrate** the camera against pitch landmarks, per frame.
5. **Project** foot points into pitch metres.
6. **Classify** each track into home/away by kit colour.
7. **Derive** ball-possession events from the tracked ball.

If the CV extras are not installed, :func:`run` returns a clearly-labelled
simulated result instead of failing — the demo path stays usable, and the job
record carries ``engine="simulated"`` so no one mistakes it for measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from app.services.analytics.pitch import Pitch
from app.services.cv.detector import (
    CLASS_BALL,
    CLASS_GOALKEEPER,
    CLASS_PLAYER,
    available_backends,
    build_detector,
)
from app.services.cv.homography import CalibrationState
from app.services.cv.synthetic import SimFrame, simulate_match
from app.services.cv.team_id import TeamClassifier, hex_to_lab, torso_colour
from app.services.cv.tracker import ByteTracker

ProgressFn = Callable[[float, str], None]


@dataclass
class PipelineResult:
    frames: list                          # SimFrame-compatible records
    engine: str
    duration_s: float
    fps_sampled: float
    #: Fraction of sampled frames with a valid homography.
    calibration_coverage: float = 0.0
    #: Lab separation between the two kits; below ~18 team ids are unreliable.
    kit_separation: float = 0.0
    tracks_seen: int = 0
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "duration_s": round(self.duration_s, 2),
            "fps_sampled": self.fps_sampled,
            "frames": len(self.frames),
            "calibration_coverage": round(self.calibration_coverage, 4),
            "kit_separation": round(self.kit_separation, 2),
            "tracks_seen": self.tracks_seen,
            "warnings": self.warnings,
            **self.meta,
        }


def capabilities() -> dict:
    caps = available_backends()
    caps["full_pipeline"] = caps["yolo"] and caps["opencv"]
    caps["engine"] = (
        "yolo+bytetrack" if caps["full_pipeline"]
        else "hog+bytetrack" if caps["opencv"]
        else "simulated"
    )
    caps["note"] = (
        "Computer-vision extras installed; video is analysed frame by frame."
        if caps["opencv"]
        else "Computer-vision extras not installed — video jobs fall back to a "
             "labelled simulation. Install with: pip install -r requirements-cv.txt"
    )
    return caps


def run(
    video_path: str | Path,
    *,
    sample_hz: float = 5.0,
    home_kit_hex: str = "#2a78d6",
    pitch: Pitch | None = None,
    progress: ProgressFn | None = None,
    max_seconds: float | None = None,
    landmark_fn: Callable[[object], dict] | None = None,
) -> PipelineResult:
    """Process a video into pitch-space tracking frames.

    ``landmark_fn`` maps a decoded frame to ``{landmark_name: (px, py)}``. In
    production this is a keypoint model; it is injectable so the pipeline can be
    tested with a fixed calibration.
    """
    pitch = pitch or Pitch()
    caps = capabilities()

    if not caps["opencv"]:
        return _simulated_fallback(video_path, sample_hz=sample_hz, pitch=pitch,
                                   progress=progress, reason=caps["note"])

    import cv2  # available past this point

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total_frames / src_fps if total_frames else 0.0
    step = max(1, int(round(src_fps / sample_hz)))

    detector = build_detector()
    if detector is None:
        cap.release()
        return _simulated_fallback(video_path, sample_hz=sample_hz, pitch=pitch,
                                   progress=progress, reason="No detector backend available")

    tracker = ByteTracker()
    calib = CalibrationState(pitch=pitch)
    classifier = TeamClassifier()
    home_lab = hex_to_lab(home_kit_hex)

    frames: list[SimFrame] = []
    warnings: list[str] = []
    calibrated_frames = 0
    sampled = 0
    idx = 0
    track_ids: set[int] = set()
    pending: list[tuple[int, list, list, tuple[float, float] | None]] = []

    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step != 0:
            idx += 1
            continue
        ok, frame = cap.retrieve()
        if not ok:
            idx += 1
            continue

        t_s = idx / src_fps
        if max_seconds is not None and t_s > max_seconds:
            break

        detections = detector.detect(frame)
        tracks = tracker.update(detections)
        track_ids.update(t.track_id for t in tracks)

        if landmark_fn is not None:
            calib.update(landmark_fn(frame))
        else:
            calib.frames_since_update += 1

        players = [t for t in tracks if t.cls in (CLASS_PLAYER, CLASS_GOALKEEPER)]
        balls = [t for t in tracks if t.cls == CLASS_BALL]

        for t in players:
            if t.appearance is None:
                t.appearance = torso_colour(frame, t.bbox)
                classifier.observe(t.appearance)

        ball_pt = balls[0].foot_point if balls else None
        pending.append((idx, players, [], ball_pt))

        if calib.is_valid:
            calibrated_frames += 1

        sampled += 1
        if progress and total_frames:
            progress(min(0.85, idx / total_frames * 0.85), "detect+track")
        idx += 1

    cap.release()

    if not classifier.fit():
        warnings.append("Not enough kit samples to separate the two teams reliably.")
    separation = classifier.separation()
    if 0 < separation < 18:
        warnings.append(
            f"Kit colours are close in Lab space (separation {separation:.1f}); "
            "team assignment may be unreliable for this fixture."
        )

    for i, (frame_idx, players, _unused, ball_pt) in enumerate(pending):
        if progress:
            progress(0.85 + 0.15 * (i + 1) / max(len(pending), 1), "project")
        if not calib.is_valid:
            continue
        pts = calib.to_pitch([t.foot_point for t in players])
        home: dict[str, list[float]] = {}
        away: dict[str, list[float]] = {}
        for t, (mx, my) in zip(players, pts):
            cluster = classifier.classify(t.appearance)
            if cluster is None:
                continue
            side = classifier.assign_side(cluster, home_lab)
            (home if side == "home" else away)[f"t{t.track_id}"] = [round(mx, 2), round(my, 2)]

        ball_m = None
        if ball_pt is not None:
            proj = calib.to_pitch([ball_pt])
            if proj:
                ball_m = [round(proj[0][0], 2), round(proj[0][1], 2)]

        frames.append(SimFrame(
            period=1 if (frame_idx / src_fps) < duration / 2 else 2,
            timestamp_ms=int(frame_idx / src_fps * 1000),
            home_positions=home, away_positions=away,
            ball=ball_m, possession_team=None,
        ))

    coverage = calibrated_frames / sampled if sampled else 0.0
    if coverage < 0.5:
        warnings.append(
            f"Camera calibration succeeded on only {coverage:.0%} of sampled frames. "
            "Positional metrics from this clip are low confidence — a tactical "
            "(wide, fixed) camera angle gives far better results than broadcast."
        )

    return PipelineResult(
        frames=frames,
        engine=caps["engine"],
        duration_s=duration,
        fps_sampled=sample_hz,
        calibration_coverage=coverage,
        kit_separation=separation,
        tracks_seen=len(track_ids),
        warnings=warnings,
        meta={"source_fps": src_fps, "sampled_frames": sampled, "detector": detector.name},
    )


def _simulated_fallback(
    video_path: str | Path,
    *,
    sample_hz: float,
    pitch: Pitch,
    progress: ProgressFn | None,
    reason: str,
) -> PipelineResult:
    """Produce clearly-labelled synthetic tracking when CV extras are missing."""
    if progress:
        progress(0.2, "simulating")

    # Seed off the file path so the same upload always yields the same result.
    seed = abs(hash(str(video_path))) % (2**31)
    duration_guess = 15 * 60.0
    sim = simulate_match(
        minutes=int(duration_guess / 60), frame_hz=sample_hz, seed=seed, pitch=pitch
    )

    if progress:
        progress(1.0, "simulated")

    return PipelineResult(
        frames=sim.frames,
        engine="simulated",
        duration_s=duration_guess,
        fps_sampled=sample_hz,
        calibration_coverage=0.0,
        kit_separation=0.0,
        tracks_seen=22,
        warnings=[
            "SIMULATED OUTPUT — no video was analysed. " + reason,
            "Every metric derived from this job is synthetic and must not be "
            "used for decisions.",
        ],
        meta={"simulated": True, "seed": seed, **sim.meta},
    )


def frames_to_average_positions(frames: list, side: str = "home") -> dict[str, tuple[float, float]]:
    """Mean position per tracked entity, for the formation detector."""
    acc: dict[str, list[list[float]]] = {}
    key = "home_positions" if side == "home" else "away_positions"
    for f in frames:
        for pid, pos in (getattr(f, key, None) or {}).items():
            acc.setdefault(pid, []).append(pos)
    return {
        pid: (float(np.mean([p[0] for p in pts])), float(np.mean([p[1] for p in pts])))
        for pid, pts in acc.items()
        if len(pts) >= 5
    }
