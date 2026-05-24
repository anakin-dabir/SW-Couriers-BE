"""Admins v1 API schemas — request and response models."""

from __future__ import annotations

from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.common.enums import UserTitle
from app.common.enums.permission import PermissionLevel, Resource
from app.common.schemas import BaseResponseSchema, BaseSchema


# ── Permission entry (reused in request + response) ──────────────────────────


class AdminPermissionEntry(BaseSchema):
    """A single resource → level assignment."""

    resource: str = Field(description="Resource name (e.g. VEHICLE_MANAGEMENT)")
    level: str = Field(description="NONE, READ, or WRITE")

    @field_validator("resource")
    @classmethod
    def validate_resource(cls, v: str) -> str:
        try:
            Resource(v)
        except ValueError:
            valid = ", ".join(r.value for r in Resource)
            raise ValueError(f"Invalid resource '{v}'. Must be one of: {valid}") from None
        return v

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        try:
            PermissionLevel[v]
        except KeyError:
            raise ValueError(f"Invalid level '{v}'. Must be one of: NONE, READ, WRITE") from None
        return v


# ── Request schemas ───────────────────────────────────────────────────────────


class CreateAdminRequest(BaseSchema):
    """Step 1 (Basic Info) + Step 2 (Permissions) combined.

    ``send_invite=True``  → create + send invite immediately (Save & Create).
    ``send_invite=False`` → create in draft mode; invite sent later via POST /{id}/invite.
    """

    title: UserTitle | None = Field(default=None, description="Salutation: MR, MRS, MS, DR, PROF")
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=50)
    position_role: str | None = Field(
        default=None, max_length=150, description="Free-text job title, e.g. Operations Manager"
    )
    address_line_1: str = Field(min_length=1, max_length=255)
    address_line_2: str | None = Field(default=None, max_length=255)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    postcode: str = Field(min_length=1, max_length=20)
    country: str | None = Field(default=None, max_length=100)
    permissions: list[AdminPermissionEntry] = Field(
        default_factory=list,
        description="Permission overrides. Resources not listed fall back to the ADMIN role default.",
    )
    send_invite: bool = Field(
        default=True,
        description="True = Send invite email immediately (Save & Create). False = Save as Draft.",
    )

    @field_validator("country", mode="before")
    @classmethod
    def _blank_country_to_none(cls, v: object) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s or None
        return str(v)

    def resolved_permissions(self) -> dict[Resource, PermissionLevel]:
        """Convert list to dict keyed by Resource enum."""
        return {Resource(p.resource): PermissionLevel[p.level] for p in self.permissions}


_DEFAULT_ADMIN_COUNTRY = "United Kingdom"


class AdminPostalPatchRequest(BaseSchema):
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    postcode: str | None = None
    country: str | None = None

    @field_validator("address_line_1", mode="before")
    @classmethod
    def _address_line_1(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            raise ValueError("address_line_1 cannot be empty")
        return s

    @field_validator("city", mode="before")
    @classmethod
    def _city(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            raise ValueError("city cannot be empty")
        return s

    @field_validator("state", mode="before")
    @classmethod
    def _state(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            raise ValueError("state cannot be empty")
        return s

    @field_validator("postcode", mode="before")
    @classmethod
    def _postcode(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            raise ValueError("postcode cannot be empty")
        return s

    @field_validator("address_line_2", mode="before")
    @classmethod
    def _address_line_2(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("country", mode="before")
    @classmethod
    def _country(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or _DEFAULT_ADMIN_COUNTRY


class UpdateAdminPermissionsRequest(BaseSchema):
    permissions: list[AdminPermissionEntry] = Field(
        default_factory=list,
        description="Full permission override list. Replaces all existing overrides.",
    )

    def resolved_permissions(self) -> dict[Resource, PermissionLevel]:
        return {Resource(p.resource): PermissionLevel[p.level] for p in self.permissions}


class AdminStatusChangeRequest(BaseSchema):
    """Body for suspend / reactivate endpoints."""

    reason: str = Field(min_length=1, max_length=500, description="Mandatory reason for the status change")


# ── Response schemas ──────────────────────────────────────────────────────────


class AdminResponse(BaseResponseSchema):
    """Admin user full detail response."""

    admin_ref: str | None
    title: str | None
    first_name: str
    last_name: str
    full_name: str
    email: str
    phone: str | None
    position_role: str | None
    address_line_1: str | None
    address_line_2: str | None
    city: str | None
    state: str | None
    postcode: str | None
    country: str | None
    role: str
    status: str
    last_login: datetime | None
    profile_photo_url: str | None
    permissions: list[AdminPermissionEntry]


class AdminProfileResponse(BaseResponseSchema):
    admin_ref: str | None
    title: str | None
    first_name: str
    last_name: str
    full_name: str
    email: str
    phone: str | None
    position_role: str | None
    address_line_1: str | None
    address_line_2: str | None
    city: str | None
    state: str | None
    postcode: str | None
    country: str | None
    role: str
    status: str
    last_login: datetime | None
    profile_photo_url: str | None


class AssignedOrgItem(BaseSchema):
    """Slim org entry used in admin list — orgs where this admin is an account manager."""

    id: str
    reference: str | None
    name: str
    email: str | None


class AdminListItemResponse(BaseSchema):
    """Slim admin row used in the list endpoint."""

    id: str
    admin_ref: str | None
    title: str | None
    first_name: str
    last_name: str
    full_name: str
    email: str
    phone: str | None
    position_role: str | None
    address_line_1: str | None
    address_line_2: str | None
    city: str | None
    state: str | None
    postcode: str | None
    country: str | None
    role: str
    status: str
    last_login: datetime | None
    created_at: datetime
    assigned_accounts: list[AssignedOrgItem] = Field(
        default_factory=list,
        description="Organisations where this admin is assigned as account manager (any position).",
    )


class CreateAdminResponse(BaseSchema):
    """Returned immediately after POST /admins."""

    user_id: str
    email: str
    invite_id: str | None = Field(
        default=None,
        description="Set when send_invite=True. Use to track delivery status.",
    )
    status: str = Field(description="PENDING_ACTIVATION (draft or invited)")
    photo_upload_failed: bool = Field(
        default=False,
        description="True when a profile_photo was submitted but the upload failed. The admin was still created.",
    )


class SendAdminInviteResponse(BaseSchema):
    """Returned after POST /admins/{user_id}/invite."""

    invite_id: str
    email: str


class AdminStatsResponse(BaseSchema):
    """Admin statistics summary (5 counts)."""

    total: int = Field(description="Total number of admins")
    active: int = Field(description="Number of active admins")
    inactive: int = Field(description="Number of inactive admins")
    suspended: int = Field(description="Number of suspended admins")
    pending_activation: int = Field(description="Number of admins pending invite acceptance")
