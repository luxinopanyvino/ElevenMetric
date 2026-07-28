"""Declarative base plus the mixins every table in the system shares."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UUIDPk:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class TenantScoped:
    """Every business table carries the tenant discriminator.

    Isolation is enforced in :mod:`app.core.tenancy`, which refuses to build a
    query for a scoped model without binding ``tenant_id``.
    """

    @property
    def __tenant_column__(self) -> str:  # documentation hook
        return "tenant_id"

    tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
