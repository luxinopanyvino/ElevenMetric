"""Player and ball detection.

Three tiers, resolved at import time:

1. **ultralytics YOLO** — a detection model fine-tuned on football broadcast
   footage (classes: player, goalkeeper, ball, referee). This is the production
   path.
2. **OpenCV HOG** — a people detector. Coarse, no ball, but works with only
   ``opencv-python`` installed.
3. **Unavailable** — the pipeline reports ``engine="simulated"`` and the API
   makes that explicit in the job record rather than pretending it ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

#: Class ids the pipeline expects from a football-tuned YOLO model.
CLASS_BALL = 0
CLASS_GOALKEEPER = 1
CLASS_PLAYER = 2
CLASS_REFEREE = 3

CLASS_NAMES = {
    CLASS_BALL: "ball",
    CLASS_GOALKEEPER: "goalkeeper",
    CLASS_PLAYER: "player",
    CLASS_REFEREE: "referee",
}


@dataclass
class Detection:
    #: Pixel box, ``(x1, y1, x2, y2)``.
    bbox: tuple[float, float, float, float]
    confidence: float
    cls: int

    @property
    def centre(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @property
    def foot_point(self) -> tuple[float, float]:
        """Bottom-centre of the box — the point that sits on the pitch plane,
        and therefore the only one that survives the homography correctly."""
        x1, _, x2, y2 = self.bbox
        return ((x1 + x2) / 2, y2)


class Detector(Protocol):
    name: str

    def detect(self, frame) -> list[Detection]: ...


class YoloDetector:
    """ultralytics-backed detector."""

    name = "yolo"

    def __init__(self, weights: str = "yolov8m-football.pt", conf: float = 0.30) -> None:
        from ultralytics import YOLO  # imported lazily; optional dependency

        self.model = YOLO(weights)
        self.conf = conf

    def detect(self, frame) -> list[Detection]:
        results = self.model.predict(frame, conf=self.conf, verbose=False)
        out: list[Detection] = []
        for r in results:
            boxes = getattr(r, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                xyxy = box.xyxy[0].tolist()
                out.append(Detection(
                    bbox=(xyxy[0], xyxy[1], xyxy[2], xyxy[3]),
                    confidence=float(box.conf[0]),
                    cls=int(box.cls[0]),
                ))
        return out


class HogDetector:
    """OpenCV HOG people detector. No ball class, lower recall in crowds."""

    name = "hog"

    def __init__(self) -> None:
        import cv2

        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame) -> list[Detection]:
        rects, weights = self.hog.detectMultiScale(
            frame, winStride=(8, 8), padding=(8, 8), scale=1.05
        )
        return [
            Detection(bbox=(float(x), float(y), float(x + w), float(y + h)),
                      confidence=float(c), cls=CLASS_PLAYER)
            for (x, y, w, h), c in zip(rects, weights)
        ]


def available_backends() -> dict[str, bool]:
    backends = {}
    try:
        import ultralytics  # noqa: F401
        backends["yolo"] = True
    except Exception:
        backends["yolo"] = False
    try:
        import cv2  # noqa: F401
        backends["opencv"] = True
    except Exception:
        backends["opencv"] = False
    return backends


def build_detector(weights: str | None = None) -> Detector | None:
    """Return the best available detector, or ``None`` if the extras are missing."""
    caps = available_backends()
    if caps["yolo"]:
        try:
            return YoloDetector(weights or "yolov8m-football.pt")
        except Exception:
            pass
    if caps["opencv"]:
        try:
            return HogDetector()
        except Exception:
            pass
    return None
