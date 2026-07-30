# Architecture

## Request path

```
HTTP → deps.get_current_principal  (JWT or X-API-Key → TenantContext)
     → TenantScope                 (the only way to query a scoped model)
     → router                      (validation, plan/role guards)
     → orchestrator.analyse()      (routes inputs → models)
     → AnalysisReport + Recommendation rows
```

`orchestrator.analyse()` is the single place that decides *what can be computed*
from *what arrived*. Routers gather data and persist results; they never decide
which model runs.

## Multitenancy

Shared schema, row-level discrimination. Every business table mixes in
`TenantScoped`, which adds an indexed `tenant_id` foreign key.

The enforcement mechanism is `app/core/tenancy.py`. Routes do not call
`db.query(...)`; they call `scope.all(Model, ...)`, `scope.get(...)`,
`scope.add(...)`. `TenantScope.select()` appends the tenant predicate for any
model that is `TenantScoped`, and `add()` refuses to write an object already
owned by a different tenant.

Two deliberate choices:

- **Another tenant's object is a 404, not a 403.** A 403 confirms the id exists.
  `CrossTenantAccess` is caught in `main.py` and rendered as a bare 404.
- **The tenant comes from the credential, never from a header** — except that a
  platform superuser may *narrow* to another tenant via `X-Tenant`. A normal
  user's header is ignored.

Roles map to capability sets (`ROLE_CAPABILITIES`); plans cap teams, users and
video minutes (`PLAN_LIMITS`).

## Storage shape

| Data | Shape | Why |
|---|---|---|
| Events | one row per action, indexed on `(match_id, minute, second)` and `(match_id, type)` | queried by window and by type |
| Tracking | one row per **frame**, positions as JSON | a whole frame is read at once and never queried per-player; per-player rows would multiply write volume 22× for no read benefit |
| Reports | large numeric blocks as JSON columns | read whole by the UI, never filtered field-by-field |

Tracking is decimated to 5 Hz on ingest. SQLite is the default so the demo runs
with no services; `ELEVENMETRIC_DATABASE_URL` switches to Postgres unchanged.

## Analytics layer

`services/analytics/` has no knowledge of the database. Every function takes
duck-typed rows — anything with `type`, `x`, `y`, `minute`… — which is why the
simulator's `SimEvent` and the ORM's `MatchEvent` are interchangeable, and why
the analytics tests run without a database.

- **`pitch.py`** — geometry, provider frame conversion, zone grids.
- **`metrics.py`** — xG (logistic on distance and visible angle) and xT (value
  iteration over a shoot / move / lose-possession MDP). The turnover term is
  what makes deep positions genuinely less valuable; without it the surface
  converges almost flat.
- **`possession.py`** — three possession definitions, because they disagree and
  the disagreement is the insight: time, pass share, and field tilt.
- **`formation.py`** — deterministic 1-D k-means on average x, choosing between
  3 and 4 lines by inertia with a penalty for the extra line.
- **`tactics.py`** — 0-100 indices, each paired with the raw quantity it came
  from, plus named vulnerabilities carrying their own evidence.
- **`heatmap.py`** — KDE for sparse event data, histogram + blur for dense
  tracking. A histogram of 40 touches is mostly noise.

## ML layer

`services/ml/` — see [MODELS.md](MODELS.md) for what each model does.

Two design rules:

- **Scale discipline.** `overall_rating` is *current level*. The age curve is a
  projection tool and is never used to discount it — doing both double-counts
  age and undervalues everyone not exactly at their peak. Positional fit is
  applied as a *damped* penalty (`effective_level`), because multiplying a
  rating by a 0.9 fit over-punishes routine compromises.
- **Bootstrap priors, not magic numbers.** Each model is fitted on an explicit
  generative process and reports `provenance: "bootstrap"`.
  `registry.fit_from_dataset()` replaces it with club observations.

## Computer vision

`services/cv/` degrades in three tiers: ultralytics YOLO → OpenCV HOG →
labelled simulation. The tier that ran is recorded on the job, echoed in the
report summary, and shown as a banner in the UI.

The pipeline is decode → detect → track → calibrate → project → classify kits.
Notable choices:

