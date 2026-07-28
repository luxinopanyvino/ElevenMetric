# The input contract

**What data does ElevenMetric need, and what does each piece buy you?**

This document is the human-readable twin of
`GET /api/v1/meta/data-requirements`, which serves the same content as JSON. The
**Data** tab renders it as a checklist, and
`GET /api/v1/meta/data-readiness` scores a club's actual data against it.

Two rules hold throughout:

1. **Tiers are cumulative.** Each adds capability; none replaces an earlier one.
2. **Nothing is imputed across tiers.** A metric whose input is absent is
   reported as `null`. It is never silently estimated.

---

## Coordinates

Everything downstream of ingest works in one frame:

> **metres, origin at the bottom-left corner, `x` along the *acting team's*
> attacking direction, `0 ≤ x ≤ 105`, `0 ≤ y ≤ 68`.**

So an opponent event at `x = 90` is 15 m from *their* attacking goal — that is,
deep in your half. Provider frames are converted on ingest:

| Provider | x range | y range | y flipped |
|---|---|---|---|
| `elevenmetric` | 0-105 | 0-68 | no |
| `statsbomb` | 0-120 | 0-80 | **yes** |
| `opta` | 0-100 | 0-100 | no |
| `wyscout` | 0-100 | 0-100 | no |
| `skillcorner` | 0-105 | 0-68 | no |
| `second_spectrum` | 0-105 | 0-68 | no |

An unknown `provider` is a hard `422`, never a silent pass-through — getting
this wrong corrupts every metric downstream in a way that looks plausible.

Second-half coordinates arrive mirrored. `flip_for_direction()` normalises them;
forgetting to is the classic route to a heatmap showing your striker defending
his own box.

---

## Tier 1 · Squad and lineup

A teamsheet and player attributes. No match data at all.

**Endpoint:** `POST /api/v1/players` (or `/players/bulk`)

| Field | Type | Req. | Why it matters |
|---|---|:--:|---|
| `name` | string | ● | — |
| `primary_position` | enum | ● | `GK RB RCB CB LCB LB RWB LWB DM CM AM RM LM RW LW CF ST SS` |
| `secondary_positions` | enum[] | | Raises positional fit; a versatile player is worth more to the optimiser |
| `birth_date` | date | | Without it every player is treated as 26 for ageing analysis |
| `preferred_foot` | enum | | `left` / `right` / `both`; feeds squad balance |
| `overall_rating` | 0-99 | ● | **Current** level. Any consistent internal scale works — it is used relatively |
| `potential_rating` | 0-99 | | Ceiling. Matters most under 23 |
| `attributes` | object | | `pace shooting passing dribbling defending physical stamina aerial composure vision work_rate_off work_rate_def`, each 0-99. Drives positional fit |
| `fitness` | 0-100 | | Current condition |
| `fatigue` | 0-100 | | Accumulated load |
| `minutes_last_7d` | int | | **The single highest-value load input** — it moves the fatigue-onset minute directly |
| `is_available` | bool | | Unavailable players are excluded from selection *and* from depth when detecting transfer needs |
| `market_value_eur`, `wage_eur_per_year`, `contract_until` | | | Transfer maths and contract-risk detection |

**Unlocks:** best XI for any formation (exact assignment), formation comparison,
out-of-position warnings, squad balance, fatigue and injury-hazard modelling,
transfer needs, academy projections.

**Still impossible:** anything derived from what happened in a match.

---

## Tier 2 · Event data

One row per on-ball action. This is where the product starts telling you things
you did not already know.

**Endpoint:** `POST /api/v1/matches/{match_id}/events`

| Field | Type | Req. | Why it matters |
|---|---|:--:|---|
| `period`, `minute`, `second` | int | ● | Match clock; sequences and PPDA windows need ordering |
| `type` | string | ● | `pass carry shot dribble duel tackle interception pressure clearance recovery foul save card sub_on sub_off` |
| `outcome` | string | ● | `success` / `incomplete` / `goal` / `on_target` / `off_target` |
| `is_own_team` | bool | ● | Which side acted |
| `player_id` | string | | Without it, per-player metrics and heatmaps are unavailable; team totals still work |
| `x`, `y` | float | ● | Start position, metres |
| `end_x`, `end_y` | float | | **Required for xT, progressive actions and directness.** A pass with no endpoint contributes almost nothing |
| `qualifiers` | object | | `situation` (`open_play\|counter\|set_piece\|corner\|free_kick\|penalty`), `body_part` (`foot\|head\|other`), `card`, `length_m` |

**Unlocks:** pass possession, field tilt, PPDA in both directions, xG per shot,
xT per action, touch heatmaps, zone control, press height, directness, width,
build-up length, vulnerability detection, substitution recommendations with
tactical justification.

