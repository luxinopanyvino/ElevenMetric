"""Matches, lineups, event streams and tracking frames."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TenantScoped, Timestamped, UUIDPk
from app.models.catalog import Position


class MatchState(str, enum.Enum):
    scheduled = "scheduled"
    live = "live"
    finished = "finished"


class InputSource(str, enum.Enum):
    """How the truth for this match arrived. Drives which model family runs."""

    manual = "manual"          # a lineup typed into the UI — no match data
    event_data = "event_data"  # Opta/StatsBomb/Wyscout-style event feed
    tracking = "tracking"      # 10-25 Hz optical/GPS tracking
    video = "video"            # raw broadcast/tactical video → CV pipeline


class Match(UUIDPk, Timestamped, TenantScoped, Base):
    __tablename__ = "matches"

    team_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    opponent_team_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("teams.id", ondelete="SET NULL"), default=None
    )
    opponent_name: Mapped[str] = mapped_column(String(160), default="Opponent")

    competition: Mapped[str] = mapped_column(String(120), default="Friendly")
    season: Mapped[str] = mapped_column(String(16), default="2025/26")
    kickoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    venue: Mapped[str] = mapped_column(String(16), default="home")  # home | away | neutral
    state: Mapped[MatchState] = mapped_column(Enum(MatchState), default=MatchState.scheduled)

    goals_for: Mapped[int] = mapped_column(Integer, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, default=0)

    source: Mapped[InputSource] = mapped_column(Enum(InputSource), default=InputSource.manual)
    #: Provider name + feed version, so a re-ingest is reproducible.
    provider: Mapped[str | None] = mapped_column(String(80), default=None)

    pitch_length_m: Mapped[float] = mapped_column(Float, default=105.0)
    pitch_width_m: Mapped[float] = mapped_column(Float, default=68.0)

    notes: Mapped[str] = mapped_column(Text, default="")

    #: Where this fixture came from, when an external source supplied it.
    #: Empty for matches created by hand. See app/services/external/base.py.
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)

    lineups: Mapped[list["Lineup"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )
    events: Mapped[list["MatchEvent"]] = relationship(
        back_populates="match", cascade="all, delete-orphan", passive_deletes=True
    )


class Lineup(UUIDPk, Timestamped, TenantScoped, Base):
    """A shape at a point in time. A match holds the planned XI plus every
    in-game change, so tactical drift is auditable."""

    __tablename__ = "lineups"

    match_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("matches.id", ondelete="CASCADE"), index=True, default=None
    )
    team_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), default="Starting XI")
    formation: Mapped[str] = mapped_column(String(16), default="4-3-3")
    #: Minute this shape came into effect (0 = kickoff).
    from_minute: Mapped[int] = mapped_column(Integer, default=0)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- Team instructions (mirror of the reference UI's INSTRUCTIONS tab) ---
    build_up: Mapped[str] = mapped_column(String(24), default="balanced")     # short | balanced | direct
    defensive_line_height: Mapped[int] = mapped_column(Integer, default=50)    # 0-100
    defensive_width: Mapped[int] = mapped_column(Integer, default=50)
    pressing_intensity: Mapped[int] = mapped_column(Integer, default=50)
    attacking_width: Mapped[int] = mapped_column(Integer, default=50)
    tempo: Mapped[int] = mapped_column(Integer, default=50)
    counter_press: Mapped[bool] = mapped_column(Boolean, default=True)
    offside_trap: Mapped[bool] = mapped_column(Boolean, default=False)

    match: Mapped["Match | None"] = relationship(back_populates="lineups")
    slots: Mapped[list["LineupSlot"]] = relationship(
        back_populates="lineup", cascade="all, delete-orphan", order_by="LineupSlot.slot_index"
    )

    @property
    def starters(self) -> list["LineupSlot"]:
        return [s for s in self.slots if s.is_starter]

    @property
    def bench(self) -> list["LineupSlot"]:
        return [s for s in self.slots if not s.is_starter]


class LineupSlot(UUIDPk, Timestamped, TenantScoped, Base):
    __tablename__ = "lineup_slots"

    lineup_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("lineups.id", ondelete="CASCADE"), index=True
    )
    player_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("players.id", ondelete="CASCADE"), index=True
    )
    slot_index: Mapped[int] = mapped_column(Integer, default=0)
    is_starter: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[Position] = mapped_column(Enum(Position), default=Position.CM)
    #: Role within the position, e.g. "inverted_winger", "ball_playing_defender".
    role: Mapped[str] = mapped_column(String(48), default="balanced")
    #: Free role duty: "attack" | "support" | "defend".
    duty: Mapped[str] = mapped_column(String(16), default="support")
    is_captain: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Normalised pitch anchor (0-1 in both axes) so the UI can render any shape.
    x: Mapped[float] = mapped_column(Float, default=0.5)
    y: Mapped[float] = mapped_column(Float, default=0.5)

    lineup: Mapped[Lineup] = relationship(back_populates="slots")


class MatchEvent(UUIDPk, TenantScoped, Base):
    """One row per on-ball event. This is the minimum viable input for
    possession, field tilt, PPDA, xG and xT — see docs/DATA_MODEL.md."""

    __tablename__ = "match_events"
    __table_args__ = (
        Index("ix_event_match_minute", "match_id", "minute", "second"),
        Index("ix_event_match_type", "match_id", "type"),
    )

    match_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("matches.id", ondelete="CASCADE"), index=True
    )
    team_id: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    #: True when the event belongs to the tenant's own team.
    is_own_team: Mapped[bool] = mapped_column(Boolean, default=True)
    player_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("players.id", ondelete="SET NULL"), default=None
    )

    period: Mapped[int] = mapped_column(Integer, default=1)
    minute: Mapped[int] = mapped_column(Integer, default=0)
    second: Mapped[int] = mapped_column(Integer, default=0)

    #: pass, carry, shot, dribble, duel, tackle, interception, pressure,
    #: clearance, recovery, foul, save, sub_on, sub_off, card.
    type: Mapped[str] = mapped_column(String(32), index=True)
    outcome: Mapped[str] = mapped_column(String(32), default="success")

    x: Mapped[float] = mapped_column(Float, default=0.0)       # metres, 0..pitch_length
    y: Mapped[float] = mapped_column(Float, default=0.0)       # metres, 0..pitch_width
    end_x: Mapped[float | None] = mapped_column(Float, default=None)
    end_y: Mapped[float | None] = mapped_column(Float, default=None)

    #: Set by the analytics layer when the report runs.
    xg: Mapped[float | None] = mapped_column(Float, default=None)
    xt_delta: Mapped[float | None] = mapped_column(Float, default=None)

    #: Provider-specific qualifiers (body part, pass height, pressure flag…).
    qualifiers: Mapped[dict] = mapped_column(JSON, default=dict)

    match: Mapped[Match] = relationship(back_populates="events")

    @property
    def clock_seconds(self) -> int:
        return self.minute * 60 + self.second


class TrackingFrame(UUIDPk, TenantScoped, Base):
    """Down-sampled positional snapshot (default 5 Hz after decimation).

    ``positions`` is ``{player_id_or_track_id: [x, y]}`` in metres. Storing a
    whole frame as one JSON row keeps write volume manageable; the analytics
    layer streams frames rather than joining per-player rows.
    """

    __tablename__ = "tracking_frames"
    __table_args__ = (Index("ix_frame_match_ts", "match_id", "timestamp_ms"),)

    match_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("matches.id", ondelete="CASCADE"), index=True
    )
    period: Mapped[int] = mapped_column(Integer, default=1)
    timestamp_ms: Mapped[int] = mapped_column(Integer, default=0)

    home_positions: Mapped[dict] = mapped_column(JSON, default=dict)
    away_positions: Mapped[dict] = mapped_column(JSON, default=dict)
    ball: Mapped[list | None] = mapped_column(JSON, default=None)   # [x, y, z?]
    #: "home" | "away" | None — which side is in control at this frame.
    possession_team: Mapped[str | None] = mapped_column(String(8), default=None)
