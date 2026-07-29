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

137 tests in three files:

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

The suite runs against a throwaway SQLite database seeded once per session.