**Still impossible:** time possession (no continuous ball signal), true
occupancy heatmaps (event data only sees a player when they touch the ball),
off-ball runs, real defensive line height and compactness, packing.

**Typical sources:** Opta/StatsPerform, StatsBomb, Wyscout, or manual tagging
(Hudl, LongoMatch).

---

## Tier 3 · Tracking data

Positions of all 22 players plus the ball, 10-25 Hz. Off-ball behaviour becomes
visible, which is where most tactical truth lives.

**Endpoint:** `POST /api/v1/matches/{match_id}/tracking`

| Field | Type | Req. | Why it matters |
|---|---|:--:|---|
| `period` | int | ● | — |
| `timestamp_ms` | int | ● | Milliseconds from the period's kickoff |
| `home_positions` | `{player_id: [x, y]}` | ● | The analysed team, in metres |
| `away_positions` | `{player_id: [x, y]}` | ● | The opposition |
| `ball` | `[x, y]` or `[x, y, z]` | | Without it, possession must come from event data |
| `possession_team` | `home`/`away` | | Supplied by most providers; otherwise inferred from proximity with a 3-frame debounce so a ball flying past a defender does not flip control |

Frames are **decimated to `target_hz` (default 5 Hz) on ingest**. A 90-minute
match at 25 Hz is 135,000 frames; nothing in the analytics layer benefits from
more than ~5 Hz, and storing the raw rate makes every query slower for no gain.

**Unlocks:** time possession, true occupancy heatmaps, played formation
detection, defensive line height, block compactness, team spread, direct speed,
packing, physical load.

**Typical sources:** Second Spectrum / Genius Sports, SkillCorner
(broadcast-derived), TRACAB, or GPS vests (own team only).

---

## Tier 4 · Video

Raw footage, when no data feed exists.

**Endpoint:** `POST /api/v1/video/analyze` (multipart)

| Field | Type | Req. | Why it matters |
|---|---|:--:|---|
| `file` | video | ● | MP4/MOV/MKV. **A fixed wide (tactical) camera gives dramatically better calibration than broadcast cuts** |
| `team_id` | string | ● | — |
| `home_kit_hex` | string | | Home shirt colour, used to map colour clusters onto sides |
| `sample_hz` | float | | Frames analysed per second; 5 Hz default |
| `camera_type` | enum | | `tactical` / `broadcast` / `handheld` |

**Unlocks:** tracking for clubs with no provider contract, clip-level phase
analysis, opposition analysis from broadcast footage.

**Still impossible:** shirt numbers, and therefore player identity, without an
OCR pass; anything outside the camera frame — broadcast footage shows roughly
60% of the pitch at any moment.

The job record reports the engine that actually ran, the fraction of frames with
a valid homography, and the Lab-space separation between the two kits. Below
~18 separation, team assignment is unreliable and the report says so.

---

## Tier 5 · Context and finance

The inputs that make recommendations *actionable* rather than merely correct.

**Endpoints:** `PATCH /api/v1/tenants/current`, `POST /api/v1/transfers/market`

| Field | Type | Req. | Why it matters |
|---|---|:--:|---|
| `transfer_budget_eur` | int | ● | Fee budget for the window |
| `wage_budget_eur_per_year` | int | ● | Headroom in the annual wage bill — the second knapsack dimension |
| `market_player.asking_price_eur` | int | ● | — |
| `market_player.wage_demand_eur_per_year` | int | ● | — |
| `market_player.league_tier` | 1-5 | | 1 = top-5 league. Drives the league-strength adjustment on projected level |
| `market_player.availability` | 0-1 | | How gettable the deal is. Ranks a realistic target above an impossible one |
| `market_player.release_clause_eur` | int | | Used when lower than the asking price |
| `market_player.injury_history_days_2y` | int | | Feeds the risk score |
| `academy.biological_age_offset` | float | | Skeletal minus chronological age, in years. **Negative = late developer**, whose current output understates them; the engine corrects upward (bio-banding) |
| Academy assessments | object[] | | Three per player over six months is the threshold for a trustworthy growth rate. Below that the projection falls back to an age prior and says so |

---

## Minimum viable inputs, by question

| You want to know | You need |
|---|---|
| Who should start on Saturday? | Tier 1 |
| Which formation suits this squad? | Tier 1 |
| Who is at injury risk? | Tier 1 + `minutes_last_7d` |
| How much of the ball did we have? | Tier 2 (pass share) or Tier 3 (time) |
| Where do we leak chances? | Tier 2 |
| Who should come off, and when? | Tier 2 + a saved lineup |
| Is our block compact? | Tier 3 |
| Where were our players when off the ball? | Tier 3 |
| Who should we sign, within budget? | Tier 1 + Tier 5 (Tier 2 sharpens it) |
| When is this youth player ready? | Tier 1 + academy assessments |
