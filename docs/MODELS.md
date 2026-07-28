# Models

Every model reports a version and a `provenance`. `bootstrap` means it was
fitted on an explicit generative process rather than on real matches: calibrated
and monotonic on day one, and meant to be replaced. Inspect them at
`GET /api/v1/meta/models`.

---

## Expected goals — `xg-logistic-1.0`

Logistic in distance to goal and visible goal angle, with multiplicative
adjustments for situation (open play, counter, set piece, rebound) and body part.
`defenders_in_cone` is used when tracking makes it available and **ignored, not
imputed, when absent** — it is the single most informative extra feature, so
guessing it would be worse than omitting it. Penalties are a fixed 0.76.

## Expected threat — `xt-grid-1.0`

A 16×12 surface produced by value iteration over a three-action MDP:

- **shoot**, with probability rising as the ball nears goal, paying `xG`;
- **move**, drawn from a distance-decaying, forward-biased kernel;
- **lose possession**, paying nothing.

The turnover term is not decoration. Without an absorbing failure state the ball
can be recycled for free, and the fixed point is an almost flat surface where
every cell inherits the value of the best shot anywhere on the pitch. With it,
the grid runs from ~0.009 in front of your own goal to ~0.53 in the six-yard box.

The kernel scale is a typical pass length, not the pitch length; a diffuse
kernel mixes the whole surface and flattens the result the same way.

---

## Fatigue and injury hazard

Not a fitted model — an explicit physiological curve, so every term is
inspectable:

```
onset  = 45 + 0.32·stamina − 0.55·max(0, age−29) − 0.012·minutes_last_7d − 0.15·fatigue
decay  = 1 − min(0.42, slope·over·(1 + over/220))          where over = max(0, minute − onset)
hazard = f(minutes past onset, acute:chronic workload ratio, age, high-intensity distance)
```

The shape matches the well-documented collapse in high-intensity running in the
closing twenty minutes: negligible decay early, then an accelerating decline
whose onset moves earlier with congestion and age.

`minutes_last_7d` is the highest-leverage input a club can supply here — it
moves the onset minute directly.

## Positional fit and effective level

`position_fit` blends a declared term (natural 1.0 → secondary 0.93 → same role
bucket 0.88 → same line 0.72 → other 0.35), an attribute-profile term, and the
distance between position anchors. Goalkeepers and outfielders never swap.

`effective_level` applies fit as a **damped** penalty:

```
effective = overall · (1 − 0.60·(1 − fit)) · performance_multiplier
```

Multiplying the rating by the raw fit over-punishes small compromises — a
centre-forward at striker is not a 12% worse footballer — while still keeping a
92-rated winger ahead of a 78-rated natural at the same slot.

**The age curve is deliberately absent here.** `overall_rating` already states
what a player is now; multiplying by an age curve double-counts age and
systematically undervalues everyone not exactly at their peak. `age_curve()`
exists for *projection* (what will this player be worth in two years) and for
flagging squad areas about to age out.

---

## Impact model — `impact-ridge-1.0`

Ridge regression over degree-2 polynomial features. Target: **contribution to
expected goal difference over the remaining minutes**.

Features: `effective_level`, `position_fit`, `performance_multiplier`,
`minutes_remaining`, `fresh_legs_edge`, `tactical_need`, `score_state`.

The generative process behind the prior: contribution is roughly linear in
effective level and positional fit, scaled by the minutes left, with a genuine
but modest bonus for fresh legs against tired opponents and a tactical-need term
for a change that addresses a diagnosed weakness. Holdout R² ≈ 0.88,
MAE ≈ 0.029 xGD.

### How a substitution is scored

```
gain = (impact(sub) − impact(starter)) · adaptation
     + injury_hazard_avoided · 0.22
     + second_yellow_risk    · 0.30
     ×  1.15 if chasing and the slot is attacking, 1.10 if protecting and defensive
```

`adaptation = minutes_remaining / (minutes_remaining + 6)` damps the *gain*, not
the substitute's rating — scaling `effective_level` instead would turn an 84 into
a 52 and suppress every recommendation.

Constraints: five substitutions across three windows; goalkeepers are not
rotated tactically; one recommendation per outgoing and incoming player.

---

## Best XI

Exact linear assignment via the Hungarian algorithm (Jonker-Volgenant shortest
paths, implemented in numpy so scipy stays optional). Greedy assignment is
genuinely worse here — it strands a versatile player in the wrong slot — and
`tests/test_ml.py` includes a case where greedy loses by a factor of three.

