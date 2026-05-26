from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

if TYPE_CHECKING:
    from app.modules.organizations.models import OrgContact, OrgDocumentShare as OrgDocumentShareModel

from app.common.enums.permission import PermissionLevel, Resource
from app.common.schemas import BaseResponseSchema, PaginatedResponse
from app.modules.org_credit_suspension.v1.schemas import (
    OrgCreditConfigInput,
    OrgCreditConfigResponse,
    OrgSuspensionConfigInput,
    OrgSuspensionConfigResponse,
)
from app.modules.org_discounts.v1.schemas import OrgDiscountConfigInput, OrgDiscountConfigResponse
from app.modules.pickup_addresses.v1.schemas import PickupAddressCreate, PickupAddressResponse
from app.modules.organizations.enums import (
    BillingSchedule,
    CompanySize,
    ContactRole,
    ContactStatus,
    IndustryType,
    OrgDocumentActivityType,
    OrgDocumentCategory,
    OrgDocumentConfidentialityLevel,
    OrgDocumentShareStatus,
    OrgDocumentStatus,
    OrgDocumentType,
    OrganizationStatus,
    PaymentModel,
    VatRate,
    VatTreatment,
)

# ── Organization Statistics ───────────────────────────────────────────────────


class OrgStatsResponse(BaseModel):
    """Organization statistics for B2B clients."""

    total: int = Field(..., description="Total organizations")
    active: int = Field(..., description="Active organizations without pending contacts")
    pending_activation: int = Field(..., description="Active organizations with pending invites")
    inactive: int = Field(..., description="Inactive organizations")
    suspended: int = Field(..., description="Suspended organizations")


# ── Pricing plan entry ────────────────────────────────────────────────────────


class PricingPlanEntry(BaseModel):
    """A single pricing plan assigned to an organisation.

    id_price_tier must always reference an existing active ServiceTier.
    Existence is validated at the service layer (DB lookup).

    base_price — snapshot of the tier's reference amount at save time (tier.base_price
    plus tier.price_per_package; per-kg is excluded). For standard plans the server
    aligns price_per_package to this reference. Populated automatically if not supplied.
    """

    id_price_tier: str = Field(..., description="UUID of the ServiceTier row")
    plain_type: str = Field(..., pattern="^(standard|custom)$")
    plain_name: str = Field(..., max_length=100)
    base_price: Decimal | None = Field(
        None,
        gt=0,
        decimal_places=2,
        description="Tier reference snapshot (base + per-package) — populated by the server on write.",
    )
    price_per_package: Decimal = Field(..., gt=0, decimal_places=2)
    price_per_kg: Decimal | None = Field(None, ge=0, decimal_places=2, description="Per-kg charge applied to the declared weight of every package in the stop.")
    days: int = Field(..., gt=0)
    selected: bool = False
    permitted: bool = Field(True, description="If false, tier is not offered on the booking flow.")
    is_default: bool = Field(False, description="Default pre-selected tier (exactly one permitted plan should be default).")
    icon: str | None = Field(None, max_length=100)
    color: str | None = Field(None, max_length=30)
    weight_margin_kg: float | None = Field(None, ge=0, description="Org-specific allowed weight margin per package (kg) before surcharge applies.")
    price_per_kg_override: float | None = Field(None, ge=0, description="Org-specific price per kg override — replaces ServiceTier.price_per_kg for this org.")


class BookingServiceTierItemResponse(BaseModel):
    """Resolved tier for booking (permitted lines or global fallback)."""

    id: str
    global_template_id: str
    org_tier_id: str | None = None
    mode: str
    is_default: bool
    tier_name: str
    description: str | None = None
    duration_days: int
    error_margin_kg: int
    price_per_kg: str
    price_per_package: str
    base_price: str
    available_for: str
    color: str | None = None
    icon: str | None = None
    source: str = Field(..., description="global | org_row — where values were read from.")


class BookingServiceTiersResponse(BaseModel):
    items: list[BookingServiceTierItemResponse]
    resolution_source: Literal["contract", "global_fallback"]


# ── Contact permission override (inline at creation) ──────────────────────────


class ContactPermission(BaseModel):
    """Override a single B2B portal resource permission for a contact."""

    resource: Resource
    level: PermissionLevel


# ── OrgContact schemas ────────────────────────────────────────────────────────


class OrgContactCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "jane.smith@acme.com",
                "first_name": "Jane",
                "last_name": "Smith",
                "contact_number": "+44 7700 900123",
                "contact_role": "OPERATIONS",
                "permissions": [
                    {"resource": r.value, "level": PermissionLevel.NONE.value}
                    for r in Resource
                ],
            }
        }
    )

    # Identity sourced from the linked User — provide email to look up / create the user
    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    contact_number: str = Field(..., max_length=50)
    contact_role: ContactRole = ContactRole.ACCOUNT_OWNER
    # Optional permission overrides — omit to use CUSTOMER_B2B role defaults
    permissions: list[ContactPermission] | None = None

    @field_validator("contact_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        cleaned = v.strip()
        if not re.match(r"^\+?[0-9][\d\s\-().]{5,49}$", cleaned):
            raise ValueError("contact_number must be a valid phone number (e.g. +44 7700 900123).")
        return cleaned


class OrgContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str
    organization_id: str
    contact_number: str
    contact_role: ContactRole
    status: ContactStatus
    is_primary: bool
    user_id: str | None
    created_at: datetime
    updated_at: datetime


class OrgContactUpdate(BaseModel):
    """All fields optional. Used for PATCH /contacts/{contact_id}."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "first_name": "Jane",
                "last_name": "Smith",
                "contact_number": "+44 7700 900123",
                "contact_role": "OPERATIONS",
                "permissions": [
                    {"resource": r.value, "level": PermissionLevel.NONE.value}
                    for r in Resource
                ],
            }
        }
    )

    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    contact_number: str | None = Field(None, max_length=50)
    contact_role: ContactRole | None = None
    # Provide to replace ALL permission overrides for this contact's linked user.
    # Omit to leave permissions unchanged.
    permissions: list[ContactPermission] | None = None


class OrgContactDetailResponse(BaseModel):
    """Full contact response, joined with User for identity fields.

    When redact_pii=True (GDPR: non-owner callers) email and phone are omitted.
    Use the class method from_orm_contact() to build an instance.
    """

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str
    organization_id: str
    contact_number: str
    contact_role: str
    status: str
    is_primary: bool
    user_id: str | None
    # Identity from linked User
    first_name: str | None
    last_name: str | None
    full_name: str | None
    # PII — omitted for non-owner callers (GDPR scoping)
    email: str | None
    phone: str | None
    # Fully resolved permissions (role defaults merged with overrides) for all resources
    permissions: list[ContactPermission] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_contact(
        cls,
        contact: OrgContact,  # noqa: F821  (forward ref resolved at runtime)
        *,
        redact_pii: bool = False,
        resolved_permissions: dict | None = None,
    ) -> OrgContactDetailResponse:
        """Build response from an OrgContact with its .user relationship loaded.

        resolved_permissions: full merged dict[Resource, PermissionLevel] from
        PermissionService.resolve_permissions(). Pass None when the contact has
        no linked user yet — permissions will be an empty list.
        """
        user = getattr(contact, "user", None)
        first_name = getattr(user, "first_name", None) if user else None
        last_name = getattr(user, "last_name", None) if user else None
        full_name = f"{first_name} {last_name}".strip() if first_name or last_name else None

        permissions = (
            [ContactPermission(resource=r, level=lev) for r, lev in resolved_permissions.items()]
            if resolved_permissions is not None
            else []
        )

        return cls(
            id=contact.id,
            organization_id=contact.organization_id,
            contact_number=contact.contact_number,
            contact_role=contact.contact_role,
            status=contact.status,
            is_primary=contact.is_primary,
            user_id=contact.user_id,
            first_name=first_name,
            last_name=last_name,
            full_name=full_name,
            email=None if redact_pii else (getattr(user, "email", None) if user else None),
            phone=None if redact_pii else (getattr(user, "phone", None) if user else None),
            permissions=permissions,
            created_at=contact.created_at,
            updated_at=contact.updated_at,
        )


class OrgContactListResponse(BaseModel):
    """Structured contacts list: owner separated from team members.

    owner        — the ACCOUNT_OWNER contact; no permissions returned (not editable by admin).
    team_members — all other active contacts, each with full resolved permissions.
    """

    owner: OrgContactDetailResponse | None = None
    team_members: list[OrgContactDetailResponse] = Field(default_factory=list)


# ── Payment configuration schemas ─────────────────────────────────────────────
# Defined before OrganizationUpdate so they can be referenced inline.

# Allowed billing schedules per payment model
_CARD_SCHEDULES = {BillingSchedule.IMMEDIATE}
_NON_CARD_SCHEDULES = {BillingSchedule.FIXED_MONTHLY_DATE, BillingSchedule.DAYS_AFTER_ORDER}


class AttemptFee(BaseModel):
    """Fee charged for a single delivery or return attempt."""

    attempt: int = Field(..., ge=1)
    fee: Decimal = Field(..., ge=0, decimal_places=2)


# Keep old name as an alias so existing imports don't break
DeliveryAttemptFee = AttemptFee


def _validate_attempt_fees(fees: list[AttemptFee], max_attempts: int, field_name: str) -> None:
    """Validate that attempt fees exactly match max_attempts, sequential from 1."""
    if len(fees) != max_attempts:
        raise ValueError(
            f"{field_name} must have exactly {max_attempts} entries "
            f"(one per attempt), got {len(fees)}."
        )
    expected = list(range(1, max_attempts + 1))
    actual = sorted(f.attempt for f in fees)
    if actual != expected:
        raise ValueError(
            f"{field_name} attempt numbers must be sequential from 1 to {max_attempts}."
        )


# ── Per-method schemas ────────────────────────────────────────────────────────


class OrgPaymentMethodCreate(BaseModel):
    """A single payment method to allow for an organisation.

    Business rules:
    - CARD              → billing_schedule must be IMMEDIATE
    - BANK_TRANSFER     → FIXED_MONTHLY_DATE or DAYS_AFTER_ORDER;
                          bank details required
    - CREDIT_ACCOUNT    → FIXED_MONTHLY_DATE or DAYS_AFTER_ORDER
    - CASH              → FIXED_MONTHLY_DATE or DAYS_AFTER_ORDER
    - FIXED_MONTHLY_DATE → billing_day_of_month required (1–28)
    - DAYS_AFTER_ORDER  → billing_days_after_order required (≥ 1)
    """

    payment_model: PaymentModel
    billing_schedule: BillingSchedule
    is_default: bool = False

    # FIXED_MONTHLY_DATE
    billing_day_of_month: int | None = Field(None, ge=1, le=28)
    # DAYS_AFTER_ORDER
    billing_days_after_order: int | None = Field(None, ge=1)

    # Bank details — required when payment_model = BANK_TRANSFER
    bank_account_name: str | None = Field(None, max_length=255)
    bank_account_number: str | None = Field(None, max_length=50)
    bank_sort_code: str | None = Field(None, max_length=20)

    # Credit settings — optional even when payment_model = CREDIT_ACCOUNT;
    # the limit can be configured later in the org's credit account settings.
    credit_limit: Decimal | None = Field(None, gt=0, decimal_places=2)
    credit_utilization_warning_pct: int | None = Field(None, ge=1, le=100)

    @model_validator(mode="after")
    def validate_payment_method(self) -> "OrgPaymentMethodCreate":
        model = self.payment_model
        schedule = self.billing_schedule

        if model == PaymentModel.CARD and schedule not in _CARD_SCHEDULES:
            raise ValueError("CARD payment model only supports IMMEDIATE billing schedule.")

        if model in (PaymentModel.BANK_TRANSFER, PaymentModel.CREDIT_ACCOUNT, PaymentModel.CASH) and schedule not in _NON_CARD_SCHEDULES:
            raise ValueError(f"{model} payment model requires FIXED_MONTHLY_DATE or DAYS_AFTER_ORDER billing schedule.")

        if schedule == BillingSchedule.FIXED_MONTHLY_DATE and self.billing_day_of_month is None:
            raise ValueError("billing_day_of_month is required for FIXED_MONTHLY_DATE schedule.")

        if schedule == BillingSchedule.DAYS_AFTER_ORDER and self.billing_days_after_order is None:
            raise ValueError("billing_days_after_order is required for DAYS_AFTER_ORDER schedule.")

        if model == PaymentModel.BANK_TRANSFER:
            missing = [f for f in ("bank_account_name", "bank_account_number", "bank_sort_code") if not getattr(self, f)]
            if missing:
                raise ValueError(f"BANK_TRANSFER requires: {', '.join(missing)}.")

        return self


class OrgPaymentMethodUpdate(BaseModel):
    """Update fields on an existing payment method. All fields optional."""

    billing_schedule: BillingSchedule | None = None
    billing_day_of_month: int | None = Field(None, ge=1, le=28)
    billing_days_after_order: int | None = Field(None, ge=1)

    bank_account_name: str | None = Field(None, max_length=255)
    bank_account_number: str | None = Field(None, max_length=50)
    bank_sort_code: str | None = Field(None, max_length=20)

    credit_limit: Decimal | None = Field(None, gt=0, decimal_places=2)
    credit_utilization_warning_pct: int | None = Field(None, ge=1, le=100)

    is_default: bool | None = None


# ── Shared config schemas ─────────────────────────────────────────────────────


class OrgPaymentConfigCreate(BaseModel):
    """Create shared payment & billing settings + one or more payment methods. Admin only.

    payment_methods must contain at least one entry. Exactly one must have
    is_default=True (or the first entry is treated as default when none is marked).
    """

    # VAT
    vat_number: str | None = Field(None, max_length=50)
    vat_rate: VatRate = VatRate.STANDARD_20
    vat_treatment: VatTreatment = VatTreatment.UK

    # Delivery reattempt charges
    max_delivery_attempts: int | None = Field(
        None,
        ge=1,
        description="Optional. Derived from delivery_attempt_fees length when omitted.",
    )
    delivery_attempt_fees: list[AttemptFee] = Field(..., min_length=1)

    # Return reattempt charges
    max_return_attempts: int | None = Field(
        None,
        ge=1,
        description="Optional. Derived from return_attempt_fees length when omitted.",
    )
    return_attempt_fees: list[AttemptFee] = Field(..., min_length=1)

    # Weight margin & surcharge
    weight_margin_kg: float | None = Field(None, ge=0)
    weight_surcharge_per_kg: Decimal | None = Field(None, ge=0, decimal_places=2)

    # One or more payment methods
    payment_methods: list[OrgPaymentMethodCreate] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_payment_config(self) -> "OrgPaymentConfigCreate":
        if self.max_delivery_attempts is None:
            self.max_delivery_attempts = len(self.delivery_attempt_fees)
        if self.max_return_attempts is None:
            self.max_return_attempts = len(self.return_attempt_fees)

        _validate_attempt_fees(self.delivery_attempt_fees, self.max_delivery_attempts, "delivery_attempt_fees")
        _validate_attempt_fees(self.return_attempt_fees, self.max_return_attempts, "return_attempt_fees")

        # Ensure at most one default method
        default_count = sum(1 for m in self.payment_methods if m.is_default)
        if default_count > 1:
            raise ValueError("At most one payment method may be marked as is_default=true.")

        # Ensure no duplicate payment models
        models = [m.payment_model for m in self.payment_methods]
        if len(models) != len(set(models)):
            raise ValueError("Duplicate payment models are not allowed.")

        return self


class OrgPaymentConfigUpdate(BaseModel):
    """Update shared payment configuration. Admin only. reason is mandatory."""

    vat_number: str | None = Field(None, max_length=50)
    vat_rate: VatRate | None = None
    vat_treatment: VatTreatment | None = None

    max_delivery_attempts: int | None = Field(
        None,
        ge=1,
        description="Optional. If delivery_attempt_fees is provided, max is derived from array length.",
    )
    delivery_attempt_fees: list[AttemptFee] | None = None

    max_return_attempts: int | None = Field(
        None,
        ge=1,
        description="Optional. If return_attempt_fees is provided, max is derived from array length.",
    )
    return_attempt_fees: list[AttemptFee] | None = None

    weight_margin_kg: float | None = Field(None, ge=0)
    weight_surcharge_per_kg: Decimal | None = Field(None, ge=0, decimal_places=2)

    reason: str = Field(..., min_length=3, max_length=500, description="Mandatory reason for the update (audit trail)")

    @model_validator(mode="after")
    def validate_attempt_fees_if_provided(self) -> "OrgPaymentConfigUpdate":
        if self.delivery_attempt_fees is not None and self.max_delivery_attempts is not None:
            _validate_attempt_fees(self.delivery_attempt_fees, self.max_delivery_attempts, "delivery_attempt_fees")
        if self.return_attempt_fees is not None and self.max_return_attempts is not None:
            _validate_attempt_fees(self.return_attempt_fees, self.max_return_attempts, "return_attempt_fees")
        return self


class OrgPaymentConfigUpdateEmbedded(BaseModel):
    """Payment config update embedded inside OrganizationUpdate.

    Identical to OrgPaymentConfigUpdate but without its own reason field —
    the parent OrganizationUpdate.reason covers both updates.
    Covers shared config fields only; use the dedicated payment methods
    endpoints to add/update/remove individual payment methods.
    """

    vat_number: str | None = Field(None, max_length=50)
    vat_rate: VatRate | None = None
    vat_treatment: VatTreatment | None = None

    max_delivery_attempts: int | None = Field(
        None,
        ge=1,
        description="Optional. If delivery_attempt_fees is provided, max is derived from array length.",
    )
    delivery_attempt_fees: list[AttemptFee] | None = None

    max_return_attempts: int | None = Field(
        None,
        ge=1,
        description="Optional. If return_attempt_fees is provided, max is derived from array length.",
    )
    return_attempt_fees: list[AttemptFee] | None = None

    weight_margin_kg: float | None = Field(None, ge=0)
    weight_surcharge_per_kg: Decimal | None = Field(None, ge=0, decimal_places=2)


# ── Organization base fields ──────────────────────────────────────────────────


def _validate_uk_postcode(v: str) -> str:
    """Validate and normalise a UK postcode. Allows both 'SW1A 2AA' and 'SW1A2AA'."""
    cleaned = v.strip().upper().replace(" ", "")
    # Standard UK postcode pattern (outward + inward codes)
    if not re.match(r"^[A-Z]{1,2}\d[A-Z\d]?\d[A-Z]{2}$", cleaned):
        raise ValueError("postcode must be a valid UK postcode (e.g. SW1A 2AA).")
    # Normalise: insert space before the inward code (last 3 chars)
    return f"{cleaned[:-3]} {cleaned[-3:]}"


class RegisteredAddressSchema(BaseModel):
    address_line_1: str = Field(..., max_length=255)
    address_line_2: str | None = Field(None, max_length=255)
    city: str = Field(..., max_length=100)
    state: str | None = Field(None, max_length=100)
    postcode: str = Field(..., max_length=20)
    country: str | None = Field(default="United Kingdom", max_length=100)

    @field_validator("postcode")
    @classmethod
    def validate_postcode(cls, v: str) -> str:
        return _validate_uk_postcode(v)


class TradingAddressSchema(BaseModel):
    """Optional trading address. When omitted the frontend uses the registered address."""

    address_line_1: str = Field(..., max_length=255)
    address_line_2: str | None = Field(None, max_length=255)
    city: str = Field(..., max_length=100)
    state: str | None = Field(None, max_length=100)
    postcode: str = Field(..., max_length=20)
    country: str = Field(default="United Kingdom", max_length=100)

    @field_validator("postcode")
    @classmethod
    def validate_postcode(cls, v: str) -> str:
        return _validate_uk_postcode(v)


# ── Profile completion (B2B onboarding widget) ──────────────────────────────


class ProfileCompletionItem(BaseModel):
    key: str
    label: str
    weight: int
    completed: bool
    missing_fields: list[str] = Field(default_factory=list)
    hint: str | None = None


class ProfileCompletionResponse(BaseModel):
    percent_complete: int
    completed_weight: int
    total_weight: int
    items: list[ProfileCompletionItem]


class OrganizationCreate(BaseModel):
    """All fields for creating an organization."""

    # General Information
    trading_name: str = Field(..., min_length=2, max_length=255)
    legal_entity_name: str = Field(..., max_length=255)
    industry: IndustryType
    company_size: CompanySize
    date_of_incorporation: date
    website: str | None = Field(None, max_length=500)
    description: str | None = Field(None, max_length=500)

    # Registration Details
    companies_house_number: str = Field(..., max_length=100)
    eori_number: str | None = Field(None, max_length=100)
    vat_number: str | None | None = Field(None, max_length=50)

    # Registered Address
    registered_address: RegisteredAddressSchema

    # Trading Address (optional — null means same as registered address)
    trading_address: TradingAddressSchema | None = None

    # Pricing Plans (list of plan objects — standard or custom)
    pricing_plans: list[PricingPlanEntry] | None = None

    # Contract & Agreement
    contract_reference: str | None = Field(None, max_length=500, description="R2/B2 bucket URL to signed contract PDF")
    pricing_agreement_start: date | None = None
    pricing_agreement_end: date | None = None

    # Contact phone
    phone: str | None = Field(None, max_length=50)

    # Account managers — all optional at create (assign later from client settings if needed)
    account_manager_user_id: str | None = Field(
        None, description="UUID of the primary account manager (admin user)"
    )
    secondary_account_manager_user_id: str | None = Field(None, description="UUID of the secondary account manager (admin user)")
    additional_account_manager_user_id: str | None = Field(None, description="UUID of the additional account manager (admin user)")

    # Package Restrictions
    max_package_weight: float | None = Field(None, gt=0, description="Max weight in kg")
    max_package_length: float | None = Field(None, gt=0, description="Max length in cm")
    max_package_width: float | None = Field(None, gt=0, description="Max width in cm")
    max_package_height: float | None = Field(None, gt=0, description="Max height in cm")
    min_charge_per_booking: Decimal | None = Field(None, gt=0, decimal_places=2, description="Minimum charge per booking in GBP")

    notes: str | None = None

    @field_validator("account_manager_user_id", "secondary_account_manager_user_id", "additional_account_manager_user_id", mode="before")
    @classmethod
    def empty_account_manager_to_none(cls, v: str | None) -> str | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @field_validator("vat_number")
    @classmethod
    def validate_vat_number(cls, v: str | None) -> str | None:
        if v is None:
            return v
        cleaned = v.strip().upper().replace(" ", "")
        # UK VAT: GB followed by 9 digits, or GB followed by 12 digits, or GBGD/GBHA + 3 digits
        if not re.match(r"^GB(\d{9}|\d{12}|(GD|HA)\d{3})$", cleaned):
            raise ValueError("vat_number must be a valid UK VAT number (e.g. GB123456789).")
        return cleaned

    @field_validator("companies_house_number")
    @classmethod
    def validate_companies_house_number(cls, v: str) -> str:
        cleaned = v.strip().upper()
        # Companies House numbers: 8 alphanumeric chars, or 2 letters + 6 digits (LLP, LP, etc.)
        if not re.match(r"^[A-Z0-9]{8}$", cleaned):
            raise ValueError("companies_house_number must be exactly 8 alphanumeric characters (e.g. 12345678 or OC123456).")
        return cleaned


class OrganizationUpdate(BaseModel):
    """All fields optional. reason is mandatory for audit trail.

    payment_config is optional — when provided it is updated atomically
    with the org fields in the same request. The parent reason covers both.
    """

    trading_name: str | None = Field(None, min_length=2, max_length=255)
    legal_entity_name: str | None = Field(None, max_length=255)
    industry: IndustryType | None = None
    company_size: CompanySize | None = None
    date_of_incorporation: date | None = None
    website: str | None = Field(None, max_length=500)
    description: str | None = Field(None, max_length=500)

    companies_house_number: str | None = Field(None, max_length=100)
    eori_number: str | None = Field(None, max_length=100)
    vat_number: str | None | None = Field(None, max_length=50)

    registered_address: RegisteredAddressSchema | None = None
    trading_address: TradingAddressSchema | None = None
    pricing_plans: list[PricingPlanEntry] | None = None

    contract_reference: str | None = Field(None, max_length=500)
    pricing_agreement_start: date | None = None
    pricing_agreement_end: date | None = None
    max_package_weight: float | None = Field(None, gt=0)
    max_package_length: float | None = Field(None, gt=0)
    max_package_width: float | None = Field(None, gt=0)
    max_package_height: float | None = Field(None, gt=0)
    min_charge_per_booking: Decimal | None = Field(None, gt=0, decimal_places=2)

    notes: str | None = None

    # Account managers
    account_manager_user_id: str | None = None
    secondary_account_manager_user_id: str | None = None
    additional_account_manager_user_id: str | None = None

    # Optional — when provided, the payment config is updated atomically with the org.
    payment_config: OrgPaymentConfigUpdateEmbedded | None = None

    reason: str = Field(..., min_length=3, max_length=500, description="Mandatory reason for the update (audit trail)")


class OrgSelfUpdate(BaseModel):
    """Fields a B2B user with ORG_PROFILE WRITE or ACCOUNT_OWNER can update."""

    trading_name: str | None = Field(None, min_length=2, max_length=255)
    legal_entity_name: str | None = Field(None, max_length=255)
    industry: IndustryType | None = None
    company_size: CompanySize | None = None
    date_of_incorporation: date | None = None
    description: str | None = Field(None, max_length=500)
    phone: str | None = Field(None, max_length=50)
    website: str | None = Field(None, max_length=500)
    companies_house_number: str | None = Field(None, max_length=100)
    eori_number: str | None = Field(None, max_length=100)
    vat_number: str | None | None = Field(None, max_length=50)
    registered_address: RegisteredAddressSchema | None = None
    trading_same_as_registered_address: bool = Field(
        False,
        description=(
            "When true, trading address columns are copied from `registered_address` in this request, "
            "or from existing organisation registered fields if `registered_address` is omitted. "
            "Mutually exclusive with `trading_address`."
        ),
    )
    trading_address: TradingAddressSchema | None = None
    pickup_addresses: list[PickupAddressCreate] | None = None

    @model_validator(mode="after")
    def _trading_same_vs_explicit_trading(self) -> Self:
        if self.trading_same_as_registered_address and self.trading_address is not None:
            raise ValueError("Cannot set trading_address when trading_same_as_registered_address is true.")
        return self

    @field_validator("vat_number")
    @classmethod
    def validate_vat_number(cls, v: str | None) -> str | None:
        if v is None:
            return v
        cleaned = v.strip().upper().replace(" ", "")
        if not re.match(r"^GB(\d{9}|\d{12}|(GD|HA)\d{3})$", cleaned):
            raise ValueError("vat_number must be a valid UK VAT number (e.g. GB123456789).")
        return cleaned

    @field_validator("companies_house_number")
    @classmethod
    def validate_companies_house_number(cls, v: str | None) -> str | None:
        if v is None:
            return v
        cleaned = v.strip().upper()
        if not re.match(r"^[A-Z0-9]{8}$", cleaned):
            raise ValueError("companies_house_number must be exactly 8 alphanumeric characters (e.g. 12345678 or OC123456).")
        return cleaned


class OrgProfileSavePayload(OrgSelfUpdate):
    """Multipart profile save payload encoded as JSON in `payload` form field."""


class OrganizationStatusChange(BaseModel):
    """Schema for status transitions (deactivate / suspend / reactivate)."""

    status: OrganizationStatus
    reason: str = Field(..., min_length=3, max_length=500, description="Mandatory reason for status change (audit trail)")


class PlaceOnHoldRequest(BaseModel):
    """Place an organisation on hold — new bookings blocked, existing shipments continue."""

    reason: str | None = Field(default=None, min_length=3, max_length=500, description="Optional reason for placing on hold")


class SuspendOrgRequest(BaseModel):
    """Suspend an organisation — pauses all active bookings and blocks new activity."""

    reason: str | None = Field(default=None, min_length=3, max_length=500, description="Optional reason for suspension")


class DeactivateOrgRequest(BaseModel):
    """Permanently deactivate an organisation.

    Requires a mandatory reason and the company's trading name typed verbatim
    as a confirmation safeguard.
    """

    reason: str = Field(..., min_length=3, max_length=500, description="Mandatory reason for permanent deactivation")
    confirm_name: str = Field(..., min_length=1, description="Must match the organisation's trading name exactly")


# ── Response ──────────────────────────────────────────────────────────────────


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str
    reference: str | None

    # General
    trading_name: str | None
    legal_entity_name: str | None
    industry: IndustryType | None
    company_size: CompanySize | None
    date_of_incorporation: date | None
    website: str | None
    description: str | None
    phone: str | None

    # Registration
    companies_house_number: str | None
    eori_number: str | None
    vat_number: str | None

    # Registered address
    reg_address_line_1: str | None
    reg_address_line_2: str | None
    reg_city: str | None
    reg_state: str | None
    reg_postcode: str | None
    reg_country: str | None

    # Trading address (null = same as registered)
    trading_address_line_1: str | None
    trading_address_line_2: str | None
    trading_address_city: str | None
    trading_address_state: str | None
    trading_address_postcode: str | None
    trading_address_country: str | None

    pricing_plans: list[PricingPlanEntry] | None

    contract_reference: str | None
    contract_title: str | None = None
    contract_expiry_date: date | None = None
    contract_url: str | None = None
    pricing_agreement_start: date | None
    pricing_agreement_end: date | None
    max_package_weight: float | None
    max_package_length: float | None
    max_package_width: float | None
    max_package_height: float | None
    min_charge_per_booking: Decimal | None

    status: OrganizationStatus
    notes: str | None

    # Onboarding — admin who created the org (computed, not a DB column)
    onboarded_by: str | None = None
    onboarded_by_role: str | None = None

    # Account management
    account_manager_user_id: str | None
    account_manager_name: str | None = None
    account_manager_email: str | None = None
    secondary_account_manager_user_id: str | None = None
    secondary_account_manager_name: str | None = None
    secondary_account_manager_email: str | None = None
    additional_account_manager_user_id: str | None = None
    additional_account_manager_name: str | None = None
    additional_account_manager_email: str | None = None

    # Profile image — signed CDN URL, generated on-demand (None when no logo uploaded)
    logo_url: str | None = None

    created_at: datetime
    updated_at: datetime
    version: int


# ── Account manager schemas ───────────────────────────────────────────────────


class AssignAccountManagerRequest(BaseModel):
    """Assign or replace the account manager for an organisation. Admin only.

    Pass account_manager_user_id=null to unassign the current account manager.
    """

    account_manager_user_id: str | None = Field(
        ...,
        description="UUID of the admin user to assign as account manager. Pass null to unassign.",
    )


class AccountManagerResponse(BaseModel):
    """Full profile of the account manager assigned to an organisation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    first_name: str
    last_name: str
    full_name: str
    email: str
    phone: str | None
    role: str


class OrgAccountManagerResponse(BaseModel):
    """Response for GET/PATCH /{org_id}/account-manager."""

    org_id: str
    account_manager: AccountManagerResponse | None


class AccountManagerListResponse(PaginatedResponse[AccountManagerResponse]):
    """Paginated list of admin users eligible as account managers."""


class OrganizationListItemResponse(BaseModel):
    """Slim response used in GET /organizations list endpoint.

    Includes all fields the B2B client list UI needs, joined from
    OrgPaymentConfig and the primary contact's User row.
    """

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str
    reference: str | None

    # Core identity
    trading_name: str | None
    legal_entity_name: str | None
    industry: IndustryType | None
    company_size: CompanySize | None
    status: OrganizationStatus

    # VAT
    vat_number: str | None
    is_vat_registered: bool

    # Registered address as a single line for display
    registered_address_full: str | None

    # Pricing
    pricing_type: str | None  # "standard" | "custom" | None

    # Payment config (null if not yet configured)
    # Lists all allowed payment models for this org (e.g. ["BANK_TRANSFER", "CREDIT_ACCOUNT"])
    payment_models: list[PaymentModel] | None
    # Credit limit from the CREDIT_ACCOUNT method, if one is configured
    credit_limit: Decimal | None

    # Primary contact (ACCOUNT_OWNER) — owner email shown in table
    owner_account_email: str | None

    # Onboarding — admin who created the org
    onboarded_by: str | None  # full name of the admin user
    onboarded_by_role: str | None  # role of the admin user

    # Account managers assigned to this org
    account_manager: str | None  # full name
    account_manager_role: str | None
    secondary_account_manager: str | None = None
    secondary_account_manager_role: str | None = None
    additional_account_manager: str | None = None
    additional_account_manager_role: str | None = None

    created_at: datetime
    updated_at: datetime


class OrganizationUpdateResponse(BaseModel):
    """Response for PATCH /organizations/{org_id}.

    Includes both the updated org and its payment config (if it exists),
    so the caller sees everything in one response.
    """

    organization: OrganizationResponse
    payment_config: OrgPaymentConfigResponse | None = None


class OrganizationProfileSaveResponse(BaseModel):
    """Response for PATCH /organizations/{org_id}/profile."""

    organization: OrganizationResponse
    pickup_addresses: list[PickupAddressResponse] = Field(default_factory=list)


class ProfileSaveSuccessResponse(BaseModel):
    """Envelope PATCH /profile — keeps explicit nulls in ``data`` (no SuccessResponse wrapper that drops Nones)."""

    model_config = ConfigDict(extra="forbid")

    success: Literal[True] = True
    data: OrganizationProfileSaveResponse


class OrganizationListResponse(PaginatedResponse[OrganizationResponse]):
    """Paginated list of organizations."""


# ── Create org + contacts request / response ──────────────────────────────────


class CreateOrgWithContactsRequest(BaseModel):
    organization: OrganizationCreate
    contacts: list[OrgContactCreate] = Field(..., min_length=1, description="At least one contact required")
    # Optional — when provided the payment config is created atomically with the org.
    payment_config: OrgPaymentConfigCreate | None = None
    # Optional — when provided the credit config is created atomically with the org.
    credit_config: OrgCreditConfigInput | None = None
    # Optional — when provided the suspension config is created atomically with the org.
    suspension_config: OrgSuspensionConfigInput | None = None
    # Optional — when provided the discount config is created atomically with the org.
    discount_config: OrgDiscountConfigInput | None = None
    # Optional — pickup addresses created atomically with the org.
    pickup_addresses: list[PickupAddressCreate] | None = None

    @model_validator(mode="after")
    def require_account_owner(self) -> CreateOrgWithContactsRequest:
        has_owner = any(c.contact_role == ContactRole.ACCOUNT_OWNER for c in self.contacts)
        if not has_owner:
            raise ValueError("At least one contact must have contact_role='ACCOUNT_OWNER'.")
        return self


class ContactCreatedEntry(BaseModel):
    contact_id: str
    user_id: str
    email: str
    contact_role: str
    invite_token: str


class CreateOrgWithContactsResponse(BaseModel):
    organization: OrganizationResponse
    contacts: list[ContactCreatedEntry]
    payment_config: OrgPaymentConfigResponse | None = None
    credit_config: OrgCreditConfigResponse | None = None
    suspension_config: OrgSuspensionConfigResponse | None = None
    discount_config: OrgDiscountConfigResponse | None = None
    pickup_addresses: list[PickupAddressResponse] | None = None

    # Contract — only populated when contract_file was supplied in the request
    contract_url: str | None = Field(
        None,
        description="Presigned download URL for the uploaded contract PDF. "
                    "Present only when contract_file was included in the request. "
                    "Valid for contract_url_expires_in_seconds seconds. "
                    "Call GET /organizations/{org_id}/contract to obtain a fresh URL later.",
    )
    contract_url_expires_in_seconds: int | None = Field(
        None,
        description="Seconds until contract_url becomes invalid (3600). "
                    "null when no contract was uploaded in this request.",
    )

    message: str


class OrgPaymentMethodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str
    organization_id: str
    payment_model: PaymentModel
    billing_schedule: BillingSchedule
    billing_day_of_month: int | None
    billing_days_after_order: int | None
    bank_account_name: str | None
    bank_account_number: str | None
    bank_sort_code: str | None
    credit_limit: Decimal | None
    credit_utilization_warning_pct: int | None
    is_default: bool
    created_at: datetime
    updated_at: datetime
    version: int


class OrgPaymentConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str
    organization_id: str

    vat_number: str | None
    vat_rate: VatRate
    vat_treatment: VatTreatment

    max_delivery_attempts: int
    delivery_attempt_fees: list[AttemptFee] | None
    max_return_attempts: int
    return_attempt_fees: list[AttemptFee] | None

    weight_margin_kg: float | None
    weight_surcharge_per_kg: Decimal | None

    payment_methods: list[OrgPaymentMethodResponse] = Field(default_factory=list)

    created_at: datetime
    updated_at: datetime
    version: int


# ── Contract upload response ───────────────────────────────────────────────────


class ContractUploadResponse(BaseModel):
    """Returned after uploading a contract PDF or fetching its download URL.

    Frontend usage guide
    --------------------
    contract_url         — Use directly as an <a href> or pass to fetch()/axios.
                           This is a time-limited presigned URL; do NOT store it.
    contract_reference   — The R2 storage key. Store this if you need to call
                           GET /organizations/{org_id}/contract again later to
                           obtain a fresh URL.
    contract_url_expires_in_seconds
                         — Seconds from now until the URL becomes invalid (3600).
                           Refresh via GET /organizations/{org_id}/contract before expiry.
    """

    # ── Identifiers ───────────────────────────────────────────────────────────
    org_id: str = Field(..., description="Organisation UUID")
    contract_reference: str = Field(
        ...,
        description="R2 storage key — stable internal identifier for the contract file. "
                    "Use to request a fresh download URL via GET /organizations/{org_id}/contract.",
    )

    # ── Download URL ──────────────────────────────────────────────────────────
    contract_url: str = Field(
        ...,
        description="Presigned download URL. Open directly in a browser tab or use in fetch(). "
                    "Valid for contract_url_expires_in_seconds seconds from the time of this response.",
    )
    contract_url_expires_in_seconds: int = Field(
        3600,
        description="Seconds until contract_url becomes invalid. Typically 3600 (1 hour).",
    )


# ── Org Document schemas ───────────────────────────────────────────────────────


class OrgDocumentResponse(BaseResponseSchema):
    """Returned by all document endpoints (simple upload, full operations, get, list, update)."""

    organization_id: str
    reference: str | None = None
    title: str
    document_type: OrgDocumentType
    # ── Extended fields (populated by Document Operations form; null for simple uploads) ──
    category: OrgDocumentCategory | None = None
    status: OrgDocumentStatus = OrgDocumentStatus.ACTIVE
    issuing_authority: str | None = None
    issue_date: date | None = None
    expiry_date: date | None = None
    description: str | None = None
    confidentiality_level: OrgDocumentConfidentialityLevel | None = None
    tags: list[str] | None = None
    uploaded_by: str | None
    uploaded_by_email: str | None = None
    # Excluded from API response and OpenAPI schema — used internally so routes
    # can pass the R2 key to OrganizationService.update_contract_metadata.
    r2_key: str | None = Field(None, exclude=True)
    document_url: str = Field(
        ...,
        description="Presigned download URL. Valid for document_url_expires_in_seconds seconds.",
    )
    document_url_expires_in_seconds: int = Field(
        3600,
        description="Seconds until document_url becomes invalid. Typically 3600 (1 hour).",
    )


class OrgDocumentOperationsRequest(BaseModel):
    """Full 'Document Operations' form — all classification and org settings fields.

    Sent as a JSON body alongside the file upload via multipart/form-data.
    """

    title: str = Field(..., min_length=1, max_length=200)
    document_type: OrgDocumentType
    category: OrgDocumentCategory
    issuing_authority: str | None = Field(None, max_length=255)
    issue_date: date | None = None
    expiry_date: date | None = None
    description: str | None = Field(None, max_length=1000)
    confidentiality_level: OrgDocumentConfidentialityLevel | None = None
    tags: list[str] | None = Field(None, max_length=10)
    notify_client: bool = False


class OrgDocumentUpdate(BaseModel):
    """Partial metadata update for PATCH. All document fields are optional; reason is mandatory."""

    title: str | None = Field(None, min_length=1, max_length=255)
    document_type: OrgDocumentType | None = None
    category: OrgDocumentCategory | None = None
    issuing_authority: str | None = Field(None, max_length=255)
    issue_date: date | None = None
    expiry_date: date | None = None
    description: str | None = Field(None, max_length=1000)
    confidentiality_level: OrgDocumentConfidentialityLevel | None = None
    tags: list[str] | None = Field(None, max_length=10)
    reason: str = Field(..., min_length=1, max_length=500, description="Mandatory reason for the audit trail")


# ── Org Document list response schemas ────────────────────────────────────────


class OrgDocumentExpiringSoonCard(BaseModel):
    """Stats card: expiring-soon summary."""

    count: int
    next_title: str | None = None
    next_expiry_date: date | None = None


class OrgDocumentTotalBreakdown(BaseModel):
    """Category breakdown for the Total Documents card."""

    contracts: int = 0
    client: int = 0
    internal: int = 0
    system: int = 0


class OrgDocumentTotalCard(BaseModel):
    """Stats card: total documents with category breakdown."""

    count: int
    breakdown: OrgDocumentTotalBreakdown


class OrgDocumentStats(BaseModel):
    """Always-unfiltered summary cards returned alongside the paginated list."""

    expiring_soon: OrgDocumentExpiringSoonCard
    total: OrgDocumentTotalCard


class OrgDocumentListResponse(BaseModel):
    """Wrapper returned by GET /{org_id}/documents — stats cards + paginated items."""

    model_config = ConfigDict(from_attributes=True)

    stats: OrgDocumentStats
    items: list[OrgDocumentResponse]
    total: int
    page: int
    size: int
    pages: int
    current_url: str | None = None
    next_url: str | None = None


# ── Org Document Activity schemas ─────────────────────────────────────────────


class OrgDocumentActivityResponse(BaseModel):
    """One row from the document recent-activity log."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str
    organization_id: str
    document_id: str | None
    document_reference: str | None = None  # DOC-YYYY-NNNNN, joined from org_documents
    activity_type: OrgDocumentActivityType
    actor_email: str | None
    actor_role: str | None
    document_name: str | None
    details: str | None
    # ── Client context captured at action time ────────────────────────────────
    ip_address: str | None = None
    browser: str | None = None
    device: str | None = None
    os: str | None = None
    created_at: datetime

    @field_validator("device", mode="before")
    @classmethod
    def normalise_device(cls, v: str | None) -> str | None:
        if not v or v.strip().lower() in ("other", "unknown", ""):
            return "Desktop"
        return v.strip()


# ── Org Document Share schemas ─────────────────────────────────────────────────


class OrgDocumentShareCreate(BaseModel):
    """Request body for POST /{org_id}/documents/{doc_id}/shares."""

    recipients: list[EmailStr] = Field(..., min_length=1, max_length=20, description="Email addresses to share with (max 20)")
    expiry_date: date | None = Field(None, description="Optional share link expiry date (ISO 8601). Null = no expiry.")
    password_protected: bool = Field(False, description="When true, recipients must verify their email via OTP each time they access the document.")
    message: str | None = Field(None, max_length=500, description="Optional message included in the share email (max 500 chars)")

    @field_validator("expiry_date")
    @classmethod
    def expiry_must_be_future(cls, v: date | None) -> date | None:
        if v is not None and v < date.today():
            raise ValueError("expiry_date must be today or a future date.")
        return v


class OrgDocumentShareResponse(BaseModel):
    """Returned by all document share endpoints."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str
    organization_id: str
    document_id: str
    document_title: str | None
    document_reference: str | None
    recipients: list[str]
    shared_by: str | None
    shared_by_name: str | None
    message: str | None
    expiry_date: date | None
    otp_required: bool
    status: OrgDocumentShareStatus
    access_count: int
    revoked_at: date | None
    status_reason: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_share(cls, share: "OrgDocumentShareModel") -> "OrgDocumentShareResponse":
        return cls(
            id=share.id,
            organization_id=share.organization_id,
            document_id=share.document_id,
            document_title=share.document_title,
            document_reference=share.document_reference,
            recipients=share.recipients or [],
            shared_by=share.shared_by,
            shared_by_name=share.shared_by_name,
            message=share.message,
            expiry_date=share.expiry_date,
            otp_required=share.otp_required,
            status=share.status,
            access_count=share.access_count,
            revoked_at=share.revoked_at,
            status_reason=share.status_reason,
            created_at=share.created_at,
            updated_at=share.updated_at,
        )


class OrgDocumentShareExtendExpiry(BaseModel):
    """Request body for PATCH /shares/{share_id}/expiry."""

    expiry_date: date = Field(..., description="New expiry date (ISO 8601)")
    reason: str = Field(..., min_length=1, max_length=500, description="Mandatory reason for the audit trail")

    @field_validator("expiry_date")
    @classmethod
    def expiry_must_be_future(cls, v: date) -> date:
        if v < date.today():
            raise ValueError("expiry_date must be today or a future date.")
        return v


class OrgDocumentShareRevoke(BaseModel):
    """Request body for PATCH /shares/{share_id}/revoke."""

    reason: str = Field(..., min_length=1, max_length=500, description="Mandatory reason for revoking access")


class SharedDocumentInfoResponse(BaseModel):
    """Returned by GET /v1/shared/documents/{share_token}.

    Tells the frontend whether an OTP challenge is required and the current share status.
    """

    share_token: str
    document_title: str | None
    document_reference: str | None
    shared_by_name: str | None
    message: str | None
    otp_required: bool
    status: OrgDocumentShareStatus  # ACTIVE / EXPIRED / REVOKED
    expiry_date: date | None


class ShareOtpSendRequest(BaseModel):
    """Body for POST /v1/shared/documents/{share_token}/otp/send."""

    email: EmailStr = Field(..., description="Recipient email address to send the OTP to.")


class ShareOtpSendResponse(BaseModel):
    """Returned after a successful share OTP send request."""

    message: str = "OTP sent to your email address. It expires in 10 minutes."


class ShareOtpVerifyRequest(BaseModel):
    """Body for POST /v1/shared/documents/{share_token}/otp/verify."""

    email: EmailStr = Field(..., description="The same email address used to request the OTP.")
    otp: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit numeric OTP received by email.",
    )


