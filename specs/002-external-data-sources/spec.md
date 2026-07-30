# Feature Specification: External data sources — SoFIFA (EA FC 26) and StatsBomb open data

**Feature Branch**: `002-external-data-sources`

**Created**: 2026-07-31

**Status**: Draft

**Input**: User description: "Integrar SoFIFA Scraper / API Wrapper (Python) y statsbombpy para las alineaciones y equipos de FC 26, así el usuario puede elegir equipos reales."

## Context

Today a club can only get data into ElevenMetric through routes it supplies
itself: a CSV export, the by-hand editor, or a video upload. Every one of those
starts from an empty database, so the first thing a new user meets is a demo
squad of invented players. The match simulator makes this sharpest: to face
anything other than a second squad already on file, the user gets a *generated*
opponent at a chosen strength — honest, but not a real team.

This feature adds a fourth route: **import a real team from a public source.**

Two sources are in scope, and they answer different questions. Conflating them
is the main risk this spec exists to prevent:

| Source | What it actually is | What it gives ElevenMetric |
|---|---|---|
| **SoFIFA** | Ratings from the EA Sports FC video game (currently **EA FC 26**), published per club and player | Real club names and squads, with attributes on a 0-99 scale that already match this product's vocabulary almost 1:1 — so best XI, positional fit, substitutions, transfers and the simulator all work immediately |
| **StatsBomb open data** (`statsbombpy`) | Free, publicly released **real match data** — competitions, fixtures, lineups, and on-ball events | Real fixtures with real lineups and a tier-2 event feed, which unlocks possession, field tilt, PPDA, xG, xT and the tactical profile for matches that actually happened |

SoFIFA is what "elegir equipos reales" means in the simulator: pick Real Madrid,
get eleven named players with attributes. StatsBomb is what makes the *analysis*
pipeline run on something other than a club's own upload.

Neither source is a measurement of the club's own players, and the constitution
(Principle I, Honesty by Construction; Principle IV, Provenance Is Tracked and
Visible) forbids presenting either as one. A game rating is an opinion published
by EA, not an observation; a StatsBomb lineup carries no player ratings at all.
The design below makes both facts structural rather than a disclaimer.

### Relationship to the data tiers

Neither source introduces a new tier. SoFIFA lands squarely in **Tier 1 ·
Squad** (it supplies attributes but *no* minutes, no load, no season stats).
StatsBomb lands in **Tier 2 · Events**. Principle II applies unchanged: a squad
imported from SoFIFA reports `minutes_last_7d`, `fitness` and season statistics
as absent, never as a plausible-looking default.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Import a real club's squad and play it in the simulator (Priority: P1)

A user opens **Import data**, chooses the external-source route, searches for a
club by name, and sees the matching clubs with their league and country. They
pick one, see a preview of the squad exactly as it would be stored — every
player, position, shirt number, age, and the attributes that were found — and
commit it. The club becomes a team on file with its players, and is immediately
selectable as the opponent (or as their own side) in the match simulator.

**Why this priority**: This is the request. It is also the smallest slice that
delivers standalone value: with only this story shipped, a user who has typed in
nothing at all can watch two real teams play each other, and the best XI,
positional-fit, substitution and transfer engines all have real input.

**Independent Test**: Search a club, preview it, commit it, then start a
simulation against it. Verify the eleven names and shirt numbers on the pitch
match the imported squad, and that the report labels the opponent's origin.

**Acceptance Scenarios**:

1. **Given** the external-source panel, **When** a user searches a club by name,
   **Then** matching clubs are listed with league, country and squad size, and
   nothing is written to the database.
2. **Given** a club in the search results, **When** the user requests a preview,
   **Then** the full squad is shown as it would be stored — including which
   attributes were found and which are absent — and still nothing is written.
3. **Given** a previewed squad, **When** the user commits it, **Then** a team and
   its players exist on file, scoped to that user's tenant only, each carrying a
   record of where the data came from and when it was fetched.
4. **Given** an imported club, **When** the user starts a simulation against it,
   **Then** the fixture runs with those players and the response identifies the
   opponent as an imported real squad rather than a generated one.
5. **Given** an imported club, **When** a report or player profile is read,
   **Then** the fields the source never supplied (minutes played, fitness,
   fatigue, market value where absent) are reported as absent, not as defaults.

---

### User Story 2 - Import a real fixture's lineups and events (Priority: P2)

