"""Transfer market: the scouted pool, shortlists and evaluated targets."""

from __future__ import annotations

import enum
from datetime import date

from sqlalchemy import Date, Enum, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TenantScoped, Timestamped, UUIDPk
from app.models.catalog import Foot, Position


class DealType(str, enum.Enum):
    permanent = "permanent"
    loan = "loan"
    free = "free"
    loan_with_option = "loan_with_option"


class MarketPlayer(UUIDPk, Timestamped, TenantScoped, Base):
    """A player available on the market.

    Kept separate from :class:`~app.models.catalog.Player` because the market
    pool is provider-sourced, far larger, and carries commercial fields the
    squad table has no use for.
    """

    __tablename__ = "market_players"

    external_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    name: Mapped[str] = mapped_column(String(160), index=True)
    current_club: Mapped[str] = mapped_column(String(160), default="")
    league: Mapped[str] = mapped_column(String(120), default="")
    #: 1 (top-5 league) … 5 (lower tier). Scales the league-strength adjustment.
    league_tier: Mapped[int] = mapped_column(Integer, default=3)
    nationality: Mapped[str | None] = mapped_column(String(3), default=None)
    birth_date: Mapped[date | None] = mapped_column(Date, default=None)

    primary_position: Mapped[Position] = mapped_column(Enum(Position), default=Position.CM)
    secondary_positions: Mapped[list] = mapped_column(JSON, default=list)
    preferred_foot: Mapped[Foot] = mapped_column(Enum(Foot), default=Foot.right)

    overall_rating: Mapped[float] = mapped_column(Float, default=70.0)
    potential_rating: Mapped[float] = mapped_column(Float, default=75.0)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Per-90 production, same keys as PlayerSeasonStat.per90.
    per90: Mapped[dict] = mapped_column(JSON, default=dict)
    minutes_last_season: Mapped[int] = mapped_column(Integer, default=0)

    asking_price_eur: Mapped[int] = mapped_column(Integer, default=0)
    wage_demand_eur_per_year: Mapped[int] = mapped_column(Integer, default=0)
    agent_fee_pct: Mapped[float] = mapped_column(Float, default=0.05)
    contract_until: Mapped[date | None] = mapped_column(Date, default=None)
    release_clause_eur: Mapped[int | None] = mapped_column(Integer, default=None)
    deal_type: Mapped[DealType] = mapped_column(Enum(DealType), default=DealType.permanent)

    injury_history_days_2y: Mapped[int] = mapped_column(Integer, default=0)
    #: 0-1 subjective likelihood the deal can actually be done.
    availability: Mapped[float] = mapped_column(Float, default=0.6)
    #: 0-1 fit with the club's style, filled by the recommender.
    homegrown: Mapped[bool] = mapped_column(default=False)

    @property
    def age(self) -> float | None:
        if not self.birth_date:
            return None
        return (date.today() - self.birth_date).days / 365.25

    @property
    def total_cost_eur(self) -> int:
        """Fee + agent fee. Wages are budgeted separately."""
        return int(self.asking_price_eur * (1 + self.agent_fee_pct))


class TransferShortlist(UUIDPk, Timestamped, TenantScoped, Base):
    __tablename__ = "transfer_shortlists"

    name: Mapped[str] = mapped_column(String(160), default="Summer window")
    team_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("teams.id", ondelete="CASCADE"), default=None
    )
    window: Mapped[str] = mapped_column(String(32), default="summer_2026")
    budget_eur: Mapped[int] = mapped_column(Integer, default=0)
    wage_budget_eur_per_year: Mapped[int] = mapped_column(Integer, default=0)
    #: Positions the analysis flagged, e.g. {"LB": 0.82, "DM": 0.61}.
    needs: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")

    targets: Mapped[list["TransferTarget"]] = relationship(
        back_populates="shortlist", cascade="all, delete-orphan"
    )


class TransferTarget(UUIDPk, Timestamped, TenantScoped, Base):
    """A scored market player against a specific squad need."""

    __tablename__ = "transfer_targets"

    shortlist_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("transfer_shortlists.id", ondelete="CASCADE"), index=True
    )
    market_player_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("market_players.id", ondelete="CASCADE"), index=True
    )

    target_position: Mapped[Position] = mapped_column(Enum(Position), default=Position.CM)
    #: 0-100 composite from the recommender.
    fit_score: Mapped[float] = mapped_column(Float, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    value_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    composite_score: Mapped[float] = mapped_column(Float, default=0.0)

    #: Projected first-team rating uplift at this position.
    projected_upgrade: Mapped[float] = mapped_column(Float, default=0.0)
    #: Fee + agent fee, after applying any release clause.
    effective_cost_eur: Mapped[int] = mapped_column(Integer, default=0)
    #: True when the optimiser included it in the recommended bundle.
    selected: Mapped[bool] = mapped_column(default=False)
    rationale: Mapped[list] = mapped_column(JSON, default=list)

    shortlist: Mapped[TransferShortlist] = relationship(back_populates="targets")
    market_player: Mapped[MarketPlayer] = relationship()
