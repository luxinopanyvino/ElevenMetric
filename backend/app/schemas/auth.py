from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.tenant import Plan, Role


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    tenant: "TenantOut"
    user: "UserOut"


class LoginRequest(BaseModel):
    email: str
    password: str
    tenant_slug: str | None = Field(
        default=None, description="Required only when the email exists in several tenants."
    )


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    country: str | None = None
    plan: Plan
    transfer_budget_eur: int
    wage_budget_eur_per_year: int
    video_minutes_used: int


class TenantCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str
    country: str | None = None
    plan: Plan = Plan.pro
    transfer_budget_eur: int = 0
    wage_budget_eur_per_year: int = 0
    owner_email: EmailStr
    owner_password: str = Field(min_length=8)
    owner_name: str = ""


class TenantUpdate(BaseModel):
    name: str | None = None
    country: str | None = None
    transfer_budget_eur: int | None = None
    wage_budget_eur_per_year: int | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    role: Role
    is_superuser: bool


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = ""
    role: Role = Role.viewer


class ApiKeyOut(BaseModel):
    id: str
    label: str
    #: Returned exactly once, at creation.
    key: str | None = None


Token.model_rebuild()
