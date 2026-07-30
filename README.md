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
The API docs are at `/docs`. The backend serves the frontend as static files, so
this one process is the whole dashboard — there is no separate frontend server.

On Windows you can skip the manual steps and run **`script.bat`** from the repo
root: it creates a virtualenv, installs dependencies, seeds the demo database
(first run only), starts uvicorn and opens the dashboard.

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
cd backend && python -m pytest         # 205 tests
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

### Getting data in

Three routes, all in the **Import data** tab:

* **CSV** — six datasets (squad, market pool, academy players, academy
  assessments, match events, tracking). Headers are matched case-insensitively
  through a table of aliases (`fullname`, `pos`, `dob`, `ovr`), the delimiter is
  sniffed, and every file is previewed before it is written: column mapping,
  per-row errors with the offending value, and the first rows as they would be
  stored. A file with any bad row is refused unless you explicitly opt into a
  partial import. Templates download from the same panel.
* **By hand** — a full player editor (identity, contract, load, all 41
  attributes), club budgets, and team creation.
* **Video** — upload footage; see [docs/CV.md](docs/CV.md).

### Player attributes

41 keys on a 0-99 scale, in three layers:

* **Six headline faces** — pace, shooting, passing, dribbling, defending,
  physical. This is the minimum, and everything works with only these.
* **27 detail attributes** grouped under them — finishing, short passing,
  standing tackle, acceleration, and so on. A missing detail falls back to *its
  headline group*, not to the player's overall rating, so a partial profile
  still produces useful positional fit.
* **Six goalkeeping attributes** — diving, handling, kicking, reflexes,
  positioning, speed. Goalkeeping is a different sport; judging a keeper on
  outfield faces made every goalkeeping decision guesswork.

Plus two work rates. `GET /api/v1/meta/reference` publishes the full vocabulary
and the per-position weights that turn it into positional fit.

---

## Architecture

```
backend/app/
├── core/            config, JWT auth, tenant scoping
├── db/              SQLAlchemy models, session, demo seed
├── api/v1/          auth · squad · matches · analysis · transfers · academy
│                    · ingest · simulation · meta
├── schemas/         Pydantic request/response models
└── services/
    ├── analytics/   pitch geometry · xG/xT · possession · formation · tactics · heatmaps
    ├── ml/          features · substitutions · best XI · transfers · academy · registry
    ├── cv/          detector · tracker · homography · kit classifier · simulator
    ├── simulation/  the match engine behind the Match sim tab
    ├── ingest/      CSV parsing and validation, as pure functions over bytes
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

**Match simulation.** Pick your side and an opponent — a second squad on file, or
a stand-in generated at a level you choose — and watch the fixture play out:
twenty-two players moving, a live clock and score, condition draining player by
player, and substitutions arriving at the usual windows when someone is spent.

Three playback speeds: **instant**, **30 seconds**, or **four minutes** for the
full ninety. All three return the same data; speed is a client-side decision, so
switching costs nothing.

Every outcome comes from the players actually on the pitch. A pass completes
according to the passer's short or long passing, his current condition, and the
pressure from the nearest opponent weighted by that opponent's defensive
awareness. A shot is gated on the shooter's role and the distance he would strike
from, and converts according to its own xG scaled by his finishing — so the
scoreline and the xG the app reports stay on the same scale.

A finished fixture can be stored as an ordinary match and fed straight into the
analysis pipeline, labelled as a simulation throughout. Same seed, same match.

The engine's calibration — and the one place it is knowingly unrealistic, shot
distribution across a side — is documented in [docs/MODELS.md](docs/MODELS.md).

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
- A simulated fixture is stored as a simulation — competition, provider and match
  notes all say so — and a generated opponent is reported as generated, in the
  API response and on screen, rather than passed off as a squad on file.
- Where a model is knowingly unrealistic, the docs say so and the test asserts a
  ceiling rather than pretending the behaviour is correct.
- Every recommendation carries the numbers behind it under `evidence`.

## Developer tooling

Two tools support how work is planned and understood here. Neither is needed to
run the API; both are listed under **Developer tooling** in
`backend/requirements.txt`, and both are best installed in isolation with
`uv tool install` / `uvx` rather than into a production image.

### Spec-driven development — GitHub Spec Kit

Non-trivial features start from a spec, not from code. [Spec Kit](https://github.com/github/spec-kit)
is initialised in `.specify/` (templates, scripts, and the project
**constitution** in `.specify/memory/constitution.md`), and drives the work
through `/speckit-*` skills in your coding agent:

```
/speckit-constitution   # establish or amend the project principles
/speckit-specify        # write the feature specification
/speckit-plan           # turn the spec into an implementation plan
/speckit-tasks          # break the plan into actionable tasks
/speckit-implement      # execute the tasks
```

The constitution encodes this project's non-negotiables — honesty by
construction, no imputation across data tiers, structural tenant isolation, and
tracked provenance — so specs and plans are checked against them rather than
against taste. Re-initialise or update with:

```bash
uvx --from git+https://github.com/github/spec-kit.git specify init --here --integration claude
```

### Codebase knowledge graph — graphify

The whole codebase is available as a navigable knowledge graph, so you can trace
relationships before touching cross-cutting code. Built with
[graphify](https://github.com/safishamsi/graphify) over `backend/`, `frontend/`
and `docs/`, the outputs live in `graphify-out/`:

* `graph.html` — the interactive graph, open in any browser (no server).
* `GRAPH_REPORT.md` — audit report: god nodes, communities, surprising links.
* `graph.json` — the raw graph (GraphRAG-ready).

The current graph is **1,220 nodes · 3,341 edges across 59 communities** (a
structural AST pass over the code plus a semantic pass over the docs). Its most
connected abstractions are exactly the ones the architecture leans on —
`Position`, `Pitch`, and the `TenantScoped` mixin. Rebuild or query it via the
`/graphify` skill:

```
/graphify                       # rebuild the graph for the current directory
/graphify query "how does cross-tenant access return 404?"
```

## Licence and data

The demo squad, ratings, prices and physical values are **synthetic** — invented
for the demo, not scouting data about real people.
