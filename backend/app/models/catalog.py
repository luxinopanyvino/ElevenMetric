"""Teams, players and their season aggregates."""

from __future__ import annotations

import enum
from datetime import date

from sqlalchemy import Date, Enum, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TenantScoped, Timestamped, UUIDPk


class Position(str, enum.Enum):
    GK = "GK"
    RB = "RB"
    RCB = "RCB"
    CB = "CB"
    LCB = "LCB"
    LB = "LB"
    RWB = "RWB"
    LWB = "LWB"
    DM = "DM"
    CM = "CM"
    AM = "AM"
    RM = "RM"
    LM = "LM"
    RW = "RW"
    LW = "LW"
    CF = "CF"
    ST = "ST"
    SS = "SS"


#: Coarse line grouping used by the formation detector and the transfer engine.
POSITION_LINE: dict[Position, str] = {
    Position.GK: "GK",
    Position.RB: "DEF", Position.RCB: "DEF", Position.CB: "DEF",
    Position.LCB: "DEF", Position.LB: "DEF", Position.RWB: "DEF", Position.LWB: "DEF",
    Position.DM: "MID", Position.CM: "MID", Position.AM: "MID",
    Position.RM: "MID", Position.LM: "MID",
    Position.RW: "ATT", Position.LW: "ATT", Position.CF: "ATT",
    Position.ST: "ATT", Position.SS: "ATT",
}

#: Canonical (x, y) anchor on a 105x68 pitch, attacking left→right.
POSITION_ANCHOR: dict[Position, tuple[float, float]] = {
    Position.GK: (5.0, 34.0),
    Position.RB: (25.0, 60.0), Position.LB: (25.0, 8.0),
    Position.RWB: (38.0, 62.0), Position.LWB: (38.0, 6.0),
    Position.RCB: (20.0, 44.0), Position.CB: (18.0, 34.0), Position.LCB: (20.0, 24.0),
    Position.DM: (38.0, 34.0), Position.CM: (52.0, 34.0), Position.AM: (68.0, 34.0),
    Position.RM: (52.0, 58.0), Position.LM: (52.0, 10.0),
    Position.RW: (80.0, 58.0), Position.LW: (80.0, 10.0),
    Position.CF: (88.0, 34.0), Position.ST: (92.0, 34.0), Position.SS: (80.0, 34.0),
}


class Foot(str, enum.Enum):
    left = "left"
    right = "right"
    both = "both"


