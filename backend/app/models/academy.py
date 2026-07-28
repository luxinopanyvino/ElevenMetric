"""Academy: youth players and their periodic assessments."""

from __future__ import annotations

import enum
from datetime import date

from sqlalchemy import Date, Enum, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TenantScoped, Timestamped, UUIDPk
from app.models.catalog import Foot, Position


class AgeGroup(str, enum.Enum):
    u12 = "U12"
    u14 = "U14"
    u16 = "U16"
    u18 = "U18"
    u19 = "U19"
    u21 = "U21"
    u23 = "U23"


class Pathway(str, enum.Enum):
    """The recommendation the academy engine converges on."""

    promote_now = "promote_now"
    train_with_first_team = "train_with_first_team"
    loan_out = "loan_out"
    continue_academy = "continue_academy"
    review = "review"
    release = "release"


class AcademyPlayer(UUIDPk, Timestamped, TenantScoped, Base):
    __tablename__ = "academy_players"

    team_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("teams.id", ondelete="SET NULL"), default=None, index=True
    )
    #: Set once promoted, linking to the senior squad row.
    senior_player_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("players.id", ondelete="SET NULL"), default=None
    )

    name: Mapped[str] = mapped_column(String(160), index=True)
    birth_date: Mapped[date | None] = mapped_column(Date, default=None)
    nationality: Mapped[str | None] = mapped_column(String(3), default=None)
    age_group: Mapped[AgeGroup] = mapped_column(Enum(AgeGroup), default=AgeGroup.u18)
    primary_position: Mapped[Position] = mapped_column(Enum(Position), default=Position.CM)
    secondary_positions: Mapped[list] = mapped_column(JSON, default=list)
    preferred_foot: Mapped[Foot] = mapped_column(Enum(Foot), default=Foot.right)

    joined_academy_on: Mapped[date | None] = mapped_column(Date, default=None)
    contract_until: Mapped[date | None] = mapped_column(Date, default=None)
    homegrown: Mapped[bool] = mapped_column(default=True)

    #: Current and ceiling ability, 0-99, same scale as the senior squad.
    current_ability: Mapped[float] = mapped_column(Float, default=55.0)
    potential_ability: Mapped[float] = mapped_column(Float, default=72.0)

    height_cm: Mapped[int | None] = mapped_column(Integer, default=None)
    #: Predicted adult height — growth spurts distort physical assessments.
    predicted_adult_height_cm: Mapped[int | None] = mapped_column(Integer, default=None)
    #: Skeletal-age minus chronological-age, in years. Negative = late developer,
    #: whose current output under-states their true level (bio-banding).
    biological_age_offset: Mapped[float] = mapped_column(Float, default=0.0)

    minutes_this_season: Mapped[int] = mapped_column(Integer, default=0)
    senior_minutes: Mapped[int] = mapped_column(Integer, default=0)

    # --- Engine output, refreshed by the academy review job ----------------
    readiness_score: Mapped[float] = mapped_column(Float, default=0.0)   # 0-100
    projected_ready_on: Mapped[date | None] = mapped_column(Date, default=None)
    months_to_first_team: Mapped[float | None] = mapped_column(Float, default=None)
    pathway: Mapped[Pathway] = mapped_column(Enum(Pathway), default=Pathway.continue_academy)
    projection: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")

    assessments: Mapped[list["AcademyAssessment"]] = relationship(
        back_populates="player",
        cascade="all, delete-orphan",
        order_by="AcademyAssessment.assessed_on",
    )

    @property
    def age(self) -> float | None:
        if not self.birth_date:
            return None
        return (date.today() - self.birth_date).days / 365.25


class AcademyAssessment(UUIDPk, Timestamped, TenantScoped, Base):
    """A point on the development curve. The projection model needs at least
    three of these spanning six months to fit a trend."""

    __tablename__ = "academy_assessments"

    academy_player_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("academy_players.id", ondelete="CASCADE"), index=True
    )
    assessed_on: Mapped[date] = mapped_column(Date, index=True)
    assessed_by: Mapped[str] = mapped_column(String(160), default="")

    #: Composite 0-99 ability at the time of the assessment.
    ability: Mapped[float] = mapped_column(Float, default=55.0)

    # --- The four pillars academies score on -------------------------------
    technical: Mapped[float] = mapped_column(Float, default=55.0)
    tactical: Mapped[float] = mapped_column(Float, default=55.0)
    physical: Mapped[float] = mapped_column(Float, default=55.0)
    mental: Mapped[float] = mapped_column(Float, default=55.0)

    # --- Objective test battery --------------------------------------------
    sprint_10m_s: Mapped[float | None] = mapped_column(Float, default=None)
    sprint_30m_s: Mapped[float | None] = mapped_column(Float, default=None)
    yoyo_ir1_m: Mapped[int | None] = mapped_column(Integer, default=None)
    cmj_cm: Mapped[float | None] = mapped_column(Float, default=None)

    minutes_since_last: Mapped[int] = mapped_column(Integer, default=0)
    goals_since_last: Mapped[int] = mapped_column(Integer, default=0)
    assists_since_last: Mapped[int] = mapped_column(Integer, default=0)
    #: Level the minutes were played at: "academy" | "reserves" | "senior".
    level: Mapped[str] = mapped_column(String(16), default="academy")

    notes: Mapped[str] = mapped_column(Text, default="")

    player: Mapped[AcademyPlayer] = relationship(back_populates="assessments")