class ShareAccessTokenResponse(BaseModel):
    """Returned after successful OTP verification for a shared document link."""

    share_access_token: str = Field(
        ...,
        description=(
            "Pass this token as the `X-Share-Access-Token` header on access/download requests. "
            "Valid for 1 hour from issue time."
        ),
    )
    expires_in: int = Field(3600, description="Seconds until the token expires.")
    expires_at: datetime = Field(..., description="UTC datetime when the token expires.")
    message: str = "OTP verified. Use the share_access_token to access the document."


class SharedDocumentAccessRequest(BaseModel):
    """Body for POST /v1/shared/documents/{share_token}/access.

    share_access_token is required when the share is otp_required.
    """

    share_access_token: str | None = Field(None, description="Token obtained from OTP verification. Required for OTP-protected shares.")


class SharedDocumentAccessResponse(BaseModel):
    """Response returned when accessing a shared document."""

    document_title: str | None
    document_reference: str | None
    document_url: str = Field(..., description="Presigned download URL, valid for 1 hour.")
    document_url_expires_in_seconds: int = 3600


# ── Document Access OTP schemas ────────────────────────────────────────────────


class DocOTPSendResponse(BaseModel):
    """Returned after a successful OTP send request."""

    message: str = "OTP sent to your registered email address. It expires in 10 minutes."


