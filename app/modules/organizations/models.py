"""Organization ORM models — B2B client companies and their sub-entities."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AppendOnlyModel, BaseModel
from app.modules.organizations.doc_access_scope import DocAccessScope
from app.modules.organizations.enums import (
    BillingSchedule,
    CompanySize,
    ContactRole,
    ContactStatus,
    IndustryType,
    OrganizationStatus,
    OrgDocumentActivityType,
    OrgDocumentCategory,
    OrgDocumentConfidentialityLevel,
    OrgDocumentShareStatus,
    OrgDocumentStatus,
    OrgDocumentType,
    PaymentModel,
    VatRate,
    VatTreatment,
)

if TYPE_CHECKING:
    from app.modules.pickup_addresses.models import PickupAddress
    from app.modules.user.models import User
class Organization(BaseModel):
    """B2B client company."""

    __tablename__ = "organizations"

    # Auto-generated reference: SWC-ORG-NNNNN (unique, set on create via DB sequence)
    reference: Mapped[str | None] = mapped_column(String(20), nullable=True, unique=True, index=True)

    # ── General Information ───────────────────────────────────────────────────
    # All formerly-required fields are nullable to allow draft saves before publish.
    trading_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    legal_entity_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[IndustryType | None] = mapped_column(Enum(IndustryType, native_enum=False), nullable=True)
    company_size: Mapped[CompanySize | None] = mapped_column(
        Enum(CompanySize, native_enum=False, values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    )
    date_of_incorporation: Mapped[date | None] = mapped_column(Date, nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Registration Details ──────────────────────────────────────────────────
    companies_house_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    eori_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vat_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Registered Address ────────────────────────────────────────────────────
    reg_address_line_1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reg_address_line_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reg_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reg_state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reg_postcode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reg_country: Mapped[str | None] = mapped_column(String(100), nullable=True, default="United Kingdom")

    # ── Trading Address ───────────────────────────────────────────────────────
    # Optional — when null, same as registered address
    trading_address_line_1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trading_address_line_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trading_address_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    trading_address_state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    trading_address_postcode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trading_address_country: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Pricing Plans ─────────────────────────────────────────────────────────
    pricing_plans: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # ── Contract & Agreement ──────────────────────────────────────────────────
    contract_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)  # R2/B2 bucket URL to PDF
    contract_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contract_expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pricing_agreement_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    pricing_agreement_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    # ── Package Restrictions ──────────────────────────────────────────────────
    max_package_weight: Mapped[float | None] = mapped_column(Float, nullable=True)  # kg
    max_package_length: Mapped[float | None] = mapped_column(Float, nullable=True)  # cm
    max_package_width: Mapped[float | None] = mapped_column(Float, nullable=True)  # cm
    max_package_height: Mapped[float | None] = mapped_column(Float, nullable=True)  # cm
    min_charge_per_booking: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)  # GBP

    status: Mapped[OrganizationStatus] = mapped_column(
        Enum(OrganizationStatus, native_enum=False),
        nullable=False,
        default=OrganizationStatus.ACTIVE,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Profile image (Cloudflare Images) ────────────────────────────────────
    # Cloudflare Images ID — signed CDN URL generated on-demand, never stored
    logo_cf_image_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Onboarding & account management ──────────────────────────────────────
    # Admin user who created this organisation (SET NULL when user is deleted)
    onboarded_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Admin user assigned as account manager (optional, SET NULL on delete)
    account_manager_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    secondary_account_manager_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    additional_account_manager_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    contacts: Mapped[list[OrgContact]] = relationship("OrgContact", back_populates="organization", lazy="raise", cascade="all, delete-orphan")
    payment_config: Mapped[OrgPaymentConfig | None] = relationship("OrgPaymentConfig", back_populates="organization", lazy="raise", uselist=False, cascade="all, delete-orphan")
    payment_methods: Mapped[list[OrgPaymentMethod]] = relationship("OrgPaymentMethod", back_populates="organization", lazy="raise", cascade="all, delete-orphan")
    pickup_addresses: Mapped[list[PickupAddress]] = relationship(
        "PickupAddress",
        back_populates="organization",
        lazy="raise",
        cascade="all, delete-orphan",
    )
    draft: Mapped[OrgDraft | None] = relationship("OrgDraft", back_populates="organization", lazy="raise", uselist=False, cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Organization {self.trading_name}>"


class OrgDraft(BaseModel):
    """Draft tracking pivot for organisations being created incrementally.

    One row per organisation (UNIQUE on organization_id). Stores the human-friendly
    draft code (ORG-D-NNN) and pending contact data (JSONB) before publish fires
    user-account creation.
    """

    __tablename__ = "org_drafts"

    # Auto-generated human-friendly code: ORG-D-001, ORG-D-002, … (via DB sequence)
    draft_number: Mapped[str | None] = mapped_column(String(20), nullable=True, unique=True, index=True)

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    created_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    published_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Raw contact form data (email, first_name, last_name, contact_number, contact_role, …)
    # Stored here until publish creates actual User + OrgContact rows.
    draft_contacts: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    organization: Mapped[Organization] = relationship("Organization", back_populates="draft", lazy="raise")

    def __repr__(self) -> str:
        return f"<OrgDraft {self.draft_number} org={self.organization_id}>"


class OrgContact(BaseModel):
    """Contact person linked to an organisation — at least one must be ACCOUNT_OWNER."""

    __tablename__ = "org_contacts"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Classification ────────────────────────────────────────────────────────
    contact_number: Mapped[str] = mapped_column(String(50), nullable=False)
    contact_role: Mapped[ContactRole] = mapped_column(Enum(ContactRole, native_enum=False), nullable=False, default=ContactRole.ACCOUNT_OWNER)
    status: Mapped[ContactStatus] = mapped_column(Enum(ContactStatus, native_enum=False), nullable=False, default=ContactStatus.PENDING)
    # Exactly one active contact per org should have is_primary=True.
    # This is the single "main point of contact" flag — separate from contact_role.
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Portal linkage ────────────────────────────────────────────────────────
    # Populated once the contact accepts their invite
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    organization: Mapped[Organization] = relationship("Organization", back_populates="contacts", lazy="raise")
    user: Mapped[User | None] = relationship("User", lazy="raise", foreign_keys=[user_id], viewonly=True)

    def __repr__(self) -> str:
        return f"<OrgContact user_id={self.user_id} role={self.contact_role}>"


class OrgPaymentConfig(BaseModel):
    """Shared billing settings for an org — one-to-one (VAT, attempt fees, weight charges)."""

    __tablename__ = "org_payment_configs"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # ── VAT ───────────────────────────────────────────────────────────────────
    vat_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    vat_rate: Mapped[VatRate] = mapped_column(Enum(VatRate, native_enum=False), nullable=False, default=VatRate.STANDARD_20)
    vat_treatment: Mapped[VatTreatment] = mapped_column(Enum(VatTreatment, native_enum=False), nullable=False, default=VatTreatment.UK)

    # ── Delivery reattempt charges ────────────────────────────────────────────
    # Number of delivery attempts allowed (1–10)
    max_delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # JSONB array: [{"attempt": 1, "fee": "1.00"}, {"attempt": 2, "fee": "3.50"}, ...]
    # Length must equal max_delivery_attempts; validated at service layer.
    delivery_attempt_fees: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # ── Return reattempt charges ──────────────────────────────────────────────
    # Number of return attempts allowed (1–10)
    max_return_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # JSONB array: [{"attempt": 1, "fee": "1.00"}, {"attempt": 2, "fee": "3.50"}, ...]
    # Length must equal max_return_attempts; validated at service layer.
    return_attempt_fees: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # ── Weight margin & surcharge ─────────────────────────────────────────────
    # Allowed weight margin per package (kg) before additional charges apply.
    weight_margin_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Surcharge applied per kg when actual weight exceeds the allowed margin.
    weight_surcharge_per_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    # ── Relationship ──────────────────────────────────────────────────────────
    organization: Mapped[Organization] = relationship("Organization", back_populates="payment_config", lazy="raise")

    def __repr__(self) -> str:
        return f"<OrgPaymentConfig org={self.organization_id}>"


class OrgPaymentMethod(BaseModel):
    """One enabled payment model for an org — unique per (organization_id, payment_model)."""

    __tablename__ = "org_payment_methods"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "payment_model",
            name="uq_org_payment_methods_org_model",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Payment model ─────────────────────────────────────────────────────────
    payment_model: Mapped[PaymentModel] = mapped_column(Enum(PaymentModel, native_enum=False), nullable=False)

    # ── Billing schedule ──────────────────────────────────────────────────────
    billing_schedule: Mapped[BillingSchedule] = mapped_column(Enum(BillingSchedule, native_enum=False), nullable=False)
    # FIXED_MONTHLY_DATE: day of month (1–28)
    billing_day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # DAYS_AFTER_ORDER: number of days after invoice date
    billing_days_after_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Bank details (BANK_TRANSFER only) ─────────────────────────────────────
    bank_account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bank_account_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bank_sort_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Credit settings (CREDIT_ACCOUNT only) ─────────────────────────────────
    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Warn when utilization reaches this percentage (0–100)
    credit_utilization_warning_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Default flag ──────────────────────────────────────────────────────────
    # Exactly one payment method per org should be marked as default.
    # Setting a new method as default clears is_default on all other methods
    # for the same org (enforced at service layer).
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    # ── Relationship ──────────────────────────────────────────────────────────
    organization: Mapped[Organization] = relationship("Organization", back_populates="payment_methods", lazy="raise")

    def __repr__(self) -> str:
        return f"<OrgPaymentMethod org={self.organization_id} model={self.payment_model} default={self.is_default}>"


class OrgDocument(BaseModel):
    """Document stored in Cloudflare R2 — presigned URLs generated on-demand."""

    __tablename__ = "org_documents"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Auto-generated reference: DOC-{YEAR}-NNNNN (unique, set on create via DB sequence)
    reference: Mapped[str | None] = mapped_column(String(25), nullable=True, unique=True, index=True)

    # ── Document metadata ─────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[OrgDocumentType] = mapped_column(
        Enum(OrgDocumentType, native_enum=False),
        nullable=False,
    )
    category: Mapped[OrgDocumentCategory | None] = mapped_column(
        Enum(OrgDocumentCategory, native_enum=False),
        nullable=True,
    )
    status: Mapped[OrgDocumentStatus] = mapped_column(
        Enum(OrgDocumentStatus, native_enum=False),
        nullable=False,
        default=OrgDocumentStatus.ACTIVE,
        index=True,
    )
    issuing_authority: Mapped[str | None] = mapped_column(String(255), nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidentiality_level: Mapped[OrgDocumentConfidentialityLevel | None] = mapped_column(
        Enum(OrgDocumentConfidentialityLevel, native_enum=False),
        nullable=True,
    )
    # JSON array of tag strings, max 10 enforced at service layer.
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # ── Storage ───────────────────────────────────────────────────────────────
    # R2 object key — e.g. "organizations/<org_id>/documents/<timestamp>_<title>.pdf"
    r2_key: Mapped[str] = mapped_column(String(500), nullable=False)

    # ── Uploader ──────────────────────────────────────────────────────────────
    uploaded_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Denormalised uploader email — survives user deletion; used in activity log.
    uploaded_by_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Soft delete ───────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<OrgDocument {self.title!r} type={self.document_type} org={self.organization_id}>"


class OrgDocumentActivity(BaseModel):
    """Immutable audit log row for a document-level action (upload, download, delete)."""

    __tablename__ = "org_document_activities"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable — SET NULL when document is hard-deleted (soft-delete keeps it).
    document_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    activity_type: Mapped[OrgDocumentActivityType] = mapped_column(
        Enum(OrgDocumentActivityType, native_enum=False),
        nullable=False,
        index=True,
    )

    # ── Denormalised actor snapshot ───────────────────────────────────────────
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. "Admin"

    # ── Denormalised document snapshot ───────────────────────────────────────
    document_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Client context (logged from request headers at action time) ───────────
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)   # IPv4 or IPv6
    browser: Mapped[str | None] = mapped_column(String(100), nullable=True)     # e.g. "Google Chrome"
    device: Mapped[str | None] = mapped_column(String(100), nullable=True)      # e.g. "WIN-3YF8J2L6"
    os: Mapped[str | None] = mapped_column(String(100), nullable=True)          # e.g. "Windows 11"

    def __repr__(self) -> str:
        return f"<OrgDocumentActivity {self.activity_type} doc={self.document_id} org={self.organization_id}>"


class OrgDocumentShare(BaseModel):
    """Secure share link for a document — one row per share event."""

    __tablename__ = "org_document_shares"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Share token (public link secret) ──────────────────────────────────────
    # 32-byte random hex — used in GET /v1/shared/documents/{share_token}
    share_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    # ── Recipients ────────────────────────────────────────────────────────────
    # JSONB array of email strings, e.g. ["a@b.com", "c@d.com"]
    recipients: Mapped[list] = mapped_column(JSONB, nullable=False)

    # ── Actor snapshot ────────────────────────────────────────────────────────
    shared_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Denormalised — survives user deletion; used in sharing history
    shared_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Document snapshot (denormalised for history display) ──────────────────
    document_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_reference: Mapped[str | None] = mapped_column(String(25), nullable=True)

    # ── Share settings ────────────────────────────────────────────────────────
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # When True, recipients must complete an email-OTP challenge each time they access the document.
    otp_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Status & tracking ─────────────────────────────────────────────────────
    status: Mapped[OrgDocumentShareStatus] = mapped_column(
        Enum(OrgDocumentShareStatus, native_enum=False),
        nullable=False,
        default=OrgDocumentShareStatus.ACTIVE,
        index=True,
    )
    # Incremented each time a recipient opens the share link
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Revocation ────────────────────────────────────────────────────────────
    revoked_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        return f"<OrgDocumentShare doc={self.document_id} status={self.status}>"


# ── Document Access OTP ────────────────────────────────────────────────────────


class DocOtp(AppendOnlyModel):
    """Short-lived OTP (10 min) for step-up auth before accessing org documents."""

    __tablename__ = "doc_otps"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    otp_code: Mapped[str] = mapped_column(String(6), nullable=False)
    access_scope: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=DocAccessScope.ORG_DOCUMENTS.value,
        server_default=DocAccessScope.ORG_DOCUMENTS.value,
    )
    is_used: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<DocOtp user={self.user_id} used={self.is_used} scope={self.access_scope}>"


class DocAccessToken(AppendOnlyModel):
    """1-hour access grant issued after OTP verification — passed as X-Doc-Access-Token header."""

    __tablename__ = "doc_access_tokens"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    access_scope: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=DocAccessScope.ORG_DOCUMENTS.value,
        server_default=DocAccessScope.ORG_DOCUMENTS.value,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<DocAccessToken user={self.user_id} scope={self.access_scope}>"


# ── Share-link OTP (unauthenticated recipients) ────────────────────────────────


class ShareOtp(AppendOnlyModel):
    """Short-lived OTP (10 min) for unauthenticated recipients accessing a shared document link."""

    __tablename__ = "share_otps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    share_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    otp_code: Mapped[str] = mapped_column(String(6), nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<ShareOtp email={self.recipient_email} share_token=...{self.share_token[-8:]} used={self.is_used}>"


class ShareAccessToken(AppendOnlyModel):
    """1-hour access grant issued to an external recipient after OTP verification."""

    __tablename__ = "share_access_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    share_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<ShareAccessToken email={self.recipient_email} share_token=...{self.share_token[-8:]}>"


class OrgServiceTierContractLine(BaseModel):
    """Per-organisation contract: which global templates apply, standard vs custom, permitted/default.

    Custom values live on ``service_tier`` rows with ``scope_type=ORG`` (``org_tier_id``).
    Standard rows reference only ``global_template_id`` (``org_tier_id`` is null).
    """

    __tablename__ = "org_service_tier_contract_lines"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    global_template_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("service_tier.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    permitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    org_tier_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("service_tier.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
