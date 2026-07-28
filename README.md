# ElevenMetric

Multi-tenant football analysis platform. It takes whatever a club actually has —
a teamsheet, an event feed, tracking data, or raw video — and returns tactical
findings plus concrete proposals: substitutions with timing, formation changes,
transfer targets inside a real budget, and academy promotion timelines.

```
┌──────────────┐   ┌───────────────┐   ┌──────────────┐   ┌────────────────┐
│  Squad only  │ → │  Event data   │ → │   Tracking   │ → │     Video      │
│  teamsheet   │   │  on-ball rows │   │  22 players  │   │  CV pipeline   │
└──────────────┘   └───────────────┘   └──────────────┘   └────────────────┘
   best XI          possession, xG,      time possession,     tracking from
   formation fit    xT, PPDA, press      true heatmaps,       footage, then
   workload         profile, subs        played shape         everything left
```

Capability scales with the data supplied, and **nothing is imputed across
tiers**: a report built from event data alone reports `time_possession_pct` as
`null` rather than guessing it, and every recommendation is scaled by the
report's `data_completeness`.

---

## Quick start

```bash
cd backend
pip install -r requirements.txt
python -m app.db.seed --reset          # demo club, squad, match, academy, market
uvicorn app.main:app --reload
```

Open <http://localhost:8000> and sign in with `owner@demo.fc` / `elevenmetric`.
The API docs are at `/docs`.

The seed creates two tenants (`demo-fc` and `rival-united`) so tenant isolation
is visible — and testable — from the first run.

### Optional: the computer-vision extras

```bash
pip install -r requirements-cv.txt     # opencv, ultralytics, scipy
```

Without them the API still runs; video jobs report `engine="simulated"` and the
UI says so in as many words. See [docs/CV.md](docs/CV.md).

### Tests

```bash
cd backend && python -m pytest         # 137 tests
```

---

## What data does it need?

This is the question the product answers about itself. The full contract lives
in [docs/DATA_MODEL.md](docs/DATA_MODEL.md), is served as JSON from
`GET /api/v1/meta/data-requirements`, and is rendered as a checklist in the
**Data** tab. `GET /api/v1/meta/data-readiness` scores a club's own data
against it and names what each missing feed would unlock.

The short version:

| Tier | Minimum input | What it unlocks |
|---|---|---|
| 1 · Squad | name, position, rating, minutes played | best XI, formation fit, fatigue and injury hazard, transfer needs, academy projections |
| 2 · Events | one row per on-ball action with `x`, `y`, `end_x`, `end_y` | possession, field tilt, PPDA, xG, xT, tactical profile, substitutions |
| 3 · Tracking | 22 positions + ball at 10-25 Hz | time possession, true heatmaps, played shape, compactness, line height |
| 4 · Video | MP4/MOV, ideally a fixed wide camera | tracking for clubs with no provider contract |
| 5 · Context | budgets, wage headroom, market pool | affordable signing plans, calibrated academy pathways |

---

## Architecture

```
backend/app/
├── core/            config, JWT auth, tenant scoping
├── db/              SQLAlchemy models, session, demo seed
├── api/v1/          auth · squad · matches · analysis · transfers · academy · meta
├── schemas/         Pydantic request/response models
└── services/
    ├── analytics/   pitch geometry · xG/xT · possession · formation · tactics · heatmaps
    ├── ml/          features · substitutions · best XI · transfers · academy · registry
    ├── cv/          detector · tracker · homography · kit classifier · simulator
    ├── orchestrator.py     routes inputs → models → a report with honest confidence
    └── data_requirements.py  the input contract, as data
frontend/            vanilla HTML/CSS/JS — no build step, no runtime dependencies
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design decisions and
[docs/MODELS.md](docs/MODELS.md) for what each model actually does.

### Multitenancy

Shared database, row-level tenant discrimination. Every business table carries
`tenant_id`, and routes never build queries directly — they go through
`TenantScope`, which is the only sanctioned way to construct a statement for a
scoped model and refuses to build one without binding the tenant.

An object belonging to another tenant returns **404, not 403**: a 403 would
confirm the id exists, and existence is itself private. `tests/test_api.py`
asserts this for players, matches, analyses and deletes.

Roles (`owner`, `analyst`, `scout`, `academy_coach`, `viewer`) map to
capabilities; plans (`starter`, `pro`, `elite`) cap teams, users and video
minutes.

---

## The engines

**Substitutions.** Answers who comes off, who comes on, and when. A
physiological fatigue curve (onset minute driven by stamina, age and minutes in
the last seven days) drives an in-match decline; a ridge model converts the
resulting level difference into expected goal difference over the remaining
minutes. Card risk, injury hazard and diagnosed tactical weaknesses are added
on top. Laws-of-the-game constraints — five substitutions across three windows —
are enforced.

**Best XI.** An exact linear assignment (Hungarian algorithm) of players to
slots, maximising effective level. Two modes: *today* (accumulated load applied
— who should start this weekend) and *raw quality* (who is our best XI). They
routinely disagree during a congested run, which is the point.

**Transfers.** Three stages: detect needs from the roster and from the match
analysis's vulnerabilities; score every market player on quality, positional
fit, value and risk; then solve a **two-dimensional knapsack** over fee and
annual wage bill to produce a signing plan the club can actually afford, rather
than a wishlist.

**Academy.** Projects months-to-first-team against the club's own bar (the 25th
percentile of the senior squad *at that position*). Corrects for biological age
(bio-banding — a late developer's output understates them) and flags the
relative age effect. Emits a pathway: promote now, train with the first team,
loan out, continue, review, or release.

**Computer vision.** Decode → detect → track → calibrate → project → classify
kits → derive events. The homography is re-estimated per frame because a
broadcast camera pans constantly and a stale matrix silently corrupts every
distance in the report.

---

## Honesty by construction

The system is designed so it cannot quietly overstate what it knows:

- Missing inputs produce `null`, never an estimate.
- Every report carries `data_completeness` and `confidence`; recommendations are
  scaled by both.
- Models ship as **bootstrap priors** — fitted on a documented generative
  process, not on real matches — and say so via `provenance` in the registry.
  `fit_from_dataset()` replaces them with club data.
- Simulated output is labelled `engine="simulated"` from the job record through
  to the report summary and the UI banner.
- Every recommendation carries the numbers behind it under `evidence`.

## Licence and data

The demo squad, ratings, prices and physical values are **synthetic** — invented
for the demo, not scouting data about real people.
