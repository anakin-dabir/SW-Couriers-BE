"""ORM models for per-organisation credit and suspension configuration."""

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel


class OrgCreditConfig(BaseModel):
    """Credit configuration for a B2B organisation (one-to-one with Organization)."""

    __tablename__ = "org_credit_configs"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Approved credit limit in GBP
    approved_credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Days the client has to clear overdue credit before suspension kicks in
    credit_clearance_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Warn / require approval when utilization reaches this % (0–100)
    credit_utilization_warning_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Whether bookings are allowed when the credit limit is exceeded
    allow_bookings_beyond_limit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<OrgCreditConfig org={self.organization_id} limit={self.approved_credit_limit}>"


class OrgSuspensionConfig(BaseModel):
    """Suspension trigger conditions and action flags for a B2B organisation (one-to-one)."""

    __tablename__ = "org_suspension_configs"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Ordered list of trigger conditions stored as JSONB.
    # Each element: {"position": int, "logic_operator": "AND"|"OR"|null,
    #                "condition_type": str, "condition_value": str}
    # position=1 always has logic_operator=null (the leading IF condition).
    trigger_conditions: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # ── Global suspension toggle ───────────────────────────────────────────────
    auto_suspension_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Actions on suspension ─────────────────────────────────────────────────
    pause_new_bookings: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    restrict_portal_login: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_finance_team: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_account_manager: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<OrgSuspensionConfig org={self.organization_id} auto={self.auto_suspension_enabled}>"
