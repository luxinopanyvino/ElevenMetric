from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, CurrentTenant, Scope, require
from app.core.config import settings
from app.core.security import (
    create_access_token,
    hash_password,
    new_api_key,
    verify_password,
)
from app.db.session import get_db
from app.models.tenant import ApiKey, Role, Tenant, User
from app.schemas.auth import (
    ApiKeyOut,
    LoginRequest,
    TenantCreate,
    TenantOut,
    TenantUpdate,
    Token,
    UserCreate,
    UserOut,
)

router = APIRouter(tags=["auth"])


def _issue(db: Session, user: User, tenant: Tenant) -> Token:
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    token = create_access_token(
        subject=user.id,
        tenant_id=tenant.id,
        role=user.role.value,
        is_superuser=user.is_superuser,
    )
    return Token(
        access_token=token,
        expires_in=settings.access_token_ttl_minutes * 60,
        tenant=TenantOut.model_validate(tenant),
        user=UserOut.model_validate(user),
    )


def _authenticate(db: Session, email: str, password: str, tenant_slug: str | None) -> tuple[User, Tenant]:
    stmt = select(User).where(User.email == email, User.is_active.is_(True))
    if tenant_slug:
        stmt = stmt.join(Tenant, Tenant.id == User.tenant_id).where(Tenant.slug == tenant_slug)
    users = list(db.scalars(stmt).all())

    if len(users) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email belongs to several clubs — supply tenant_slug.",
        )
    if not users or not verify_password(password, users[0].password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        )

    user = users[0]
    tenant = db.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(status_code=403, detail="Club account is inactive")
    return user, tenant


@router.post("/auth/login", response_model=Token)
def login(payload: LoginRequest, db: Annotated[Session, Depends(get_db)]) -> Token:
    user, tenant = _authenticate(db, payload.email, payload.password, payload.tenant_slug)
    return _issue(db, user, tenant)


@router.post("/auth/token", response_model=Token, include_in_schema=False)
def login_form(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> Token:
    """OAuth2 password flow, so the interactive docs can authenticate."""
    user, tenant = _authenticate(db, form.username, form.password, None)
    return _issue(db, user, tenant)


@router.get("/auth/me", response_model=UserOut)
def me(ctx: CurrentPrincipal, db: Annotated[Session, Depends(get_db)]) -> UserOut:
    if ctx.user_id is None:
        raise HTTPException(status_code=400, detail="API keys have no user profile")
    user = db.get(User, ctx.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut.model_validate(user)


# --- Tenants ---------------------------------------------------------------

@router.post("/tenants", response_model=TenantOut, status_code=201)
def create_tenant(payload: TenantCreate, db: Annotated[Session, Depends(get_db)]) -> TenantOut:
    """Self-service club signup. Creates the tenant and its owner in one call."""
    if db.scalars(select(Tenant).where(Tenant.slug == payload.slug)).first():
        raise HTTPException(status_code=409, detail="That club slug is taken")

    tenant = Tenant(
        slug=payload.slug, name=payload.name, country=payload.country, plan=payload.plan,
        transfer_budget_eur=payload.transfer_budget_eur,
        wage_budget_eur_per_year=payload.wage_budget_eur_per_year,
    )
    db.add(tenant)
    db.flush()

    owner = User(
        tenant_id=tenant.id, email=payload.owner_email, full_name=payload.owner_name,
        password_hash=hash_password(payload.owner_password), role=Role.owner,
    )
    db.add(owner)
    db.commit()
    db.refresh(tenant)
    return TenantOut.model_validate(tenant)


@router.get("/tenants/current", response_model=TenantOut)
def current_tenant(tenant: CurrentTenant) -> TenantOut:
    return TenantOut.model_validate(tenant)


@router.patch("/tenants/current", response_model=TenantOut,
              dependencies=[Depends(require("squad:write"))])
def update_tenant(
    payload: TenantUpdate, tenant: CurrentTenant, db: Annotated[Session, Depends(get_db)]
) -> TenantOut:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(tenant, field, value)
    db.commit()
    db.refresh(tenant)
    return TenantOut.model_validate(tenant)


# --- Users and keys --------------------------------------------------------

@router.post("/users", response_model=UserOut, status_code=201,
             dependencies=[Depends(require("squad:write"))])
def create_user(payload: UserCreate, scope: Scope, tenant: CurrentTenant) -> UserOut:
    existing = scope.first(User, User.email == payload.email)
    if existing:
        raise HTTPException(status_code=409, detail="That email already exists in this club")

    limit = tenant.limits["max_users"]
    if scope.count(User) >= limit:
        raise HTTPException(
            status_code=402, detail=f"Plan '{tenant.plan.value}' allows {limit} users"
        )

    user = User(
        email=payload.email, full_name=payload.full_name, role=payload.role,
        password_hash=hash_password(payload.password),
    )
    scope.add(user)
    scope.commit()
    scope.refresh(user)
    return UserOut.model_validate(user)


@router.get("/users", response_model=list[UserOut])
def list_users(scope: Scope) -> list[UserOut]:
    return [UserOut.model_validate(u) for u in scope.all(User, order_by=User.email)]


@router.post("/api-keys", response_model=ApiKeyOut, status_code=201,
             dependencies=[Depends(require("squad:write"))])
def create_api_key(label: str, scope: Scope, ctx: CurrentPrincipal) -> ApiKeyOut:
    """Issue a machine credential for provider ingest. The plaintext is shown once."""
    raw, digest = new_api_key()
    key = ApiKey(label=label, key_digest=digest, created_by=ctx.user_id)
    scope.add(key)
    scope.commit()
    scope.refresh(key)
    return ApiKeyOut(id=key.id, label=key.label, key=raw)


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(scope: Scope) -> list[ApiKeyOut]:
    return [
        ApiKeyOut(id=k.id, label=k.label, key=None)
        for k in scope.all(ApiKey, ApiKey.revoked_at.is_(None))
    ]


@router.delete("/api-keys/{key_id}", status_code=204,
               dependencies=[Depends(require("squad:write"))])
def revoke_api_key(key_id: str, scope: Scope) -> None:
    key = scope.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="Key not found")
    key.revoked_at = datetime.now(timezone.utc)
    scope.commit()