class DocOTPVerifyRequest(BaseModel):
    """Body for POST /v1/organizations/documents/otp/verify."""

    otp: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit numeric OTP received by email.",
    )


class DocAccessTokenResponse(BaseModel):
    """Returned after successful OTP verification."""

    doc_access_token: str = Field(
        ...,
        description=(
            "Pass this token as the `X-Doc-Access-Token` header on every document API request. "
            "Valid for 1 hour from issue time."
        ),
    )
    expires_in: int = Field(3600, description="Seconds until the token expires.")
    expires_at: datetime = Field(..., description="UTC datetime when the token expires.")
    message: str


# ── Org Draft schemas ─────────────────────────────────────────────────────────


class OrgDraftContactInput(BaseModel):
    """Pending contact stored in org_drafts.draft_contacts JSONB before publish."""

    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., max_length=100)
    contact_number: str = Field(..., max_length=50)
    contact_role: ContactRole = ContactRole.ACCOUNT_OWNER
    permissions: list[ContactPermission] | None = None

    @field_validator("contact_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        cleaned = v.strip()
        if not re.match(r"^\+?[0-9][\d\s\-().]{5,49}$", cleaned):
            raise ValueError("contact_number must be a valid phone number.")
        return cleaned


class OrgDraftCreateRequest(BaseModel):
    """All fields optional — allows partial saves at any stage of onboarding."""

    # General Information
    trading_name: str | None = Field(None, min_length=2, max_length=255)
    legal_entity_name: str | None = Field(None, max_length=255)
    industry: IndustryType | None = None
    company_size: CompanySize | None = None
    date_of_incorporation: date | None = None
    website: str | None = Field(None, max_length=500)
    description: str | None = Field(None, max_length=500)
    phone: str | None = Field(None, max_length=50)

    # Registration Details
    companies_house_number: str | None = Field(None, max_length=100)
    eori_number: str | None = Field(None, max_length=100)
    vat_number: str | None = Field(None, max_length=50)

    # Registered Address
    registered_address: RegisteredAddressSchema | None = None

    # Trading Address
    trading_address: TradingAddressSchema | None = None

    # Pricing Plans
    pricing_plans: list[PricingPlanEntry] | None = None

    # Contract & Agreement
    pricing_agreement_start: date | None = None
    pricing_agreement_end: date | None = None

    # Contact phone
    contract_title: str | None = Field(None, max_length=255)
    contract_expiry_date: date | None = None

    # Account managers (all optional for draft)
    account_manager_user_id: str | None = None
    secondary_account_manager_user_id: str | None = None
    additional_account_manager_user_id: str | None = None

    # Package Restrictions
    max_package_weight: float | None = Field(None, gt=0)
    max_package_length: float | None = Field(None, gt=0)
    max_package_width: float | None = Field(None, gt=0)
    max_package_height: float | None = Field(None, gt=0)
    min_charge_per_booking: Decimal | None = Field(None, gt=0, decimal_places=2)

    notes: str | None = None

    # Pending contacts — stored in org_drafts.draft_contacts JSONB
    contacts: list[OrgDraftContactInput] | None = None

    @field_validator("vat_number")
    @classmethod
    def validate_vat_number(cls, v: str | None) -> str | None:
        if v is None:
            return v
        cleaned = v.strip().upper().replace(" ", "")
        if not re.match(r"^GB(\d{9}|\d{12}|(GD|HA)\d{3})$", cleaned):
            raise ValueError("vat_number must be a valid UK VAT number (e.g. GB123456789).")
        return cleaned

    @field_validator("companies_house_number")
    @classmethod
    def validate_companies_house_number(cls, v: str | None) -> str | None:
        if v is None:
            return v
        cleaned = v.strip().upper()
        if not re.match(r"^[A-Z0-9]{8}$", cleaned):
            raise ValueError("companies_house_number must be exactly 8 alphanumeric characters.")
        return cleaned


class OrgDraftUpdateRequest(OrgDraftCreateRequest):
    """Same as create — all fields remain optional for incremental updates."""


class OrgDraftResponse(BaseModel):
    """Full draft response — organization fields + draft metadata."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    # Draft metadata
    draft_number: str | None
    draft_created_by_id: str | None = None
    draft_created_at: datetime | None = None
    draft_updated_at: datetime | None = None
    draft_contacts: list | None = None

    # Org fields
    id: str
    reference: str | None

    trading_name: str | None
    legal_entity_name: str | None
    industry: IndustryType | None
    company_size: CompanySize | None
    date_of_incorporation: date | None
    website: str | None
    description: str | None
    phone: str | None

    companies_house_number: str | None
    eori_number: str | None
    vat_number: str | None

    reg_address_line_1: str | None
    reg_address_line_2: str | None
    reg_city: str | None
    reg_state: str | None
    reg_postcode: str | None
    reg_country: str | None

    trading_address_line_1: str | None
    trading_address_line_2: str | None
    trading_address_city: str | None
    trading_address_state: str | None
    trading_address_postcode: str | None
    trading_address_country: str | None

    pricing_plans: list[PricingPlanEntry] | None

    contract_reference: str | None
    contract_title: str | None
    contract_expiry_date: date | None
    pricing_agreement_start: date | None
    pricing_agreement_end: date | None

    max_package_weight: float | None
    max_package_length: float | None
    max_package_width: float | None
    max_package_height: float | None
    min_charge_per_booking: Decimal | None

    status: OrganizationStatus
    notes: str | None
    logo_url: str | None = None

    account_manager_user_id: str | None
    secondary_account_manager_user_id: str | None
    additional_account_manager_user_id: str | None

    created_at: datetime
    updated_at: datetime
    version: int


class OrgDraftListItem(BaseModel):
    """Slim response used in GET /organizations/drafts list."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    draft_number: str | None
    draft_created_by_id: str | None = None
    draft_created_at: datetime | None = None
    draft_updated_at: datetime | None = None

    id: str
    reference: str | None
    trading_name: str | None
    legal_entity_name: str | None
    industry: IndustryType | None
    company_size: CompanySize | None
    status: OrganizationStatus

    account_manager_user_id: str | None
    created_at: datetime
    updated_at: datetime


class OrgDraftPublishRequest(BaseModel):
    """Request body for POST /organizations/drafts/{draft_number}/publish.

    contacts is required — at least one must have contact_role=ACCOUNT_OWNER.
    Overrides any contacts already stored in draft_contacts JSONB.
    If omitted the previously saved draft_contacts are used (must not be empty).
    """

    contacts: list[OrgDraftContactInput] | None = None
    # Optional sub-configs — created atomically on publish (same as full create)
    payment_config: OrgPaymentConfigCreate | None = None
    pickup_addresses: list[PickupAddressCreate] | None = None


# ── Payment Details Dashboard ────────────────────────────────────────────────


class PaymentMethodStats(BaseModel):
    model: PaymentModel
    usage_percentage: float
    total_charged: Decimal
    order_count: int


class OrgPaymentDetailsResponse(BaseModel):
    """Aggregated financial and activity stats for an organization's payment dashboard."""

    # Overall summary
    total_charged: Decimal = Decimal("0.00")
    total_orders: int = 0

    # Card Payment specific stats
    successful_payments: int = 0
    failed_payments: int = 0
    payment_success_rate: float = 0.0

    # Billing/Invoicing stats (combined for Bank/Credit/Cash models)
    total_invoiced: Decimal = Decimal("0.00")
    paid_invoices_amount: Decimal = Decimal("0.00")
    unpaid_invoices_amount: Decimal = Decimal("0.00")
    overdue_amount: Decimal = Decimal("0.00")
    invoice_count: int = 0

    # Credit specific (populated if CREDIT_ACCOUNT is used)
    credit_limit: Decimal | None = None
    used_credit: Decimal | None = None
    available_credit: Decimal | None = None
    credit_utilization_pct: float | None = None

    # Next billing due date — derived from the default payment method's schedule
    next_due_date: date | None = None

    # Shared billing config (VAT, attempt fees, weight) — drives the Payment Model Card rows
    payment_config: OrgPaymentConfigResponse | None = None

    # Configured payment methods for this org (drives the tile cards on the dashboard)
    payment_methods: list[OrgPaymentMethodResponse] = Field(default_factory=list)

    # Breakdown by payment method
    method_distribution: list[PaymentMethodStats] = Field(default_factory=list)
