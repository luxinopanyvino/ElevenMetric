"""The input contract, as data.

This is the machine-readable answer to "what do I need to feed it?". The UI
renders it as the onboarding checklist, and :func:`assess` scores a real tenant
against it so a club can see exactly which capability each missing feed unlocks.

Tiers are cumulative: everything in tier 1 works with a teamsheet, and each
subsequent tier adds capabilities rather than replacing them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Field_:
    name: str
    type: str
    required: bool
    description: str
    example: str | None = None
    unit: str | None = None


@dataclass
class Tier:
    key: str
    name: str
    order: int
    summary: str
    #: What you can compute once this tier is present.
    unlocks: list[str]
    #: What stays impossible without the next tier.
    still_missing: list[str]
    fields: list[Field_] = field(default_factory=list)
    typical_sources: list[str] = field(default_factory=list)
    ingest_endpoint: str | None = None


TIERS: list[Tier] = [
    Tier(
        key="squad",
        name="Tier 1 · Squad and lineup",
        order=1,
        summary=(
            "A teamsheet and player attributes. No match data at all. Enough for "
            "shape, selection and workload work before a ball is kicked."
        ),
        unlocks=[
            "Best XI for any formation (exact assignment)",
            "Formation comparison — which shape this squad actually fills",
            "Out-of-position warnings and squad balance",
            "Fatigue and injury-hazard modelling from minutes played",
            "Transfer needs by position (depth, quality, ageing, contracts)",
            "Academy projections",
        ],
        still_missing=[
            "Possession, heatmaps, xG, xT — all require match data",
            "Tactical profiling and vulnerability detection",
            "Evidence-backed in-match substitutions",
        ],
        typical_sources=["Manual entry in the UI", "CSV import", "Club squad database",
                         "SoFIFA import (EA FC 26 ratings — a game's judgement, "
                         "not measurements; see docs/EXTERNAL_SOURCES.md)"],
        ingest_endpoint="POST /api/v1/players",
        fields=[
            Field_("name", "string", True, "Player's full name", "Frenkie de Jong"),
            Field_("primary_position", "enum", True,
                   "One of GK, RB, RCB, CB, LCB, LB, RWB, LWB, DM, CM, AM, RM, LM, RW, LW, CF, ST, SS", "CM"),
            Field_("secondary_positions", "enum[]", False, "Positions the player also covers", '["DM"]'),
            Field_("birth_date", "date", False,
                   "Drives the age curve; without it every player is treated as 26", "1997-05-12"),
            Field_("preferred_foot", "enum", False, "left | right | both", "right"),
            Field_("overall_rating", "float 0-99", True,
                   "Current level. Any consistent internal scale works — it is used relatively", "94"),
            Field_("potential_rating", "float 0-99", False, "Ceiling; matters most for under-23s", "95"),
            Field_("attributes", "object", False,
                   "pace, shooting, passing, dribbling, defending, physical, stamina, aerial, "
                   "composure, vision, work_rate_off, work_rate_def — each 0-99. Drives positional fit",
                   '{"pace": 78, "passing": 91, "stamina": 84}'),
            Field_("fitness", "float 0-100", False, "Current condition", "92", "%"),
            Field_("fatigue", "float 0-100", False, "Accumulated load", "31", "%"),
            Field_("minutes_last_7d", "int", False,
                   "The single most useful load input — it moves the fatigue-onset minute directly",
                   "180", "minutes"),
            Field_("market_value_eur", "int", False, "For transfer maths", "80000000", "EUR"),
            Field_("wage_eur_per_year", "int", False, "For the wage-budget constraint", "12000000", "EUR"),
            Field_("contract_until", "date", False, "Feeds contract-risk in need detection", "2029-06-30"),
        ],
    ),
    Tier(
        key="event_data",
        name="Tier 2 · Event data",
        order=2,
        summary=(
            "One row per on-ball action with coordinates. This is the tier where "
            "the product starts telling you things you did not already know."
        ),
        unlocks=[
            "Pass possession and field tilt",
            "PPDA (pressing intensity), both directions",
            "xG per shot and xT per action",
            "Touch heatmaps and zone control",
            "Tactical profiling: press height, directness, width, build-up",
            "Vulnerability detection (lane leaks, counterpress failure, final-third stalls)",
            "Substitution recommendations with tactical justification",
        ],
        still_missing=[
            "Time possession (needs a continuous ball signal)",
            "True occupancy heatmaps — event data only sees a player when they touch the ball",
            "Off-ball runs, defensive line height, real compactness",
            "Packing / players bypassed",
        ],
        typical_sources=["Opta / StatsPerform", "StatsBomb", "Wyscout",
                         "Manual tagging (Hudl, LongoMatch)",
                         "StatsBomb open data import (free, real fixtures; "
                         "publishes no player ratings)"],
        ingest_endpoint="POST /api/v1/matches/{match_id}/events",
        fields=[
            Field_("period", "int", True, "1 or 2 (3/4 for extra time)", "1"),
            Field_("minute", "int", True, "Match clock minute", "37"),
            Field_("second", "int", True, "Match clock second", "12"),
            Field_("type", "string", True,
                   "pass, carry, shot, dribble, duel, tackle, interception, pressure, clearance, "
                   "recovery, foul, save, card, sub_on, sub_off", "pass"),
            Field_("outcome", "string", True, "success | incomplete | goal | on_target | off_target", "success"),
            Field_("team_id / is_own_team", "string / bool", True,
                   "Which side acted. Coordinates are always in the acting team's attacking frame", "true"),
            Field_("player_id", "string", False,
                   "Without it, per-player metrics and heatmaps are unavailable — team totals still work", "p_8812"),
            Field_("x", "float", True, "Start position along the pitch length", "68.4", "m (0-105)"),
            Field_("y", "float", True, "Start position across the pitch width", "22.1", "m (0-68)"),
            Field_("end_x", "float", False,
                   "Required for xT, progressive actions and directness — a pass without an endpoint "
                   "contributes almost nothing", "81.0", "m"),
            Field_("end_y", "float", False, "See end_x", "14.5", "m"),
            Field_("qualifiers", "object", False,
                   "situation (open_play|counter|set_piece|corner|free_kick|penalty), body_part "
                   "(foot|head|other), card, length_m",
                   '{"situation": "counter", "body_part": "foot"}'),
        ],
    ),
    Tier(
        key="tracking",
        name="Tier 3 · Tracking data",
        order=3,
        summary=(
            "Positions of all 22 players plus the ball, 10-25 Hz. Off-ball "
            "behaviour becomes visible, which is where most tactical truth lives."
        ),
        unlocks=[
            "Time possession, the number broadcasters quote",
            "True occupancy heatmaps (where players *were*, not where they touched)",
            "Played formation detection, in and out of possession separately",
            "Defensive line height, block compactness, team spread",
            "Direct speed, space creation, packing",
            "Physical load: distance, high-intensity distance, sprint counts",
        ],
        still_missing=[
            "Nothing structural — this is the reference tier. Event data remains "
            "useful alongside it for action semantics (a 'pass' vs a 'clearance')."
        ],
        typical_sources=[
            "Second Spectrum / Genius Sports", "SkillCorner (broadcast-derived)",
            "TRACAB / ChyronHego", "STATSports or Catapult GPS (own team only)",
        ],
        ingest_endpoint="POST /api/v1/matches/{match_id}/tracking",
        fields=[
            Field_("period", "int", True, "1 or 2", "1"),
            Field_("timestamp_ms", "int", True, "Milliseconds from kickoff of the period", "2214000", "ms"),
            Field_("home_positions", "object", True,
                   "{player_id: [x, y]} in metres for the analysed team",
                   '{"p_8812": [61.2, 30.4]}'),
            Field_("away_positions", "object", True, "Same, for the opposition", '{"o_41": [44.0, 12.7]}'),
            Field_("ball", "float[]", False,
                   "[x, y] or [x, y, z]. Without it, possession must come from event data",
                   "[62.0, 31.1, 0.4]"),
            Field_("possession_team", "string", False,
                   "home | away. Supplied by most providers; otherwise inferred from proximity", "home"),
        ],
    ),
    Tier(
        key="video",
        name="Tier 4 · Video",
        order=4,
        summary=(
            "Raw footage, when no data feed exists. The CV pipeline produces "
            "tracking from it, then everything in tier 3 applies — at the accuracy "
            "the footage allows."
        ),
        unlocks=[
            "Tracking data for clubs with no provider contract",
            "Clip-level analysis of specific phases",
            "Opposition analysis from broadcast footage",
        ],
        still_missing=[
            "Shirt numbers, and therefore player identity, unless an OCR pass is added",
            "Anything outside the camera frame — broadcast footage shows roughly "
            "60% of the pitch at any moment",
        ],
        typical_sources=["Tactical (wide, fixed) camera — strongly preferred",
                         "Broadcast footage", "Veo / Spiideo / Pixellot"],
        ingest_endpoint="POST /api/v1/video/analyze",
        fields=[
            Field_("file", "video", True,
                   "MP4/MOV/MKV. A fixed wide angle gives dramatically better calibration "
                   "than broadcast cuts", "match.mp4"),
            Field_("home_kit_hex", "string", False,
                   "Home shirt colour, used to map colour clusters onto sides", "#a50044"),
            Field_("sample_hz", "float", False, "Frames analysed per second; 5 Hz is the default", "5", "Hz"),
            Field_("camera_type", "enum", False, "tactical | broadcast | handheld", "tactical"),
            Field_("known_lineup", "object", False,
                   "Track-to-player mapping, so CV output carries real identities", None),
        ],
    ),
    Tier(
        key="context",
        name="Tier 5 · Context and finance",
        order=5,
        summary=(
            "The inputs that make recommendations *actionable* rather than merely "
            "correct — budgets, availability and the market pool."
        ),
        unlocks=[
            "Transfer bundles constrained by real fee and wage budgets",
            "Availability-weighted target ranking",
            "Academy pathways calibrated to this club's actual first-team level",
        ],
        still_missing=[],
        typical_sources=["Club finance", "Transfermarkt / provider market data", "Agent network"],
        ingest_endpoint="POST /api/v1/transfers/market",
        fields=[
            Field_("transfer_budget_eur", "int", True, "Fee budget for the window", "120000000", "EUR"),
            Field_("wage_budget_eur_per_year", "int", True, "Headroom in the annual wage bill", "25000000", "EUR"),
            Field_("market_player.asking_price_eur", "int", True, "Fee the selling club wants", "45000000", "EUR"),
            Field_("market_player.wage_demand_eur_per_year", "int", True, "Player's wage demand", "6000000", "EUR"),
            Field_("market_player.league_tier", "int 1-5", False,
                   "1 = top-5 league. Drives the league-strength adjustment on projected level", "2"),
            Field_("market_player.availability", "float 0-1", False,
                   "How gettable the deal is. Ranks a realistic target above an impossible one", "0.55"),
            Field_("market_player.injury_history_days_2y", "int", False,
                   "Days lost to injury over two seasons", "45", "days"),
            Field_("academy.biological_age_offset", "float", False,
                   "Skeletal minus chronological age, in years. Negative = late developer, whose "
                   "output is corrected upward (bio-banding)", "-1.2", "years"),
            Field_("academy assessments", "object[]", False,
                   "Three assessments over six months is the threshold for a trustworthy growth rate; "
                   "below that the projection uses an age prior", None),
        ],
    ),
]


def catalogue() -> dict:
    return {
        "tiers": [
            {
                **{k: v for k, v in asdict(t).items() if k != "fields"},
                "fields": [asdict(f) for f in t.fields],
            }
            for t in TIERS
        ],
        "principles": [
            "Nothing is imputed across tiers: a metric that needs an absent input is "
            "reported as null, never estimated silently.",
            "Every report carries data_completeness and confidence, and every "
            "recommendation is scaled by them.",
            "Coordinates are always metres on a 105x68 pitch, origin bottom-left, "
            "x along the acting team's attacking direction. Provider frames "
            "(StatsBomb 120x80, Opta 0-100) are converted on ingest.",
        ],
    }


def assess(
    *,
    has_players: bool,
    has_lineups: bool,
    has_events: bool,
    has_tracking: bool,
    has_video: bool,
    has_market: bool,
    has_budget: bool,
    has_academy_assessments: bool,
) -> dict:
    """Score a tenant's actual data against the contract."""
    present = {
        "squad": has_players,
        "event_data": has_events,
        "tracking": has_tracking,
        "video": has_video,
        "context": has_market and has_budget,
    }

    rows = []
    for tier in TIERS:
        ok = present.get(tier.key, False)
        rows.append({
            "key": tier.key,
            "name": tier.name,
            "satisfied": ok,
            "unlocks": tier.unlocks,
            "blocked": [] if ok else tier.unlocks,
            "ingest_endpoint": tier.ingest_endpoint,
        })

    score = sum(1 for v in present.values() if v) / len(present)
    gaps = []
    if not has_players:
        gaps.append("No players on file — start by importing the squad.")
    if has_players and not has_lineups:
        gaps.append("No lineups saved — the best-XI and formation tools need at least one.")
    if not (has_events or has_tracking or has_video):
        gaps.append(
            "No match data of any kind. Everything tactical is unavailable; "
            "event data is the cheapest way to unlock it."
        )
    if has_events and not has_tracking:
        gaps.append(
            "Event data present but no tracking — time possession, true heatmaps "
            "and off-ball shape are unavailable."
        )
    if not has_budget:
        gaps.append("No transfer or wage budget set — the transfer engine cannot build a bundle.")
    if not has_market:
        gaps.append("Market pool is empty — import targets to run a transfer scan.")
    if not has_academy_assessments:
        gaps.append(
            "No academy assessments — projections will use age priors instead of "
            "measured growth. Three per player over six months fixes this."
        )

    return {
        "coverage_score": round(score, 3),
        "tiers": rows,
        "gaps": gaps,
    }
