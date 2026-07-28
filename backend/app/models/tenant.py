"""Tenants, users and machine credentials."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TenantScoped, Timestamped, UUIDPk


class Plan(str, enum.Enum):
    """Feature gate. Video ingestion is expensive, so it is plan-limited."""

    starter = "starter"
    pro = "pro"
    elite = "elite"


class Role(str, enum.Enum):
    owner = "owner"
    analyst = "analyst"
    scout = "scout"
    academy_coach = "academy_coach"
    viewer = "viewer"


#: Coarse capability map consulted by :func:`app.api.deps.require_role`.
ROLE_CAPABILITIES: dict[Role, set[str]] = {
    Role.owner: {"*"},
    Role.analyst: {
        "match:read", "match:write", "analysis:read", "analysis:run",
        "video:upload", "squad:read", "squad:write", "academy:read",
        "transfer:read",
    },
    Role.scout: {"transfer:read", "transfer:write", "squad:read", "analysis:read", "academy:read"},
    Role.academy_coach: {"academy:read", "academy:write", "squad:read", "analysis:read"},
    Role.viewer: {"match:read", "analysis:read", "squad:read", "transfer:read", "academy:read"},
}

PLAN_LIMITS: dict[Plan, dict[str, int]] = {
    Plan.starter: {"video_minutes_per_month": 60, "max_teams": 1, "max_users": 3},
    Plan.pro: {"video_minutes_per_month": 900, "max_teams": 5, "max_users": 25},
    Plan.elite: {"video_minutes_per_month": 10_000, "max_teams": 50, "max_users": 250},
}


class Tenant(UUIDPk, Timestamped, Base):
    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    country: Mapped[str | None] = mapped_column(String(3), default=None)
    plan: Mapped[Plan] = mapped_column(Enum(Plan), default=Plan.pro)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    #: Club finances that the transfer engine constrains against (EUR).
    transfer_budget_eur: Mapped[int] = mapped_column(Integer, default=0)
    wage_budget_eur_per_year: Mapped[int] = mapped_column(Integer, default=0)

    video_minutes_used: Mapped[int] = mapped_column(Integer, default=0)

    users: Mapped[list["User"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")

    @property
    def limits(self) -> dict[str, int]:
        return PLAN_LIMITS[self.plan]


class User(UUIDPk, Timestamped, TenantScoped, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),)

    email: Mapped[str] = mapped_column(String(255), index=True)
    full_name: Mapped[str] = mapped_column(String(160), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.viewer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Platform staff; may switch tenants via the tenant header.
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    tenant: Mapped[Tenant] = relationship(back_populates="users")

    def can(self, capability: str) -> bool:
        caps = ROLE_CAPABILITIES.get(self.role, set())
        return "*" in caps or capability in caps


class ApiKey(UUIDPk, Timestamped, TenantScoped, Base):
    """Server-to-server credential used by data-provider ingest jobs."""

    __tablename__ = "api_keys"

    label: Mapped[str] = mapped_column(String(120))
    key_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_by: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
