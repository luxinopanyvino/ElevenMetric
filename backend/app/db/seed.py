"""Demo data.

Creates two tenants so multitenancy is demonstrable (and testable), a squad
modelled on the reference screenshot, an academy with assessment histories, a
market pool, and a full simulated match with events and tracking.

    python -m app.db.seed [--reset]

Every rating, price and physical value here is **synthetic** — invented for the
demo, not scouting data about real people.
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import date, datetime, timedelta, timezone

from app.core.security import hash_password
from app.db.session import SessionLocal, engine, init_db
from app.models.academy import AcademyAssessment, AcademyPlayer, AgeGroup
from app.models.catalog import Foot, Player, Position, Team
from app.models.match import (
    InputSource,
    Lineup,
    LineupSlot,
    Match,
    MatchEvent,
    MatchState,
    TrackingFrame,
)
from app.models.tenant import Plan, Role, Tenant, User
from app.models.transfer import DealType, MarketPlayer
from app.services.cv.synthetic import simulate_match

RNG = random.Random(20260728)

# --- Reference squad, from the lineup in the brief -------------------------
# (name, shirt, position, overall, potential, age, foot, is_starter, slot)
SQUAD: list[tuple] = [
    ("Marc-André ter Stegen", 1, Position.GK, 92, 92, 33, Foot.right, True, 0),
    ("Joško Gvardiol", 24, Position.LB, 94, 95, 24, Foot.left, True, 1),
    ("Alessandro Bastoni", 4, Position.LCB, 93, 93, 27, Foot.left, True, 2),
    ("Ronald Araujo", 5, Position.RCB, 93, 93, 27, Foot.right, True, 3),
    ("Jules Koundé", 23, Position.RB, 92, 93, 27, Foot.right, True, 4),
    ("Pedri", 8, Position.CM, 95, 96, 23, Foot.right, True, 5),
    ("Frenkie de Jong", 21, Position.DM, 94, 94, 28, Foot.right, True, 6),
    ("Florian Wirtz", 10, Position.CM, 93, 95, 23, Foot.right, True, 7),
    ("Ousmane Dembélé", 7, Position.RW, 95, 95, 28, Foot.left, True, 8),
    ("Dušan Vlahović", 9, Position.ST, 97, 97, 26, Foot.left, True, 9),
    ("Lucas Valentin", 11, Position.LW, 95, 96, 25, Foot.right, True, 10),
    # Bench
    ("Kai Schmidt", 13, Position.GK, 88, 90, 26, Foot.right, False, 11),
    ("Yassin Ibrahim", 2, Position.RB, 89, 92, 22, Foot.right, False, 12),
    ("Piero Hincapié", 3, Position.LB, 85, 89, 24, Foot.left, False, 13),
    ("Luka Kovačević", 6, Position.CM, 86, 90, 21, Foot.right, False, 14),
    ("Ansu Fati", 14, Position.LW, 88, 93, 23, Foot.right, False, 15),
    ("Ferran Torres", 17, Position.LW, 92, 92, 26, Foot.right, False, 16),
    ("Adrien Mercier", 19, Position.CF, 88, 91, 25, Foot.right, False, 17),
    # Squad depth beyond the matchday 18
    ("Iñigo Martínez", 15, Position.CB, 84, 84, 34, Foot.left, False, None),
    ("Mateo Ruiz", 26, Position.DM, 79, 88, 20, Foot.right, False, None),
    ("Elias Novak", 27, Position.AM, 81, 89, 21, Foot.left, False, None),
]

#: A congested week: three fixtures in eight days, so the XI carries real load.
#: name -> (minutes in the last 7 days, accumulated fatigue 0-100, fitness 0-100)
LOAD_PROFILE: dict[str, tuple[int, float, float]] = {
    "Marc-André ter Stegen": (270, 22, 96),
    "Joško Gvardiol": (270, 58, 88),
    "Alessandro Bastoni": (250, 46, 91),
    "Ronald Araujo": (270, 63, 84),
    "Jules Koundé": (180, 34, 93),
    "Pedri": (270, 71, 79),
    "Frenkie de Jong": (245, 55, 87),
    "Florian Wirtz": (270, 66, 82),
    "Ousmane Dembélé": (255, 61, 85),
    "Dušan Vlahović": (240, 52, 89),
    "Lucas Valentin": (268, 69, 80),
    # Bench — fresh, which is the point of them.
    "Kai Schmidt": (0, 4, 100),
    "Yassin Ibrahim": (45, 11, 99),
    "Piero Hincapié": (90, 18, 97),
    "Luka Kovačević": (30, 9, 100),
    "Ansu Fati": (60, 14, 98),
    "Ferran Torres": (75, 16, 97),
    "Adrien Mercier": (25, 8, 100),
    "Iñigo Martínez": (90, 27, 93),
    "Mateo Ruiz": (0, 5, 100),
    "Elias Novak": (20, 7, 100),
}

#: Long-term absentees. Every real squad has them, and they are what turns a
#: strong-looking roster into one that needs the market.
UNAVAILABLE = {
    "Ronald Araujo": "Hamstring — out 5 weeks",
    "Yassin Ibrahim": "Ankle ligament — out 8 weeks",
    "Iñigo Martínez": "Adductor — out 3 weeks",
}

#: Headline archetype per role bucket. Detail attributes are derived from these.
ATTRIBUTE_PROFILES: dict[str, dict[str, int]] = {
    "GK":  {"pace": 55, "shooting": 25, "passing": 78, "dribbling": 50,
            "defending": 30, "physical": 80},
    "CB":  {"pace": 74, "shooting": 45, "passing": 80, "dribbling": 68,
            "defending": 92, "physical": 88},
    "FB":  {"pace": 90, "shooting": 60, "passing": 84, "dribbling": 82,
            "defending": 84, "physical": 78},
    "DM":  {"pace": 72, "shooting": 62, "passing": 90, "dribbling": 85,
            "defending": 84, "physical": 80},
    "CM":  {"pace": 78, "shooting": 76, "passing": 93, "dribbling": 92,
            "defending": 70, "physical": 68},
    "AM":  {"pace": 82, "shooting": 84, "passing": 88, "dribbling": 92,
            "defending": 52, "physical": 66},
    "W":   {"pace": 94, "shooting": 84, "passing": 82, "dribbling": 94,
            "defending": 44, "physical": 66},
    "ST":  {"pace": 86, "shooting": 94, "passing": 74, "dribbling": 84,
            "defending": 40, "physical": 86},
}

#: Where a role's detail differs meaningfully from its headline group. Offsets
#: are added to the parent value — a centre-back's heading is well above their
#: general shooting, a winger's crossing above their general passing.
SIGNATURE_OFFSETS: dict[str, dict[str, int]] = {
    "GK":  {"long_passing": 6, "reactions": 14, "composure": 12, "jumping": 10,
            "strength": 6, "agility": 8},
    "CB":  {"heading_accuracy": 34, "jumping": 10, "strength": 8, "composure": 4,
            "short_passing": 6, "long_passing": 2, "sliding_tackle": -3,
            "finishing": -12, "curve": -14, "agility": -12},
    "FB":  {"crossing": 8, "stamina": 12, "sprint_speed": 4, "agility": 6,
            "heading_accuracy": -10, "finishing": -14, "strength": -6},
    "DM":  {"interceptions": 8, "defensive_awareness": 6, "strength": 8,
            "short_passing": 6, "stamina": 8, "finishing": -14, "agility": -8,
            "heading_accuracy": 2},
    "CM":  {"vision": 6, "short_passing": 5, "ball_control": 4, "stamina": 12,
            "long_passing": 3, "heading_accuracy": -18, "strength": -6},
    "AM":  {"vision": 8, "ball_control": 6, "agility": 8, "curve": 6,
            "finishing": 2, "heading_accuracy": -22, "strength": -8,
            "standing_tackle": -8},
    "W":   {"acceleration": 5, "crossing": 8, "agility": 6, "ball_control": 4,
            "heading_accuracy": -24, "strength": -8, "long_passing": -10,
            "standing_tackle": -6},
    "ST":  {"finishing": 5, "heading_accuracy": 2, "composure": 6, "strength": 6,
            "shot_power": 4, "crossing": -18, "long_passing": -14,
            "standing_tackle": -14},
}


def _clamp(v: float) -> int:
    return int(max(20, min(99, round(v))))


def _attributes(position: Position, overall: float) -> dict:
    """Build a full attribute profile: six headline faces, every detail, the six
    goalkeeping attributes, and the two work rates."""
    from app.services.ml.features import DETAIL_GROUPS, POSITION_BUCKET

    bucket = POSITION_BUCKET[position]
    base = ATTRIBUTE_PROFILES[bucket]
    signature = SIGNATURE_OFFSETS.get(bucket, {})
    scale = overall / 88.0

    attrs = {k: _clamp(v * scale + RNG.uniform(-3, 3)) for k, v in base.items()}

    for parent, keys in DETAIL_GROUPS.items():
        for key in keys:
            attrs[key] = _clamp(
                attrs[parent] + signature.get(key, 0) + RNG.uniform(-4, 4)
            )

    if bucket == "GK":
        gk_base = overall
        attrs.update({
            "gk_diving": _clamp(gk_base + RNG.uniform(-3, 3)),
            "gk_handling": _clamp(gk_base + RNG.uniform(-4, 3)),
            "gk_kicking": _clamp(gk_base - 6 + RNG.uniform(-6, 6)),
            "gk_reflexes": _clamp(gk_base + 1 + RNG.uniform(-3, 3)),
            "gk_positioning": _clamp(gk_base + RNG.uniform(-3, 3)),
            "gk_speed": _clamp(gk_base - 22 + RNG.uniform(-6, 6)),
        })
    else:
        # Outfielders carry token goalkeeping values, as scouting databases do.
        attrs.update({k: RNG.randint(20, 30) for k in
                      ("gk_diving", "gk_handling", "gk_kicking", "gk_reflexes",
                       "gk_positioning", "gk_speed")})

    attrs["work_rate_off"] = RNG.randint(55, 92)
    attrs["work_rate_def"] = RNG.randint(45, 90)
    return attrs


MARKET_NAMES = [
    ("Tomás Iglesias", "Sporting Braga", "Primeira Liga", 2),
    ("Emeka Obi", "Genk", "Jupiler Pro League", 3),
    ("Lars Bergström", "Malmö FF", "Allsvenskan", 4),
    ("Rayan Cherki", "Olympique Lyonnais", "Ligue 1", 1),
    ("Kenji Nakamura", "Feyenoord", "Eredivisie", 2),
    ("Marco Silvestri", "Bologna", "Serie A", 1),
    ("Andrei Popescu", "Steaua", "Liga I", 5),
    ("Diego Fuentes", "River Plate", "Liga Profesional", 3),
    ("Nikola Jovanović", "Red Star", "SuperLiga", 4),
    ("Hugo Almeida", "Vitória SC", "Primeira Liga", 2),
    ("Ismail Traoré", "Club Brugge", "Jupiler Pro League", 3),
    ("Ben Carter", "Brighton", "Premier League", 1),
    ("Kwame Mensah", "Salzburg", "Bundesliga (AT)", 3),
    ("Luca Ferrari", "Atalanta", "Serie A", 1),
    ("Ivan Petrov", "Ludogorets", "Parva Liga", 5),
    ("Théo Lambert", "Stade Rennais", "Ligue 1", 1),
    ("Samuel Osei", "Copenhagen", "Superliga", 4),
    ("Aleix Serra", "Real Sociedad", "LaLiga", 1),
    ("Jonas Weber", "Stuttgart", "Bundesliga", 1),
    ("Rafael Costa", "Palmeiras", "Brasileirão", 2),
    ("Mert Yilmaz", "Basaksehir", "Süper Lig", 3),
    ("Erik Lindqvist", "AIK", "Allsvenskan", 4),
    ("Pau Riera", "Villarreal", "LaLiga", 1),
    ("Daniel Okoye", "Slavia Praha", "Fortuna Liga", 4),
]

#: Name pool for the market entries beyond the hand-written list, so every
#: generated target has a distinct plausible name.
EXTRA_FIRST = [
    "Matias", "Youssef", "Sondre", "Bruno", "Arda", "Nuno", "Filip", "Karim",
    "Viktor", "Josip", "Denis", "Milan", "Sebastián", "Malik", "Tobias", "Ander",
    "Radu", "Jamal", "Otto", "Kylian", "Nikolas", "Adama", "Levi", "Iker",
]
EXTRA_LAST = [
    "Alvarez", "Bekele", "Dahl", "Moreira", "Yildirim", "Cabral", "Novak", "Diaba",
    "Sørensen", "Matić", "Kovač", "Petrović", "Ríos", "Sissoko", "Lindholm",
    "Etxeberria", "Munteanu", "Baraka", "Keller", "Dupont", "Larsen", "Traoré",
    "Visser", "Aguirre",
]

# (name, position, age group, age, current ability, potential, bio-age offset)
# Spread chosen so the demo shows every pathway the engine can emit: a couple of
# near-ready prospects, some long-term projects, a late developer whose numbers
# under-state him, and one player whose ceiling sits below the first-team bar.
ACADEMY_NAMES = [
    ("Gabriel Sanz", Position.AM, AgeGroup.u21, 19.6, 84, 93, -0.9),
    ("Nico Ferrer", Position.DM, AgeGroup.u21, 20.4, 82, 90, 0.6),
    ("Amir Haddad", Position.ST, AgeGroup.u21, 19.9, 80, 91, 0.8),
    ("Marc Vidal", Position.CB, AgeGroup.u19, 18.2, 73, 89, 0.2),
    ("Ilias Bouzid", Position.LW, AgeGroup.u18, 17.1, 68, 92, -1.4),
    ("Pau Esteve", Position.GK, AgeGroup.u19, 18.0, 70, 88, 0.1),
    ("Leo Andersen", Position.LB, AgeGroup.u21, 20.3, 76, 84, 0.4),
    ("Théo Bianchi", Position.RB, AgeGroup.u18, 16.8, 62, 86, -0.5),
    ("Jordi Camps", Position.CM, AgeGroup.u16, 15.7, 55, 87, -1.8),
    ("Rubén Mas", Position.RW, AgeGroup.u18, 17.3, 64, 76, 0.9),
]


def _birth(age_years: float) -> date:
    return date.today() - timedelta(days=int(age_years * 365.25))


def _market_value(overall: float, age: float) -> int:
    """Exponential in rating, with an age multiplier.

    Calibrated so the curve lands where the real market does: ~180M€ at 95,
    ~90M€ at 90, ~22M€ at 80, ~6M€ at 70. A polynomial in the rating (the
    obvious first attempt) overshoots wildly at the top end.
    """
    base = 345.0 * math.exp(0.1386 * overall)
    if age <= 24:
        factor = 1.30
    elif age <= 28:
        factor = 1.00
    elif age <= 31:
        factor = 0.62
    else:
        factor = 0.30
    return int(base * factor)


def _drop_everything() -> None:
    """Drop all tables.

    SQLite enforces foreign keys per-connection, and ``drop_all`` does not order
    drops to satisfy them, so the pragma is lifted for the duration.
    """
    from sqlalchemy import text

    from app.db import base  # noqa: F401  (registers the metadata)
    from app.db.base_class import Base

    is_sqlite = engine.dialect.name == "sqlite"
    with engine.connect() as conn:
        if is_sqlite:
            conn.execute(text("PRAGMA foreign_keys=OFF"))
        Base.metadata.drop_all(bind=conn)
        conn.commit()


def seed(reset: bool = False) -> dict:
    if reset:
        _drop_everything()
    init_db()

    db = SessionLocal()
    try:
        if db.query(Tenant).count() > 0 and not reset:
            return {"skipped": True, "reason": "Data already present. Use --reset to rebuild."}

        # --- Tenant 1: the demo club ---------------------------------------
        club = Tenant(
            slug="demo-fc", name="Demo FC", country="ESP", plan=Plan.elite,
            transfer_budget_eur=140_000_000, wage_budget_eur_per_year=28_000_000,
        )
        db.add(club)
        db.flush()

        users = [
            ("owner@demo.fc", "Directora deportiva", Role.owner),
            ("analyst@demo.fc", "Analista táctico", Role.analyst),
            ("scout@demo.fc", "Jefe de scouting", Role.scout),
            ("academy@demo.fc", "Coordinador de cantera", Role.academy_coach),
        ]
        for email, name, role in users:
            db.add(User(
                tenant_id=club.id, email=email, full_name=name, role=role,
                password_hash=hash_password("elevenmetric"),
            ))

        # --- A second tenant, so isolation is visible ----------------------
        rival = Tenant(slug="rival-united", name="Rival United", country="ENG", plan=Plan.pro,
                       transfer_budget_eur=40_000_000, wage_budget_eur_per_year=9_000_000)
        db.add(rival)
        db.flush()
        db.add(User(
            tenant_id=rival.id, email="owner@rival.united", full_name="Rival owner",
            role=Role.owner, password_hash=hash_password("elevenmetric"),
        ))
        db.add(Player(tenant_id=rival.id, name="Rival Striker", primary_position=Position.ST,
                      overall_rating=80))

        # --- Teams ---------------------------------------------------------
        first_team = Team(
            tenant_id=club.id, slug="demo-fc-first", name="Demo FC", short_name="DEM",
            country="ESP", competition="LaLiga", kind="first_team",
            primary_color="#a50044", secondary_color="#004d98", default_formation="4-3-3",
        )
        academy_team = Team(
            tenant_id=club.id, slug="demo-fc-academy", name="Demo FC Academy",
            short_name="DEM-A", country="ESP", competition="División de Honor Juvenil",
            kind="academy", default_formation="4-3-3",
        )
        db.add_all([first_team, academy_team])
        db.flush()

        # --- Senior squad ---------------------------------------------------
        players: list[Player] = []
        for name, shirt, position, overall, potential, age, foot, _starter, _slot in SQUAD:
            minutes_7d, fatigue, fitness = LOAD_PROFILE.get(name, (90, 20, 95))
            p = Player(
                tenant_id=club.id, team_id=first_team.id, name=name,
                known_as=name.split()[-1] if len(name.split()) > 1 else name,
                shirt_number=shirt, birth_date=_birth(age), nationality="ESP",
                primary_position=position, preferred_foot=foot,
                overall_rating=overall, potential_rating=potential,
                attributes=_attributes(position, overall),
                height_cm=RNG.randint(170, 195), weight_kg=RNG.randint(66, 88),
                market_value_eur=_market_value(overall, age),
                wage_eur_per_year=int(_market_value(overall, age) * 0.11),
                contract_until=date(2027 + RNG.randint(0, 3), 6, 30),
                fitness=fitness,
                fatigue=fatigue,
                minutes_last_7d=minutes_7d,
                injury_risk=round(0.02 + minutes_7d / 270 * 0.16, 3),
                is_available=name not in UNAVAILABLE,
            )
            players.append(p)
            db.add(p)
        db.flush()

        # --- Matchday lineup ------------------------------------------------
        lineup = Lineup(
            tenant_id=club.id, team_id=first_team.id, name="Starting XI",
            formation="4-3-3", is_template=True,
            build_up="short", defensive_line_height=72, pressing_intensity=78,
            defensive_width=46, attacking_width=68, tempo=64,
            counter_press=True, offside_trap=True,
        )
        db.add(lineup)
        db.flush()

        from app.models.catalog import POSITION_ANCHOR

        for (name, _shirt, position, *_rest), player in zip(SQUAD, players):
            row = next(s for s in SQUAD if s[0] == name)
            is_starter, slot_index = row[7], row[8]
            if slot_index is None:
                continue
            anchor = POSITION_ANCHOR[position]
            db.add(LineupSlot(
                tenant_id=club.id, lineup_id=lineup.id, player_id=player.id,
                slot_index=slot_index, is_starter=is_starter, position=position,
                is_captain=(name == "Alessandro Bastoni"),
                x=anchor[0] / 105.0, y=anchor[1] / 68.0,
            ))

        # --- A match, with simulated events and tracking ---------------------
        match = Match(
            tenant_id=club.id, team_id=first_team.id, opponent_name="Rival United",
            competition="LaLiga", season="2025/26", venue="home",
            kickoff_at=datetime.now(timezone.utc) - timedelta(days=3),
            state=MatchState.finished, source=InputSource.tracking,
            provider="elevenmetric-sim",
        )
        db.add(match)
        db.flush()

        starters = [p for p, row in zip(players, SQUAD) if row[7] and row[8] is not None]
        sim = simulate_match(
            home_player_ids=[p.id for p in starters],
            home_formation="4-3-3", away_formation="4-4-2",
            minutes=90, frame_hz=5.0, home_strength=0.58, home_press_height=0.68,
        )

        goals_for = sum(1 for e in sim.events if e.type == "shot" and e.outcome == "goal" and e.is_own_team)
        goals_against = sum(1 for e in sim.events if e.type == "shot" and e.outcome == "goal" and not e.is_own_team)
        match.goals_for, match.goals_against = goals_for, goals_against

        db.add_all([
            MatchEvent(
                tenant_id=club.id, match_id=match.id, period=e.period, minute=e.minute,
                second=e.second, type=e.type, outcome=e.outcome, is_own_team=e.is_own_team,
                player_id=e.player_id if e.is_own_team else None,
                x=e.x, y=e.y, end_x=e.end_x, end_y=e.end_y, qualifiers=e.qualifiers,
            )
            for e in sim.events
        ])
        db.add_all([
            TrackingFrame(
                tenant_id=club.id, match_id=match.id, period=f.period,
                timestamp_ms=f.timestamp_ms, home_positions=f.home_positions,
                away_positions=f.away_positions, ball=f.ball,
                possession_team=f.possession_team,
            )
            for f in sim.frames
        ])
        lineup_for_match = Lineup(
            tenant_id=club.id, team_id=first_team.id, match_id=match.id,
            name="Matchday XI", formation="4-3-3",
            build_up="short", defensive_line_height=72, pressing_intensity=78,
        )
        db.add(lineup_for_match)
        db.flush()
        for (name, _s, position, *_r), player in zip(SQUAD, players):
            row = next(s for s in SQUAD if s[0] == name)
            if row[8] is None:
                continue
            anchor = POSITION_ANCHOR[position]
            db.add(LineupSlot(
                tenant_id=club.id, lineup_id=lineup_for_match.id, player_id=player.id,
                slot_index=row[8], is_starter=row[7], position=position,
                x=anchor[0] / 105.0, y=anchor[1] / 68.0,
            ))

        # --- Academy ---------------------------------------------------------
        for name, position, group, age, ability, potential, bio in ACADEMY_NAMES:
            youth = AcademyPlayer(
                tenant_id=club.id, team_id=academy_team.id, name=name,
                birth_date=_birth(age), nationality="ESP", age_group=group,
                primary_position=position, current_ability=ability,
                potential_ability=potential, biological_age_offset=bio,
                height_cm=RNG.randint(165, 190),
                predicted_adult_height_cm=RNG.randint(175, 195),
                joined_academy_on=date.today() - timedelta(days=RNG.randint(400, 2600)),
                contract_until=date(2027 + RNG.randint(0, 2), 6, 30),
                minutes_this_season=RNG.randint(600, 2200),
                senior_minutes=RNG.choice([0, 0, 0, 45, 90, 210]),
            )
            db.add(youth)
            db.flush()

            # Four assessments over the last 18 months, on an upward trend.
            n = 4
            growth = RNG.uniform(1.5, 6.5)
            for i in range(n):
                months_ago = (n - 1 - i) * 6
                when = date.today() - timedelta(days=int(months_ago * 30.44))
                value = ability - growth * (months_ago / 12.0) + RNG.uniform(-1.2, 1.2)
                db.add(AcademyAssessment(
                    tenant_id=club.id, academy_player_id=youth.id, assessed_on=when,
                    assessed_by="Academy staff",
                    ability=round(max(30, min(99, value)), 1),
                    technical=round(max(30, min(99, value + RNG.uniform(-4, 5))), 1),
                    tactical=round(max(30, min(99, value + RNG.uniform(-6, 4))), 1),
                    physical=round(max(30, min(99, value + bio * 4 + RNG.uniform(-5, 5))), 1),
                    mental=round(max(30, min(99, value + RNG.uniform(-4, 4))), 1),
                    sprint_10m_s=round(RNG.uniform(1.62, 1.86), 2),
                    sprint_30m_s=round(RNG.uniform(3.95, 4.45), 2),
                    yoyo_ir1_m=RNG.randint(1600, 2600),
                    cmj_cm=round(RNG.uniform(34, 52), 1),
                    minutes_since_last=RNG.randint(200, 900),
                    goals_since_last=RNG.randint(0, 9),
                    assists_since_last=RNG.randint(0, 7),
                    level=RNG.choice(["academy", "academy", "reserves", "senior"]),
                ))

        # --- Market pool ------------------------------------------------------
        # Four candidates per position the club fields, at a spread of price
        # points, so a detected need always has real options and the budget
        # optimiser has something to choose between.
        market_rows = []
        covered_positions = [
            Position.GK, Position.LB, Position.LCB, Position.RCB, Position.RB,
            Position.DM, Position.CM, Position.AM, Position.LW, Position.RW,
            Position.ST, Position.CB,
        ]
        for slot, position in enumerate(covered_positions):
            for k in range(4):
                idx = slot * 4 + k
                _, club_name, league, tier = MARKET_NAMES[idx % len(MARKET_NAMES)]
                name = (
                    MARKET_NAMES[idx][0] if idx < len(MARKET_NAMES)
                    else f"{EXTRA_FIRST[idx % len(EXTRA_FIRST)]} "
                         f"{EXTRA_LAST[(idx * 7) % len(EXTRA_LAST)]}"
                )
                market_rows.append((name, club_name, league, tier, position, k))

        for name, club_name, league, tier, position, band in market_rows:
            age = RNG.uniform(17.5, 33.0)
            # Top-tier leagues carry genuine upgrades on an elite squad; lower
            # tiers carry value and potential rather than immediate quality.
            # `band` spreads the four candidates per position across price points.
            ceiling = {1: 95.0, 2: 91.0, 3: 88.0, 4: 85.0, 5: 82.0}[tier]
            overall = ceiling - band * 4.0 - RNG.uniform(0, 3.0)
            potential = min(99, overall + max(0, (25 - age)) * RNG.uniform(0.6, 1.6))
            price = _market_value(overall, age)
            db.add(MarketPlayer(
                tenant_id=club.id, name=name, current_club=club_name, league=league,
                league_tier=tier, birth_date=_birth(age), primary_position=position,
                preferred_foot=RNG.choice(list(Foot)),
                overall_rating=round(overall, 1), potential_rating=round(potential, 1),
                attributes=_attributes(position, overall),
                minutes_last_season=RNG.randint(400, 3100),
                asking_price_eur=price,
                wage_demand_eur_per_year=int(price * RNG.uniform(0.09, 0.16)),
                agent_fee_pct=round(RNG.uniform(0.03, 0.12), 3),
                contract_until=date(2026 + RNG.randint(0, 4), 6, 30),
                release_clause_eur=int(price * RNG.uniform(1.1, 2.2)) if RNG.random() < 0.4 else None,
                deal_type=RNG.choices(
                    [DealType.permanent, DealType.loan, DealType.free, DealType.loan_with_option],
                    weights=[0.7, 0.12, 0.08, 0.10],
                )[0],
                injury_history_days_2y=RNG.choice([0, 0, 12, 30, 75, 140]),
                availability=round(RNG.uniform(0.25, 0.95), 2),
            ))

        db.commit()

        return {
            "tenants": ["demo-fc", "rival-united"],
            "login": {"email": "owner@demo.fc", "password": "elevenmetric"},
            "team_id": first_team.id,
            "academy_team_id": academy_team.id,
            "match_id": match.id,
            "lineup_id": lineup.id,
            "players": len(players),
            "events": len(sim.events),
            "tracking_frames": len(sim.frames),
            "academy_players": len(ACADEMY_NAMES),
            "market_players": len(market_rows),
        }
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the ElevenMetric demo database")
    parser.add_argument("--reset", action="store_true", help="Drop every table first")
    args = parser.parse_args()

    summary = seed(reset=args.reset)
    print("Seed complete:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