A user browses the StatsBomb open-data competitions, picks a season, picks a
fixture, and imports it. The two lineups and the full event feed are stored
against a match, which then runs through the existing analysis pipeline and
produces possession, field tilt, PPDA, xG, xT, heatmaps and a tactical profile.

**Why this priority**: It is the only route in the product that produces a
tier-2 analysis without the user owning an event feed, which is the single
biggest gap between the demo and the product's actual claims. It depends on
nothing in Story 1 and can ship separately.

**Independent Test**: Import one open-data fixture, run the analysis endpoint on
it, and confirm the report reaches tier-2 completeness with metrics that are
non-null and within plausible ranges.

**Acceptance Scenarios**:

1. **Given** the external-source panel, **When** the user browses StatsBomb,
   **Then** the available competitions and seasons are listed, and selecting one
   lists its fixtures with date, teams and score.
2. **Given** a fixture, **When** the user imports it, **Then** a match exists
   with `source = event_data`, `provider = statsbomb`, both lineups, and one
   event row per on-ball action, with coordinates converted from the StatsBomb
   120x80 frame into metres.
3. **Given** an imported fixture, **When** the analysis is run, **Then** the
   report's `data_completeness` reflects tier 2 and tracking-only fields such as
   `time_possession_pct` remain `null`.
4. **Given** a StatsBomb lineup, **When** the players it creates are read,
   **Then** their attributes and overall rating are reported as **unknown** —
   the source publishes none — rather than defaulted to a plausible number.

---

### User Story 3 - Work offline, and survive the source changing (Priority: P2)

A user without network access, or whose environment blocks the external hosts,
can still import real squads from a downloaded source-format file, and every
part of the product that does not use external sources keeps working. When a
live fetch fails — network down, source markup changed, rate limit hit — the
product says so plainly and imports nothing.

**Why this priority**: Both sources are outside this project's control. A
scraper against a third-party site *will* break, and the constitution requires
optional dependencies and optional features to fail visibly rather than
silently. Without this story, a markup change turns into a half-imported squad.

**Independent Test**: With the external dependency uninstalled and the network
unavailable, start the API, open the panel, and confirm the source is reported
as unavailable with the reason; then import the same club from a local file and
confirm the result is identical to a live import.

**Acceptance Scenarios**:

1. **Given** the optional dependencies are not installed, **When** the API
   starts, **Then** it starts normally and the external-source panel reports
   each source as unavailable, naming what to install.
2. **Given** a live fetch that fails or returns markup the parser does not
   recognise, **When** a user previews or commits, **Then** the error names the
   source, the URL and what was expected, and no partial team is written.
3. **Given** a source-format file on disk, **When** the user imports from it,
   **Then** the resulting team and players are identical to a successful live
   import of the same club, and the provenance records the file rather than a
   fetch.

---

### User Story 4 - Refresh an imported squad without duplicating it (Priority: P3)

A user who imported a club earlier re-imports it after a roster update. Players
already on file are updated in place; players no longer in the source squad are
flagged rather than deleted; the previous provenance is replaced by the new one.

**Why this priority**: Valuable but not needed for the first useful version, and
the CSV route already establishes the expected behaviour (re-importing a squad
refreshes it rather than duplicating it), so this is consistency work.

**Independent Test**: Import a club twice and confirm the player count does not
double and that changed attributes take the newer value.

**Acceptance Scenarios**:

1. **Given** a club already imported, **When** it is imported again, **Then**
   matched players are updated and the team's player count does not double.
2. **Given** a player present in the first import but absent from the second,
   **When** the second import commits, **Then** that player is reported as no
   longer in the source squad and is not silently deleted.

---

### Edge Cases

- **A club has fewer than eleven players in the source.** The import succeeds and
  reports the squad size; the simulator continues to refuse a side with fewer
  than eleven available players, with the existing message.
- **A source position has no equivalent in this product's vocabulary.** The row
  is reported as unmappable with the offending value, exactly as the CSV route
  reports an unknown position — it is never coerced to the nearest guess.
