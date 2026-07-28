# Computer vision

The video path exists for clubs with no data-provider contract. It turns footage
into tracking frames, after which everything in the tracking tier applies.

```bash
pip install -r backend/requirements-cv.txt   # opencv, ultralytics, scipy
```

Check what the deployment can actually do:

```bash
curl localhost:8000/api/v1/video/capabilities
```

---

## Degradation, and why it is loud

Three tiers, resolved at import time:

| Engine | Requires | Quality |
|---|---|---|
| `yolo+bytetrack` | ultralytics + opencv | production path |
| `hog+bytetrack` | opencv only | coarse; people only, no ball |
| `simulated` | nothing | **not a measurement** |

When the extras are missing the API does not fail — it returns a labelled
simulation so the demo stays usable. That label is carried all the way through:
`AnalysisJob.engine`, a `warnings` entry on the report, the report summary text,
and a banner in the UI. Nothing produced this way should inform a decision, and
the system says so in those words.

---

## Pipeline

```
decode → detect → track → calibrate → project → classify kits → derive events
```

**Decode.** Sampled at a fixed rate (default 5 Hz). Decoding every frame costs
five times as much for no analytic gain.

**Detect** (`detector.py`). A football-tuned YOLO with classes
`ball / goalkeeper / player / referee`. Detections expose both a box centre and
a **foot point** — bottom-centre of the box. Only the foot point sits on the
pitch plane, so only it survives the homography correctly; projecting box
centres puts tall players metres out of position.

**Track** (`tracker.py`). Greedy association over a cost that blends IoU
(reliable when boxes are stable) with foot-point distance (reliable through
partial occlusion), plus velocity smoothing and a grace period for reclaiming a
lost track. Identity switches are the dominant error source in football
tracking — players occlude each other constantly — so both gating and the grace
period matter more than the association algorithm.

**Calibrate** (`homography.py`). Direct Linear Transform with Hartley
normalisation from pitch landmarks (line intersections, box corners, penalty
spots). Normalising the two point sets first is not optional: without it the DLT
is badly conditioned and the result drifts at the frame edges.

The homography is **re-estimated continuously**, and gated on reprojection error
(> 1.5 m is discarded) and staleness. A broadcast camera pans and zooms
constantly; a stale matrix corrupts every distance in the report silently, which
is the worst failure mode available.

`CalibrationState.update()` takes `{landmark_name: (px, py)}`. In production
that comes from a keypoint model; it is injectable so the pipeline can be tested
with a fixed calibration.

**Classify kits** (`team_id.py`). Crops the torso band — middle 50%
horizontally, 20-55% vertically — drops pitch-green pixels, converts to CIE Lab,
and clusters into two teams seeded on the two most distant samples.

Three choices worth stating:
- *Torso only*: including shorts and socks blurs kits that differ only above the
  waist; including background makes everyone green.
- *Lab space*: distances track perception, so a threshold set on one fixture
  transfers to another.
- *Outlier rejection*: samples beyond the distance threshold are returned as
  `None` rather than forced into a cluster — that is where keepers and referees
  land.

The report carries `kit_separation`. Below ~18 the two kits are too alike to
separate reliably, and the job says so rather than producing confident nonsense.

---

## What it cannot do

- **Shirt numbers, and therefore player identity.** Tracks are `t17`, not
  "Pedri", unless an OCR pass or a manual mapping is supplied. Team-level
  metrics are unaffected; per-player ones are not available.
- **Anything off-camera.** Broadcast footage shows roughly 60% of the pitch at
  any moment, so "all 22 players" is not achievable from it. A fixed wide
  tactical camera is worth far more than a higher-resolution broadcast feed.

`calibration_coverage` reports the fraction of sampled frames with a valid
homography. Below 50% the report warns that positional metrics from that clip
are low confidence.

---

## Practical guidance

| Situation | Recommendation |
|---|---|
| Club has a tactical camera | Use it. Calibration coverage is typically > 90% |
| Only broadcast footage | Expect gaps; analyse specific phases rather than whole matches |
| Kits are similar | Supply `home_kit_hex`; check `kit_separation` in the job |
| Long match, limited compute | Lower `sample_hz` to 3, or set `max_seconds` for a clip |
| You need player identity | Supply `known_lineup`, or accept team-level metrics only |
