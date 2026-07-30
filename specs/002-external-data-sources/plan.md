# Implementation Plan: External data sources — SoFIFA (EA FC 26) and StatsBomb open data

**Branch**: `002-external-data-sources` | **Date**: 2026-07-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-external-data-sources/spec.md`

## Summary

Add a fourth input route alongside CSV, by-hand and video: **import from a public
external source**. Two adapters sit behind one interface in a new
`app/services/external/` package, exposed through a new `/api/v1/external/*`
router and one new panel in the existing **Import data** tab.

* **SoFIFA** → EA FC 26 club squads. Implemented as a small, self-contained HTML
  reader over stdlib (`urllib` + `html.parser`), with its own rate limiter and
  on-disk cache. No new runtime dependency. Off unless enabled in configuration.
* **StatsBomb** → real fixtures, lineups and events, via the optional
  `statsbombpy` package. Absent, the source reports itself unavailable and the
  rest of the product is untouched.

Both adapters produce the same intermediate shapes (`SourceSquad`,
`SourceFixture`) so preview, error reporting and provenance are written once.
Every import goes preview-then-commit, mirroring the CSV route's contract.

## Technical Context

**Language/Version**: Python 3.12 (backend `.venv`), vanilla ES modules (frontend)

**Primary Dependencies**: FastAPI · SQLAlchemy 2 · Pydantic 2 (existing). New
*optional* extra: `statsbombpy>=1.22` in a new `backend/requirements-external.txt`.
**No new required dependency** — the SoFIFA reader uses only the standard library.

**Storage**: existing SQLite/SQLAlchemy schema, plus an on-disk HTTP response
cache under a configurable directory (default `data/external-cache/`, gitignored).

**Testing**: pytest. Every test in this feature runs **offline**, against stored
fixtures under `backend/tests/fixtures/external/`. No test performs a network
request; no test requires `statsbombpy` to be installed.

**Target Platform**: same single-process uvicorn deployment; Windows-first dev.

**Project Type**: web service with a static frontend served by the API.

**Performance Goals**: a club preview completes in under 5 s warm (cache hit) and
under 30 s cold; a StatsBomb fixture import of ~3,500 events commits in under 20 s.

**Constraints**: one outbound request per second per host maximum, with a
configurable ceiling; responses cached with their fetch timestamp; fetching is
**off by default** and names the hosts it will contact when enabled.

**Scale/Scope**: ~1,100 new backend lines across 8 new modules, ~350 frontend
lines in one new view, ~450 test lines, and edits to 6 existing files.

## Constitution Check

*GATE: checked before design, re-checked after.*

| Principle | How this design satisfies it |
|---|---|
| **I · Honesty by Construction** | The feature's central risk is presenting a *video-game rating* as a measurement. Mitigated structurally: (a) provenance is a column, not a comment — see below; (b) `overall_rating` becomes nullable so "the source published no rating" is representable instead of defaulting to 70; (c) the UI labels imported attributes as third-party game ratings wherever they drive a recommendation (FR-014); (d) the simulator distinguishes squad-on-file / imported-real / generated (FR-015). |
| **II · Nothing Imputed Across Tiers** | SoFIFA is Tier 1 and StatsBomb is Tier 2. Neither writes into fields belonging to a tier it does not supply: an imported squad leaves `minutes_last_7d`, `fitness`, `fatigue` and season stats **absent**, and an imported fixture leaves tracking-only metrics `null`. Asserted by test, not by convention. |
| **III · Tenant Isolation Is Structural** | Every write goes through `TenantScope`; no new query is built directly. `test_external.py` asserts 404-not-403 for an imported team read by another tenant, alongside the existing isolation tests. |
| **IV · Provenance Is Tracked and Visible** | New `provenance` JSON column on `Team`, `Player` and `Match`, populated on every imported row with source, edition/competition, source row id, fetch-or-file timestamp, and retrieval mode. Surfaced in the API responses and rendered in the UI. This is the load-bearing piece of the whole feature. |
| **V · Tests Assert the Real Contract** | Tests assert absence (not defaults), tenant 404, honest degradation with the dependency uninstalled, and identical results between a live-shaped fetch and a file import. No existing assertion is deleted. |
| **Optional dependencies degrade honestly** | Follows the established CV-extras pattern: `capabilities()` per source, reported at `/api/v1/external/sources` and in the UI, naming the reason and the remedy. |

**Result: PASS.** One item goes in Complexity Tracking (nullable rating).

## Project Structure

### Documentation (this feature)

```text
specs/002-external-data-sources/
├── spec.md              # written
├── plan.md              # this file
└── tasks.md             # /speckit-tasks output — not created here
```

### Source Code (repository root)

```text
backend/
├── requirements-external.txt          # NEW — statsbombpy, optional extra
├── app/
│   ├── core/config.py                 # EDIT — external fetch settings
│   ├── models/
│   │   ├── catalog.py                 # EDIT — Team.provenance, Player.provenance,
│   │   │                              #        nullable overall/potential rating
│   │   └── match.py                   # EDIT — Match.provenance
│   ├── schemas/
│   │   └── external.py                # NEW — request/response models
│   ├── api/v1/
│   │   ├── __init__.py                # EDIT — mount the router
│   │   └── external.py                # NEW — discovery, preview, commit
│   └── services/
│       ├── ml/features.py             # EDIT — unknown-rating handling
│       └── external/                  # NEW package
│           ├── __init__.py            #   registry + capabilities()
│           ├── base.py                #   SourceSquad/SourcePlayer/SourceFixture,
│           │                          #   Provenance, SourceUnavailable, FetchError
│           ├── http.py                #   rate-limited, disk-cached GET (stdlib)
│           ├── sofifa.py              #   search, squad fetch, HTML parse, file load
│           ├── sofifa_map.py          #   position + attribute mapping tables
│           ├── statsbomb.py           #   competitions/matches/lineups/events
│           └── commit.py              #   SourceSquad/SourceFixture → TenantScope writes
└── tests/
    ├── test_external.py               # NEW
    └── fixtures/external/             # NEW — saved HTML + JSON, no network
frontend/
├── index.html                         # EDIT — nav entry
└── js/views/external.js               # NEW — search, browse, preview, commit
docs/
├── DATA_MODEL.md                      # EDIT — external sources in the contract
└── EXTERNAL_SOURCES.md                # NEW — what each source is, ToS, setup
README.md                              # EDIT — the fourth route
```

**Structure Decision**: The existing layout already separates thin routes from
`services/`, and `services/ingest/` establishes the precedent that an input route
is a pure-function package over bytes with a thin committer above it. This
feature copies that shape exactly: `services/external/` is pure (fetch → parse →
`SourceSquad`), `commit.py` is the only module that touches `TenantScope`, and
`api/v1/external.py` stays thin.

## Phase 0 — Research findings

Recorded here rather than in a separate file; there are four findings and they
change the plan.

1. **There is no `sofifa` package on PyPI.** `pip install sofifa`,
   `sofifa-scraper`, `pysofifa` all 404. The only maintained option that includes
   a SoFIFA reader is `soccerdata` 1.9.1 — which pulls `seleniumbase`, `lxml`,
   `pandas`, `tqdm`, `rich` and a real browser. **Rejected**: a browser-automation
   stack is disproportionate for reading a static HTML table, and this project's
   constraint is a light runtime. Decision: ~200-line stdlib reader in
   `sofifa.py`.
2. **`statsbombpy` 1.22.0 is available** and its open-data path needs no
   credentials. Its API surface is `sb.competitions()`, `sb.matches(comp, season)`,
   `sb.lineups(match_id)`, `sb.events(match_id)`. It pulls `pandas`, `requests`,
   `requests-cache`, `inflect`, `joblib` — hence *optional extra*, not a core
   dependency.
3. **`statsbomb` is already a known provider frame** in
   `services/analytics/pitch.py` (`(120.0, 80.0, flipped=True)`). Event
   coordinates therefore convert through the existing `to_metres()` with no new
   geometry code — this is why Story 2 is much cheaper than it looks.
4. **SoFIFA's attribute vocabulary already matches this product's.** The 27
   detail attributes in `features.DETAIL_GROUPS`, the six GK attributes and the
   two work rates are the same vocabulary SoFIFA publishes, so `sofifa_map.py` is
   a naming table, not a modelling exercise. (The two attributes that spec 001
   adds — attacking positioning and dribbling-in-motion — are also SoFIFA's; if
   001 lands first this feature picks them up for free.)

### Known limitation, stated up front

The SoFIFA HTML selectors cannot be verified from this environment — sofifa.com's
`robots.txt` disallows the AI crawler this assistant would fetch as, so no live
page was retrieved while writing this plan. The parser is therefore written
defensively (it reports what it *expected to find* and refuses rather than
guessing, per FR-018), ships with a synthetic fixture, and ships with a probe
command:

```bash
python -m app.services.external.sofifa --probe "https://sofifa.com/team/241"
```

which prints what it parsed and what it failed to find. **One live run is needed
to confirm the selectors**, and any correction is confined to `sofifa.py`.
Everything else in the feature — mapping, preview, commit, provenance, tests —
is exercised by the file-import path (FR-020) and does not depend on it.

## Phase 1 — Design

### Data model changes

| Table | Change | Why |
|---|---|---|
| `teams` | `+ provenance JSON default {}` | FR-013 |
| `players` | `+ provenance JSON default {}` | FR-013 |
| `players` | `overall_rating`, `potential_rating` → **nullable** | FR-008 — a StatsBomb lineup publishes no rating, and 70.0 is a lie |
| `matches` | `+ provenance JSON default {}` | FR-013 |
| `match_events` | none — the source event id goes in the existing `qualifiers` JSON | avoids a column for one adapter |

Provenance shape (identical across tables):

```json
{
  "source": "sofifa",
  "edition": "EA FC 26",
  "source_id": "241",
  "source_url": "https://sofifa.com/team/241",
  "retrieved": "fetch",
  "retrieved_at": "2026-07-31T10:04:11Z",
  "note": "Game ratings published by EA Sports, not measurements."
}
```

The project has no migration tooling (`init_db` creates tables directly), so
existing databases must be reseeded. Called out in the release notes per the
spec's Assumptions.

### Unknown ratings — the one invasive change

`features.attribute()` currently ends its fallback chain at
`overall_rating` (default 70.0). With a nullable rating that chain can end in
`None`, so:

* `features.rating(player)` returns `float | None`, and `features.rating_or()`
  makes the fallback explicit at every call site (deliberately not
  `rating(p) or default`, which would swap a genuine 0.0 for the default).
* `features.is_rankable(player)` — true when a rating exists — and
  `features.split_rankable(players)`, which returns the rankable players plus an
  `excluded` report naming who was dropped and why.
* `lineup_optimizer.best_xi`, `substitution.recommend_substitutions`,
  `transfer.detect_needs`, `academy.first_team_bar` and the simulator's
  `_setup_from_team` **exclude** unrankable players instead of ranking a
  fabricated 70. `XIAssignment` gained an `excluded` field so a thin XI is
  explained rather than merely small.

**As built, `is_rankable` is simply "has a rating"**, not the "rating *or* six
headline attributes" the design first sketched. Deriving a rating from headline
faces would have been a second, subtler kind of invention for a case that cannot
currently arise — the CSV route requires `overall_rating`, and SoFIFA always
publishes it — so the simpler rule is both more honest and smaller.

Blast radius is contained because unknown ratings only ever arise from StatsBomb
lineup players, which are attached to `kind="opponent"` teams and never enter
best XI or the simulator in v1. The exclusion path is belt-and-braces, and it is
what makes FR-008 true rather than aspirational.

### Source interface

```python
# services/external/base.py
@dataclass
class SourcePlayer:      # pre-mapping, as published
    source_id: str; name: str; position_raw: str
    shirt_number: int | None; age: int | None; birth_date: date | None
    nationality: str | None; overall: int | None; potential: int | None
    attributes_raw: dict[str, int]; foot: str | None
    height_cm: int | None; weight_kg: int | None
    market_value_eur: int | None; wage_eur_per_year: int | None
    contract_until: date | None

@dataclass
class SourceSquad:
    source_id: str; name: str; league: str | None; country: str | None
    formation: str | None; players: list[SourcePlayer]; provenance: dict

@dataclass
class SourceFixture:
    source_id: str; competition: str; season: str; kickoff: datetime
    home: str; away: str; score: tuple[int, int]
    lineups: dict[str, list[SourcePlayer]]
    events: list[SourceEvent]; provenance: dict
```

Errors: `SourceUnavailable` (dependency missing / fetching disabled) → HTTP 503
with the remedy; `FetchError` (network, timeout, unrecognised markup) → HTTP 502
naming source, URL and what was expected. Neither ever writes.

### Mapping and preview

`sofifa_map.py` holds two tables: SoFIFA position codes → `Position`, and SoFIFA
attribute labels → `ATTRIBUTE_KEYS`. Both are exact-match only. An unknown
position makes that row **unmappable** (reported with the offending value, never
guessed); an unknown attribute is reported and dropped.

Preview returns the same shape the CSV route returns — mapped rows, fields found,
fields absent, per-row errors — so `ingest.js`'s existing preview rendering is
reused rather than reinvented.

### API contract

```
GET  /api/v1/external/sources                      → availability, tier, description
GET  /api/v1/external/sofifa/clubs?q=              → candidate clubs
POST /api/v1/external/sofifa/preview               → SourceSquad, mapped
POST /api/v1/external/sofifa/commit                → team + players (201)
POST /api/v1/external/sofifa/preview-file          → same, from an uploaded file
POST /api/v1/external/sofifa/commit-file           → same, from an uploaded file
GET  /api/v1/external/statsbomb/competitions       → competitions + seasons
GET  /api/v1/external/statsbomb/matches?...        → fixtures
POST /api/v1/external/statsbomb/preview            → SourceFixture summary
POST /api/v1/external/statsbomb/commit             → match + lineups + events (201)
```

Writes require the existing `squad:write` capability; reads require
authentication. `GET /external/sources` is readable without a capability so the
UI can render the panel's state.

### Frontend

One new view, `frontend/js/views/external.js`, reached from the **Import data**
tab: source picker → search/browse → preview table → commit. Vanilla ES module,
no build step, reusing `ui.js` helpers and `ingest.js`'s preview table. It
renders each source's availability, the provenance banner, and the "game ratings,
not measurements" label required by FR-014.

## Phase 2 — Task shape (for `/speckit-tasks`)

Ordered so each block is independently testable and Story 1 ships alone:

1. **Foundation** — model columns + nullable rating + `features` unknown handling
   + config settings + `requirements-external.txt`. Existing suite must stay green.
2. **Story 1 (P1)** — `base.py`, `http.py`, `sofifa_map.py`, `sofifa.py`,
   `commit.py` squad path, routes, fixtures, tests, frontend panel.
3. **Story 3 (P2)** — file-import path and the unavailable/failure behaviours.
   Deliberately built with Story 1, since it is what makes Story 1 testable offline.
4. **Story 2 (P2)** — `statsbomb.py`, fixture commit path, routes, tests,
   frontend browse.
5. **Story 4 (P3)** — re-import matching on `source_id`, departed-player reporting.
6. **Docs** — `docs/EXTERNAL_SOURCES.md`, `docs/DATA_MODEL.md`, README.

## Found while building — outside this feature's scope

**`time_possession_pct` is computed from event data, contradicting the README.**
The README's opening paragraph and the constitution's second principle both use
this exact field as the example: *"a report built from event data alone reports
`time_possession_pct` as `null` rather than guessing it."* It does not.
`analytics/possession.py:possession_from_events()` estimates it by summing the
gaps between consecutive events, capped at 12 seconds each, and the only test
guarding the field (`test_possession_on_empty_input_returns_nulls_not_zeros`)
covers *empty* input, not event-only input.

This predates this feature and was found because Story 2's acceptance criterion
asserts exactly that behaviour. It is left unchanged here: silently altering a
core analytics engine under cover of a data-source feature is precisely the kind
of change the constitution asks to be called out rather than assumed. The test
`test_an_imported_fixture_analyses_at_tier_two_and_no_higher` asserts the tier
boundary that *is* enforced — `method == "events"`, no `tracking` in
`inputs_used`, and `data_completeness < 1` — and this note is the deliberate
record of the gap. **It needs its own decision: either the estimate is defensible
and the README should describe it honestly, or the field should be `null` from
events and the docs are right.**

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `Player.overall_rating` becomes nullable, touching four engines | FR-008 and Principle I: StatsBomb publishes no rating, and the current default of 70.0 would silently invent one for every imported lineup player | Keeping the default and adding a `rating_is_known` flag was rejected — it leaves the lie in the column and every future reader has to remember to check the flag. Nullability makes the type system enforce what the constitution requires. |
| A hand-written HTML reader instead of a maintained library | `soccerdata` is the only maintained option and pulls a browser-automation stack (`seleniumbase`) plus pandas/lxml into a runtime whose constraint is to stay light | Rejected on dependency weight, not capability. The risk it trades into — markup drift — is mitigated by FR-018 (refuse, never guess), FR-020 (file import works without the fetcher) and the `--probe` command. |