- **Foot points, not box centres**, are projected: only the point on the pitch
  plane survives the homography correctly.
- **The homography is re-estimated continuously.** A broadcast camera pans and
  zooms constantly; a stale matrix corrupts every distance silently.
- **Torso-only kit sampling in Lab space.** Including shorts and socks blurs
  kits that differ only above the waist; including grass makes everyone green.
  Lab distance means a threshold set on one fixture transfers to another.

## Match simulation

`services/simulation/engine.py` plays a fixture at 5 Hz and returns it in the
same shapes the rest of the app already consumes — `SimEvent` is duck-type
compatible with `MatchEvent`, `SimFrame` with `TrackingFrame` — so
`/simulation/run` can persist a simulated fixture as an ordinary `Match` and the
analysis pipeline reads it back with no special case. The stored match is labelled
`competition="Simulation"`, `provider="elevenmetric-sim"`, and its notes say
plainly that it is not a record of a real match.

Two decisions shape the API:

- **All the work happens in one request.** Playback speed — instant, 30 s, or four
  minutes — is entirely a client-side decision, so all three modes return the same
  payload. Streaming would add a socket and a session for no gain.
- **Positions ship as a flat integer stream.** One array per frame,
  `[clock_s, x0, y0, …, ball_x, ball_y, possession]` against a fixed roster order,
  quantised to half-metres. The object-per-player-per-frame form is ~6× larger
  over the wire for exactly the same numbers; a 90-minute fixture is ~600 KB
  rather than 1.5 MB.

The roster describes each slot **at kick-off**, and substitutions carry their
`slot_index`, which is what lets the client relabel a token at the right minute
and lets you scrub backwards to the original XI. See [MODELS.md](MODELS.md) for
the engine's calibration and its one known limitation.

## Ingestion

`services/ingest/csv_ingest.py` is deliberately split from the API: parsing and
validation are pure functions over bytes, which is what makes a preview
endpoint possible at all — the same code path runs whether or not anything will
be written.

The parser is tolerant on shape (sniffed delimiter, alias-matched headers,
several date formats) and strict on meaning (an unknown column is reported, a
bad value fails its own row with the value quoted, nothing is coerced silently).
Commit refuses a file with any invalid row unless the caller opts into a partial
import: a half-imported squad is worse than none.

Re-importing a squad updates by name rather than duplicating, so a club can
treat the CSV as the source of truth and re-upload it.

## Frontend

Vanilla ES modules — no build step, no runtime dependencies, served as static
files by the same process. `charts.js` holds the primitives; each tab is a
module exporting `render(root, state)`.

Visualisation rules held throughout: one y-axis ever; at most three categorical
series in fixed slot order, never recoloured when a filter changes the count;
sequential blue for magnitude and diverging blue↔red with a neutral midpoint for
signed values; a legend whenever there is more than one series plus selective
direct labels, so colour never carries meaning alone; and a table view beside
every chart.

## Testing

205 tests in five files:

- `test_analytics.py` — geometry, provider conversion, xG/xT monotonicity,
  possession definitions, formation recovery, heatmap invariants, and the
  simulator's own realism (pass completion inside a plausible band, coordinates
  on the pitch, determinism).
- `test_ml.py` — positional-fit ordering, fatigue behaviour, exact assignment
  against brute force, substitution constraints, budget constraints, academy
  corrections.
- `test_api.py` — auth, **tenant isolation** (404-not-403 on four surfaces),
  ingest conversion and decimation, analysis output shape, role enforcement,
  API-key lifecycle.
- `test_ingest.py` — the attribute vocabulary, positional weights, header alias
  matching, per-row validation, and the preview/commit contract.
- `test_simulation.py` — the match engine: determinism, every realism band in
  [MODELS.md](MODELS.md), substitution bookkeeping, and playback stream shape.

Most of `test_simulation.py` exists because a plausible-looking simulation is
worse than none: the tests are the bands, and each one that pins a past bug says
in its docstring what the bug looked like. A few deliberately assert a *ceiling*
rather than realism — `test_the_forward_does_not_take_almost_every_shot` is a
regression guard on a limitation, not a claim that the behaviour is right.

The suite runs against a throwaway SQLite database seeded once per session.
