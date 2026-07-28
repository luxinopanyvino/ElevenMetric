"""Tenant isolation.

The rule this module enforces: **a query against a tenant-scoped table must
carry a tenant filter.** Rather than trusting every route to remember, routes
go through :class:`TenantScope`, which is the only sanctioned way to build a
statement for a scoped model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from app.db.base_class import TenantScoped

T = TypeVar("T")


class CrossTenantAccess(Exception):
    """Raised when an object from another tenant is reached. Never surfaced to
    the client as-is — the API layer turns it into a 404 so tenant existence
    does not leak."""


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    tenant_slug: str
    user_id: str | None = None
    role: str = "viewer"
    is_superuser: bool = False


class TenantScope:
    """Session wrapper bound to exactly one tenant."""

    def __init__(self, db: Session, ctx: TenantContext) -> None:
        self.db = db
        self.ctx = ctx

    # --- Query builders ----------------------------------------------------
    def select(self, model: type[T], *criteria: Any) -> Select:
        stmt = select(model)
        if issubclass(model, TenantScoped):
            stmt = stmt.where(model.tenant_id == self.ctx.tenant_id)  # type: ignore[attr-defined]
        if criteria:
            stmt = stmt.where(*criteria)
        return stmt

    def all(self, model: type[T], *criteria: Any, limit: int | None = None,
            offset: int = 0, order_by: Any = None) -> list[T]:
        stmt = self.select(model, *criteria)
        if order_by is not None:
            # Accept a single column or a tuple/list of them.
            clauses = order_by if isinstance(order_by, (tuple, list)) else (order_by,)
            stmt = stmt.order_by(*clauses)
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def get(self, model: type[T], obj_id: str) -> T | None:
        return self.db.scalars(self.select(model, model.id == obj_id)).first()  # type: ignore[attr-defined]

    def get_or_raise(self, model: type[T], obj_id: str) -> T:
        obj = self.get(model, obj_id)
        if obj is None:
            raise CrossTenantAccess(f"{model.__name__} {obj_id} not visible to tenant")
        return obj

    def first(self, model: type[T], *criteria: Any) -> T | None:
        return self.db.scalars(self.select(model, *criteria)).first()

    def count(self, model: type[T], *criteria: Any) -> int:
        """Row count via SQL.

        Counting by loading the rows and taking ``len`` is fine until a table
        holds 27,000 tracking frames with a JSON blob each, at which point the
        dashboard's counters stall for seconds.
        """
        stmt = select(func.count()).select_from(model)
        if issubclass(model, TenantScoped):
            stmt = stmt.where(model.tenant_id == self.ctx.tenant_id)  # type: ignore[attr-defined]
        if criteria:
            stmt = stmt.where(*criteria)
        return int(self.db.scalar(stmt) or 0)

    # --- Writes ------------------------------------------------------------
    def add(self, obj: T) -> T:
        if isinstance(obj, TenantScoped):
            current = getattr(obj, "tenant_id", None)
            if current and current != self.ctx.tenant_id:
                raise CrossTenantAccess("refusing to write an object owned by another tenant")
            obj.tenant_id = self.ctx.tenant_id  # type: ignore[attr-defined]
        self.db.add(obj)
        return obj

    def add_all(self, objs: list[T]) -> list[T]:
        for o in objs:
            self.add(o)
        return objs

    def delete(self, obj: Any) -> None:
        if isinstance(obj, TenantScoped) and obj.tenant_id != self.ctx.tenant_id:
            raise CrossTenantAccess("refusing to delete an object owned by another tenant")
        self.db.delete(obj)

    def delete_where(self, model: type[T], *criteria: Any) -> None:
        stmt = delete(model)
        if issubclass(model, TenantScoped):
            stmt = stmt.where(model.tenant_id == self.ctx.tenant_id)  # type: ignore[attr-defined]
        if criteria:
            stmt = stmt.where(*criteria)
        self.db.execute(stmt)

    def commit(self) -> None:
        self.db.commit()

    def flush(self) -> None:
        self.db.flush()

    def refresh(self, obj: Any) -> None:
        self.db.refresh(obj)