`ignore_load=False` answers "who should start this weekend" (fatigue and fitness
applied); `ignore_load=True` answers "who is our best XI". During a congested
run they disagree, and that disagreement is the useful output.

Formations with a repeated position (two central midfielders, two strikers)
spread those slots 18 m apart laterally. Sharing one anchor stacks the players
on a single point — which draws two names on top of each other in the UI, and in
the simulator put two strikers in the same high-value spot and doubled that
team's shot volume.

---

## Transfer recommender

**Stage 1 — needs.** Per position, a severity in [0, 1] combining:

| Term | Weight | Meaning |
|---|---|---|
| quality gap | 0.99 | best available option vs the squad's 70th-percentile starter bar |
| slot depth | 0.30 | fewer than two bodies above a 78% fit is one injury from an emergency |
| line depth | 0.20 | bodies per line against a target (GK 3, DEF 8, MID 6, ATT 5) |
| ageing | 0.14 | every option past peak inside the horizon |
| contract | 0.13 | half the position's contracts expiring within 13 months |
| tactical | + | a diagnosed lane leak or final-third stall on this side |

Unavailable players do not count as depth — long-term absentees are the most
common reason a roster that looks fine on paper needs the market.

The scan is limited to positions the club's formations actually use, so a 4-3-3
side is not told it urgently needs wing-backs.

**Stage 2 — scoring.** Projected level in *our* shirt = rating, adjusted for
league strength (tier 1 → 1.00 … tier 5 → 0.70), blended modestly with potential
under 23, then through `effective_level` for the target position. Compared
against the best level the squad can currently field there — which, with the
first-choice centre-back injured, is whoever shuffles across, not the absent
starter and not zero.

Composite = 0.36·quality + 0.22·fit + 0.27·value − 0.15·risk, scaled by need
severity. Value is log-scaled in cost so free transfers do not dominate purely
by having a tiny denominator.

**Stage 3 — bundle.** A two-dimensional knapsack over transfer fee and annual
wage bill. NP-hard in general; with a handful of signings and fees bucketed to
€250k, exact dynamic programming is fast. The same player scored against several
needs is deduplicated to their best slot — you cannot sign a footballer twice.

---

## Academy — `academy-gbr-1.0`

Gradient boosting, target **months until first-team level**. Holdout R² ≈ 0.98,
MAE ≈ 3.1 months against the generative process.

Two corrections run *before* the model sees anything:

- **Biological age.** A late developer (negative skeletal offset) has their
  ability adjusted upward by up to 6 points; an early maturer is adjusted down
  and flagged, because their dominance is partly physical and will normalise.
  This is the whole point of bio-banding.
- **Level of competition.** A rating earned in the reserves is weighted ×1.035,
  in the seniors ×1.08.

The **relative age effect** is reported rather than corrected: a Q1 birth date
under 18 raises a warning that age-group performance is inflated.

The first-team bar is the 25th percentile of the senior squad **at that
position** — a prospect displaces the bottom of the roster before they displace a
starter, and a goalkeeper should not be judged against the forwards. The model is
trained against a fixed bar of 68 and shifted for the club's actual bar, so a
second-tier academy and a Champions League academy give different answers for
the same player.

Growth is least-squares over the assessment history. Below three assessments
spanning six months the rate is an age prior, not a measurement, and the
projection says so and lowers its confidence.

Pathways: `promote_now`, `train_with_first_team`, `loan_out`,
`continue_academy`, `review`, `release`. A ceiling below the first-team bar
returns `months_to_first_team = null` — that is a loan or sale profile, not a
promotion timeline.

---

## Match simulator

Not a product model — a deterministic fixture generator, used for the demo, for
tests, and as the labelled fallback when the CV extras are missing.

It is calibrated against real match distributions, and getting there exposed two
bugs worth recording:

- **The block must be compressed around the ball.** The position anchors span
  ~87 m; used as absolute positions the "team" strings out over the whole pitch
  and every compactness metric reads as broken.
- **The block centre must be clamped, per direction.** Block follows ball,
  ball follows carrier, carrier is pushed forward by the block — an unclamped
  loop walks the whole team onto the goal line and produces ~90 shots a side.
  A single shared clamp is worse than none: it over-restricts one team and lets
  the other shoot from the six-yard box.

Current output per 90 minutes: ~11-15 shots a side, ~1-3 xG, mean pass 17 m,
75-80% completion, 33 m block depth.