class Team(UUIDPk, Timestamped, TenantScoped, Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_team_tenant_slug"),)

    slug: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(160))
    short_name: Mapped[str] = mapped_column(String(24), default="")
    country: Mapped[str | None] = mapped_column(String(3), default=None)
    competition: Mapped[str | None] = mapped_column(String(120), default=None)
    #: "first_team" | "academy" | "opponent" — an opponent team holds scouting data only.
    kind: Mapped[str] = mapped_column(String(24), default="first_team")
    primary_color: Mapped[str] = mapped_column(String(7), default="#2a78d6")
    secondary_color: Mapped[str] = mapped_column(String(7), default="#0b0b0b")
    #: Default shape, e.g. "4-3-3".
    default_formation: Mapped[str] = mapped_column(String(16), default="4-3-3")

    #: Where this team came from, when a source other than the user supplied it.
    #: Empty for teams created by hand. See app/services/external/base.py.
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)

    players: Mapped[list["Player"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )


class Player(UUIDPk, Timestamped, TenantScoped, Base):
    __tablename__ = "players"

    team_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("teams.id", ondelete="SET NULL"), index=True, default=None
    )
    external_ids: Mapped[dict] = mapped_column(JSON, default=dict)

    name: Mapped[str] = mapped_column(String(160), index=True)
    known_as: Mapped[str] = mapped_column(String(80), default="")
    shirt_number: Mapped[int | None] = mapped_column(Integer, default=None)
    birth_date: Mapped[date | None] = mapped_column(Date, default=None)
    nationality: Mapped[str | None] = mapped_column(String(3), default=None)

    primary_position: Mapped[Position] = mapped_column(Enum(Position), default=Position.CM)
    secondary_positions: Mapped[list] = mapped_column(JSON, default=list)
    preferred_foot: Mapped[Foot] = mapped_column(Enum(Foot), default=Foot.right)

    height_cm: Mapped[int | None] = mapped_column(Integer, default=None)
    weight_kg: Mapped[int | None] = mapped_column(Integer, default=None)

    #: 0-99 scouting rating, the scale the reference UI uses.
    #:
    #: Nullable on purpose. Some sources publish a squad with no ratings at all —
    #: a StatsBomb lineup names eleven real players and grades none of them — and
    #: defaulting those to 70 would be the product inventing a measurement. An
    #: unrated player is excluded from every ranking engine, with the reason
    #: reported; see `features.is_rankable`.
    overall_rating: Mapped[float | None] = mapped_column(Float, default=70.0)
    potential_rating: Mapped[float | None] = mapped_column(Float, default=75.0)

    #: Fine-grained attributes: pace, passing, dribbling, defending, physical,
    #: shooting, stamina, aerial, work_rate_off, work_rate_def (all 0-99).
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)

    market_value_eur: Mapped[int] = mapped_column(Integer, default=0)
    wage_eur_per_year: Mapped[int] = mapped_column(Integer, default=0)
    contract_until: Mapped[date | None] = mapped_column(Date, default=None)

    #: Rolling readiness signals fed by the load monitoring integration.
    fitness: Mapped[float] = mapped_column(Float, default=100.0)   # 0-100
    fatigue: Mapped[float] = mapped_column(Float, default=0.0)     # 0-100
    injury_risk: Mapped[float] = mapped_column(Float, default=0.05)  # 0-1
    minutes_last_7d: Mapped[int] = mapped_column(Integer, default=0)
    is_available: Mapped[bool] = mapped_column(default=True)

    #: Where this player came from, when a source other than the user supplied
    #: them. Empty for players created by hand or imported from the club's own
    #: CSV. See app/services/external/base.py.
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)

    team: Mapped["Team | None"] = relationship(back_populates="players")
    season_stats: Mapped[list["PlayerSeasonStat"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        return self.known_as or self.name

    @property
    def age(self) -> float | None:
        if not self.birth_date:
            return None
        return (date.today() - self.birth_date).days / 365.25

    @property
    def line(self) -> str:
        return POSITION_LINE[self.primary_position]


class PlayerSeasonStat(UUIDPk, Timestamped, TenantScoped, Base):
    """Per-competition season totals. The ML feature store reads from here."""

    __tablename__ = "player_season_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "season", "competition", name="uq_stat_player_season_comp"),
    )

    player_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("players.id", ondelete="CASCADE"), index=True
    )
    season: Mapped[str] = mapped_column(String(16))          # "2025/26"
    competition: Mapped[str] = mapped_column(String(120), default="all")

    appearances: Mapped[int] = mapped_column(Integer, default=0)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    xg: Mapped[float] = mapped_column(Float, default=0.0)
    xa: Mapped[float] = mapped_column(Float, default=0.0)
    shots: Mapped[int] = mapped_column(Integer, default=0)
    key_passes: Mapped[int] = mapped_column(Integer, default=0)
    passes_completed: Mapped[int] = mapped_column(Integer, default=0)
    passes_attempted: Mapped[int] = mapped_column(Integer, default=0)
    progressive_passes: Mapped[int] = mapped_column(Integer, default=0)
    progressive_carries: Mapped[int] = mapped_column(Integer, default=0)
    duels_won: Mapped[int] = mapped_column(Integer, default=0)
    duels_total: Mapped[int] = mapped_column(Integer, default=0)
    tackles: Mapped[int] = mapped_column(Integer, default=0)
    interceptions: Mapped[int] = mapped_column(Integer, default=0)
    pressures: Mapped[int] = mapped_column(Integer, default=0)
    distance_km: Mapped[float] = mapped_column(Float, default=0.0)
    high_intensity_km: Mapped[float] = mapped_column(Float, default=0.0)
    yellow_cards: Mapped[int] = mapped_column(Integer, default=0)
    red_cards: Mapped[int] = mapped_column(Integer, default=0)

    player: Mapped[Player] = relationship(back_populates="season_stats")

    @property
    def per90(self) -> dict[str, float]:
        if self.minutes <= 0:
            return {}
        f = 90.0 / self.minutes
        return {
            "goals": self.goals * f,
            "assists": self.assists * f,
            "xg": self.xg * f,
            "xa": self.xa * f,
            "key_passes": self.key_passes * f,
            "progressive_passes": self.progressive_passes * f,
            "progressive_carries": self.progressive_carries * f,
            "tackles": self.tackles * f,
            "interceptions": self.interceptions * f,
            "pressures": self.pressures * f,
        }
