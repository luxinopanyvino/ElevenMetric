"""FastAPI dependencies: authentication, tenant resolution, authorisation."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import api_key_digest, decode_access_token
from app.core.tenancy import TenantContext, TenantScope
from app.db.session import get_db
from app.models.tenant import ApiKey, Plan, Role, Tenant, User

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_v1_prefix}/auth/login", auto_error=False
)

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def _tenant_by_id(db: Session, tenant_id: str) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(status_code=403, detail="Tenant inactive or unknown")
    return tenant


def get_current_principal(
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    x_tenant: Annotated[str | None, Header(alias="X-Tenant")] = None,
) -> TenantContext:
    """Resolve the caller to a tenant.

    Two credential types are accepted: a user bearer token, and a machine API
    key used by ingest jobs. The tenant always comes from the credential; the
    ``X-Tenant`` header may only *narrow* for platform superusers.
    """
    if x_api_key:
        key = db.scalars(
            select(ApiKey).where(
                ApiKey.key_digest == api_key_digest(x_api_key),
                ApiKey.revoked_at.is_(None),
            )
        ).first()
        if key is None:
            raise _CREDENTIALS_ERROR
        tenant = _tenant_by_id(db, key.tenant_id)
        return TenantContext(
            tenant_id=tenant.id, tenant_slug=tenant.slug, user_id=None, role=Role.analyst.value
        )

    if not token:
        raise _CREDENTIALS_ERROR

    payload = decode_access_token(token)
    if not payload:
        raise _CREDENTIALS_ERROR

    user = db.get(User, payload.get("sub", ""))
    if user is None or not user.is_active:
        raise _CREDENTIALS_ERROR

    tenant_id = user.tenant_id
    if user.is_superuser and x_tenant and settings.allow_header_tenant_override:
        target = db.scalars(select(Tenant).where(Tenant.slug == x_tenant)).first()
        if target is None:
            raise HTTPException(status_code=404, detail="Tenant not found")
        tenant_id = target.id

    tenant = _tenant_by_id(db, tenant_id)
    return TenantContext(
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        user_id=user.id,
        role=user.role.value,
        is_superuser=user.is_superuser,
    )


CurrentPrincipal = Annotated[TenantContext, Depends(get_current_principal)]


def get_scope(
    db: Annotated[Session, Depends(get_db)], ctx: CurrentPrincipal
) -> TenantScope:
    return TenantScope(db, ctx)


Scope = Annotated[TenantScope, Depends(get_scope)]


def get_tenant(
    db: Annotated[Session, Depends(get_db)], ctx: CurrentPrincipal
) -> Tenant:
    return _tenant_by_id(db, ctx.tenant_id)


CurrentTenant = Annotated[Tenant, Depends(get_tenant)]


def require(capability: str):
    """Route guard: ``Depends(require("analysis:run"))``."""

    def _guard(ctx: CurrentPrincipal) -> TenantContext:
        from app.models.tenant import ROLE_CAPABILITIES

        try:
            role = Role(ctx.role)
        except ValueError:
            raise HTTPException(status_code=403, detail="Unknown role")
        caps = ROLE_CAPABILITIES.get(role, set())
        if "*" in caps or capability in caps:
            return ctx
        raise HTTPException(
            status_code=403, detail=f"Role '{ctx.role}' lacks capability '{capability}'"
        )

    return _guard


def require_plan(*plans: Plan):
    def _guard(tenant: CurrentTenant) -> Tenant:
        if tenant.plan not in plans:
            raise HTTPException(
                status_code=402,
                detail=f"Requires plan {' or '.join(p.value for p in plans)}; tenant is on '{tenant.plan.value}'",
            )
        return tenant

    return _guard
