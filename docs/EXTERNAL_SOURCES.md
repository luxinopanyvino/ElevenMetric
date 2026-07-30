# External data sources

Three routes get data into ElevenMetric from the club itself — CSV, the by-hand
editor, video. This is the fourth: **import a real team from a public source**,
so a fresh install can analyse a real squad and watch two real clubs play
without a file to prepare.

Two sources are supported. They answer different questions, and the product is
built so they can never quietly stand in for each other.

| | **SoFIFA** | **StatsBomb open data** |
|---|---|---|
| What it actually is | EA Sports FC (currently **EA FC 26**) player ratings, republished per club | Free, publicly released data about matches that **actually happened** |
| Tier | 1 · Squad | 2 · Events |
| Gives you | real clubs, real squads, positions, OVR/POT, the full 0-99 attribute profile, age, foot, value, wage | competitions, fixtures, both lineups, one row per on-ball action with coordinates |
| Does **not** give you | minutes played, fitness, fatigue, season statistics | **player ratings or attributes of any kind**, market values, tracking |
| Unlocks | best XI, positional fit, substitutions, transfer planning, **the match simulator against a real club** | possession, field tilt, PPDA, xG, xT, heatmaps, tactical profile |
| Dependency | none — standard library only | `statsbombpy` (optional extra) |
| Live fetch | opt-in, off by default | on when the package is installed |

The single most important thing this page can tell you: **a SoFIFA rating is a
games studio's opinion, not a measurement of the player.** The product analyses
*the source's view of* a squad, and says so — in the API response, on every
imported row, and on screen wherever those numbers drive a recommendation.

---

## Getting started

Open **Import data → Real teams**. `GET /api/v1/external/sources` is the same
information as data: what each source is, which tier it lands in, whether it can
be used right now, and if not, why and what to do about it.

### StatsBomb

```bash
cd backend
pip install -r requirements-external.txt
```

That is all. Open data needs no credentials. Browse competitions → season →
fixture → preview → import. The fixture arrives as an ordinary match with
`source = event_data` and `provider = statsbomb`, so the analysis pipeline
treats it exactly like a club's own feed.

You can also import from a local checkout of the
[open-data repository](https://github.com/statsbomb/open-data) without the
package at all — the same parser reads
`lineups/<match_id>.json` and `events/<match_id>.json` directly.

### SoFIFA

Two routes, and **the file route always works**:

* **From a file** — a saved club page (`.html`) or a SoFIFA-format export
  (`.csv`, the column vocabulary the widely circulated EA FC player datasets
  use: `sofifa_id`, `short_name`, `player_positions`, `attacking_finishing`…).
  No network, no dependency on anyone's markup.
* **Live** — off by default. To enable:

  ```bash
  export ELEVENMETRIC_EXTERNAL_FETCH_ENABLED=true
  ```

  Requests are throttled to one per second per host, cached on disk with their
  retrieval time, and sent with a user agent naming this deployment.

---

## Terms, and what is your call rather than the product's

sofifa.com's `robots.txt` currently allows general crawling (`User-agent: *`,
`Allow: /`) while signalling `ai-train=no, use=reference` and disallowing a list
of named AI crawlers. This feature's fetching is reference use by your own
deployment. **It is off unless you switch it on, and complying with the source's
terms is your responsibility as the operator, not something the product can
decide for you.** The underlying ratings are EA's.