- **A player has no birth date, or only an age.** Age-derived output (academy
  projections, the fatigue curve's age term) must degrade to its no-age branch
  rather than assuming a birth year.
- **Two players in a squad share a name.** They are stored as distinct players;
  matching on re-import uses the source's own player identifier, not the name.
- **The source publishes an attribute this product does not have, or omits one it
  does.** Unknown source attributes are reported and dropped, never mapped by
  similarity. Missing attributes stay absent, and the existing headline-fallback
  rule applies unchanged.
- **The same club is imported by two different tenants.** Each tenant gets its
  own team and players; neither can see the other's, and requesting the other's
  by id returns 404.
- **A StatsBomb fixture has no events** (metadata-only release). The import
  reports it and either declines or stores lineups only, with the match left at
  its previous `source` tier rather than promoted to `event_data`.
- **A source is slow or rate-limits.** Requests are throttled and cached; a
  timeout is reported as a timeout, and no partial write occurs.
- **The source's terms or `robots.txt` disallow automated collection.** The
  feature is opt-in and off by default, and the product records and displays the
  source and fetch time for every imported row (see Assumptions).

## Requirements *(mandatory)*

### Functional Requirements

**Discovery and preview**

- **FR-001**: The system MUST publish which external sources exist, what each one
  supplies, which data tier it lands in, and whether it is currently available —
  as data, on the same footing as the existing input contract.
- **FR-002**: Users MUST be able to search SoFIFA for a club by name and see
  candidate clubs with league, country and squad size, without writing anything.
- **FR-003**: Users MUST be able to browse StatsBomb open-data competitions,
  seasons and fixtures, without writing anything.
- **FR-004**: Every import MUST be previewable before it is committed, showing
  the rows as they would be stored, the fields that were found, the fields that
  were absent, and any row that could not be mapped — matching the guarantee the
  CSV route already makes.
- **FR-005**: An import in which any row failed to map MUST be refused by
  default, and MUST require an explicit opt-in to import the valid rows only.

**Import — squads (SoFIFA)**

- **FR-006**: The system MUST create a team and its players from a source club,
  mapping the source's positions to this product's position vocabulary and its
  attributes to this product's attribute vocabulary.
- **FR-007**: Attributes the source does not supply MUST remain absent. The
  system MUST NOT write a default, an average, or a value derived from a
  different tier of data.
- **FR-008**: A player whose source record carries no overall rating MUST be
  stored with its rating recorded as **unknown**, and every consumer of a rating
  MUST handle unknown explicitly rather than receiving a default.
- **FR-009**: An imported team MUST be usable everywhere a team on file is usable
  — squad views, best XI, the simulator (as either side), and analysis.

**Import — fixtures (StatsBomb)**

- **FR-010**: The system MUST create a match from an open-data fixture with both
  lineups and one event row per on-ball action, converting coordinates from the
  source frame into the canonical metre frame using the existing provider-frame
  mechanism.
- **FR-011**: An imported fixture MUST be marked with its input tier and provider
  so the analysis pipeline treats it exactly as it treats a club's own event
  feed — no special-casing downstream.
- **FR-012**: Players created to satisfy a lineup MUST be attached to a team of
  kind `opponent` and MUST NOT be presented as the tenant's own squad.

**Provenance and honesty**

- **FR-013**: Every team, player, match and event created by this feature MUST
  carry a record of its source, what that source is (for SoFIFA: the game and
  edition; for StatsBomb: the competition and season), the fetch or file
  timestamp, and the source's own identifier for the row.
- **FR-014**: The UI MUST show that an imported squad's attributes are **game
  ratings published by a third party**, not measurements of the players, wherever
  those attributes drive a recommendation.
- **FR-015**: The simulator's response and the UI MUST distinguish three kinds of
  opponent: a squad on file, an imported real squad (naming the source), and a
  generated stand-in — the last of which is already labelled today.
- **FR-016**: Reports built on imported data MUST scale `confidence` by the same
  `data_completeness` rule as any other input; an imported squad MUST NOT raise
  completeness for tiers it does not supply.

**Availability and failure**

- **FR-017**: External-source support MUST be optional. With its dependencies
  absent the API MUST start normally, every existing route MUST behave
  identically, and the sources MUST report themselves unavailable with the
  reason and the remedy.
- **FR-018**: A failed fetch, an unrecognised source response, or a timeout MUST
  produce an error naming the source and what was expected, and MUST write
  nothing.
- **FR-019**: Live fetches MUST be rate-limited and cached locally, so a repeated
  preview does not repeat the request, and the cache entry MUST record when it
  was fetched.
- **FR-020**: Users MUST be able to import from a locally held source-format file
  and get a result identical to a live import of the same club, so the feature is
  usable offline and its tests need no network.
- **FR-021**: External fetching MUST be off unless explicitly enabled by
  configuration, and the configuration MUST state which hosts will be contacted.

**Isolation**

- **FR-022**: Every entity this feature creates MUST be tenant-scoped through the
  existing scope mechanism, and MUST return 404 — not 403 — to another tenant.

### Key Entities

- **External source**: A named, describable origin of data (`sofifa`,
  `statsbomb`). Knows what it supplies, which tier it lands in, whether it is
  currently available and why not, and what it needs to become available.
- **Source club / source squad**: A club as the source publishes it, and its
  players, before any mapping to this product's vocabulary. Exists so preview
  can show mapping decisions instead of hiding them.
- **Source fixture**: A real match as the source publishes it — competition,
  season, date, the two teams, the score — before import.
- **Provenance record**: Attached to every imported team, player, match and
  event. Names the source, the edition or competition/season, the fetch or file
  timestamp, the source's identifier for the row, and whether it came from a live
  fetch or a local file. It is the thing that makes Principle IV structural here
  rather than a convention.
- **Import preview**: The mapped rows, the fields found, the fields absent, and
  the rows that could not be mapped. Deliberately mirrors the CSV preview so the
  two routes make the same promise.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From a database with no data, a user can go from opening the app to
  watching two real, named clubs play a simulated fixture in under three minutes,
  with no file to prepare and nothing typed by hand beyond the club names.
- **SC-002**: 100% of entities created by this feature carry a provenance record
  that names the source and the fetch time; a spot check of any imported player
  traces back to its source row.
- **SC-003**: Every field the source does not supply is absent on read. Zero
  imported players carry an invented rating, minutes total, fitness or fatigue
  value.
- **SC-004**: An imported StatsBomb fixture produces an analysis whose tier-2
  metrics are all non-null and whose tracking-only metrics are all `null`.
- **SC-005**: With the optional dependencies uninstalled and no network, the full
  existing test suite passes unchanged, the API starts, and both sources report
  themselves unavailable with an actionable reason.
- **SC-006**: The feature's own tests run to completion with no network access,
  against stored source-format fixtures.
- **SC-007**: A second import of the same club changes the squad's player count
  by zero.
- **SC-008**: A tenant requesting another tenant's imported team by id receives
  404, asserted in the suite alongside the existing isolation tests.

## Assumptions

- **The two sources are not interchangeable, and the product will say so.**
  SoFIFA supplies game ratings for EA FC 26 squads; StatsBomb supplies real
  match data with no ratings. The UI names which is which at the point of
  choice, because a user who imports a StatsBomb lineup expecting attributes has
  been misled by the product, not by the source.
- **SoFIFA has no official API and no maintained PyPI wrapper.** `pip install
  sofifa` does not resolve. Access is therefore either an HTML fetch performed by
  this product or a source-format file supplied by the user. The spec requires
  both (FR-020) so the feature does not depend on a third party's markup staying
  still.
- **SoFIFA's `robots.txt` currently allows general crawling** (`User-agent: *`,
  `Allow: /`) while signalling `ai-train=no, use=reference` and disallowing a
  list of named AI crawlers. This feature's fetching is reference use by the
  user's own deployment, identified as such, rate-limited and cached. It is
  opt-in and off by default (FR-021), and the operator is responsible for their
  own compliance with the source's terms. The product records the source on
  every row so redistribution is never accidental.
- **StatsBomb open data is free to use under StatsBomb's user agreement, which
  requires attribution.** The product will carry that attribution wherever
  StatsBomb-derived output is shown, satisfying FR-013/FR-014 and the agreement
  at the same time.
- **Ratings from a video game are opinions, not measurements**, and the honest
  framing is that the product is analysing *the source's view of* a squad. All
  existing engines work unchanged on that basis; nothing downstream needs to know
  the ratings' origin except the labelling required by FR-014.
- **Scope boundary — v1 imports squads and fixtures only.** Transfer market pools,
  academy players and tracking data are not sourced externally in this feature.
  Historical season statistics for imported players are out of scope; they stay
  absent per FR-007.
- **Scope boundary — no redistribution.** The product imports into a tenant's own
  database for that tenant's analysis. Nothing in this feature exposes a bulk
  export of source data.
- **Existing databases will need reseeding** if representing "unknown rating"
  (FR-008) requires a schema change, since the project creates its schema
  directly and carries no migration tooling. This is acceptable for a product
  whose seed is a demo, and must be stated in the release notes.