StatsBomb open data is free under
[StatsBomb's user agreement](https://github.com/statsbomb/open-data/blob/master/LICENSE.pdf),
which **requires attribution**. The product carries that attribution in the
provenance of every StatsBomb-derived row and displays it in the panel.

Nothing in this feature exposes a bulk export of source data. Imports go into
your tenant's own database for your own analysis.

---

## Provenance

Every team, player and match this feature creates carries a `provenance` record.
It is a column, not a log line — a log can be rotated away while the claim it
justified stays on screen.

```json
{
  "source": "sofifa",
  "edition": "EA FC 26",
  "source_id": "241",
  "source_url": "https://sofifa.com/team/241",
  "retrieved": "fetch",
  "retrieved_at": "2026-07-31T10:04:11+00:00",
  "note": "Player ratings published by SoFIFA from the EA Sports FC video game. These are a games studio's opinion, not measurements of the players."
}
```

`retrieved` is `fetch` (a live request), `cache` (a fetch served from the local
cache) or `file` (a page or export you supplied), so an audit can tell a live
read from a replay.

The match simulator reports three kinds of opponent, never two:
`opponent_origin` is `squad_on_file`, `imported` (with the source named) or
`generated`.

---

## What an import will and will not do

**It will not invent anything.** A field the source does not publish stays
absent: it keeps the column's own default on create, and its existing value on a
refresh. It is never given a plausible number.

**Ratings can be unknown.** `overall_rating` and `potential_rating` are
nullable, because StatsBomb names eleven real players and grades none of them.
An ungraded player is **excluded** from best XI, substitutions, transfer scoring
and the simulator, with the reason reported — rather than ranked as if they were
a 70. This is the one place the feature changed the schema; see
`features.is_rankable`.

**A re-import refreshes, it does not duplicate.** Players are matched on the
source's own identifier, so two players who share a name survive a refresh
intact. A player who has left the source squad is **reported, not deleted** —
the club may still want their history.

**An unmappable row is reported, not guessed at.** An unrecognised position
fails that row with the offending value; an unrecognised attribute is reported
and dropped. Nothing is matched by similarity. An import with any failed row is
refused unless you explicitly opt into a partial import — the same promise the
CSV route makes.

**Attributes that have nowhere to live are named.** SoFIFA's attacking
positioning currently has no key in this product's vocabulary (spec 001 adds
it), so it is reported as unmapped rather than folded into `defensive_awareness`,
which is a different skill entirely.

---

## When a source breaks

Both sources are outside this project's control, and a scraper against a
third-party site *will* eventually break. The design assumes it:

* An unavailable source returns **503** naming the reason and the remedy.
* An unrecognised response returns **502** naming the source, the URL and what
  the parser expected. **Nothing is written.**
* The file route (`/external/sofifa/preview-file`, `/commit-file`) has no
  dependency on live markup, so a broken selector degrades the feature rather
  than removing it.

To diagnose a SoFIFA markup change in one step:

```bash
cd backend
python -m app.services.external.sofifa --probe "https://sofifa.com/team/241"
```

It prints the tables it found, the headers it matched, and the first players it
read — so a fix is usually a table edit in `sofifa.py`, not an investigation.

> **Note.** The SoFIFA HTML selectors have not been confirmed against a live
> page: sofifa.com's `robots.txt` disallows the crawler the assistant that wrote
> this module would have fetched as, so no live page was retrieved. The parser
> is header-driven and defensive, and the file route and the whole test suite
> run without it. Run `--probe` once against a real club page to confirm — any
> correction is confined to `sofifa.py`.

---

## Configuration

| Setting | Default | What it does |
|---|---|---|
| `ELEVENMETRIC_EXTERNAL_FETCH_ENABLED` | `false` | Master switch for **all** outbound requests |
| `ELEVENMETRIC_EXTERNAL_HOSTS` | `sofifa.com,github.com,githubusercontent.com` | The only hosts that will ever be contacted |
| `ELEVENMETRIC_EXTERNAL_RATE_LIMIT_S` | `1.0` | Seconds between requests to the same host |
| `ELEVENMETRIC_EXTERNAL_TIMEOUT_S` | `20.0` | Per-request timeout |
| `ELEVENMETRIC_EXTERNAL_CACHE_DIR` | `data/external-cache` | Where fetched responses are cached, with their timestamps |
| `ELEVENMETRIC_EXTERNAL_CACHE_TTL_HOURS` | `24.0` | How long a cached page is reused |
| `ELEVENMETRIC_EXTERNAL_USER_AGENT` | names this project | Sent on every request. Identify your deployment honestly |

---

## API

```
GET  /api/v1/external/sources                  # what each source is, and its state
GET  /api/v1/external/sofifa/clubs?q=          # search clubs           (live)
GET  /api/v1/external/sofifa/preview           # squad as it would be stored (live)
POST /api/v1/external/sofifa/preview-file      # …from a saved page or export
POST /api/v1/external/sofifa/commit            # import                 (live)
POST /api/v1/external/sofifa/commit-file       # …from a saved page or export
GET  /api/v1/external/statsbomb/competitions
GET  /api/v1/external/statsbomb/matches?competition_id=&season_id=
GET  /api/v1/external/statsbomb/preview?match_id=
POST /api/v1/external/statsbomb/commit
```

Previews write nothing. Commits require the `squad:write` capability and are
tenant-scoped like everything else: another tenant asking for an imported team
by id gets **404, not 403**.

---

## Tests

`backend/tests/test_external.py` runs entirely offline, against fixtures in
`backend/tests/fixtures/external/`. No test makes a network request, and none
requires `statsbombpy` to be installed — which is the only way to actually
assert that a missing source degrades the product visibly rather than breaking
it.
