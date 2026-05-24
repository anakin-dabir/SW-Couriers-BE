"""Account statements API schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.modules.account_statements.constants import COMPANY_ADDRESS, COMPANY_EMAIL, COMPANY_NAME
from app.modules.account_statements.enums import StatementRowType

StatementRowTypeLiteral = Literal[
    StatementRowType.INVOICE.value,
    StatementRowType.PAYMENT.value,
    StatementRowType.CREDIT_NOTE.value,
    StatementRowType.REFUND.value,
]


class StatementOptionsRequest(BaseModel):
    include_line_item_detail: bool = Field(
        False,
        description="When true, each INVOICE ledger row includes nested line_items[] breakdown.",
    )
    include_credit_notes: bool = Field(True, description="Include CREDIT_NOTE rows in the ledger.")
    include_payment_history: bool = Field(
        True,
        description="Include PAYMENT and REFUND rows in the ledger.",
    )


class StatementPeriodRequest(BaseModel):
    period_start: date
    period_end: date


class StatementPreviewRequest(StatementPeriodRequest, StatementOptionsRequest):
    pass


class StatementCreateRequest(StatementPreviewRequest):
    pass


class StatementProviderInfo(BaseModel):
    """SW Couriers letterhead shown on statement PDF and UI header."""

    name: str = Field(default=COMPANY_NAME, examples=[COMPANY_NAME])
    address: str = Field(default=COMPANY_ADDRESS, examples=[COMPANY_ADDRESS])
    email: str = Field(default=COMPANY_EMAIL, examples=[COMPANY_EMAIL])


class StatementAgingBuckets(BaseModel):
    """AR aging buckets (amounts as decimal strings). Keys match UI: 1-30, 31-60, 61-90, 90+ days."""

    days_1_30: str = Field(default="0.00", description="Outstanding 1-30 days past due.")
    days_31_60: str = Field(default="0.00", description="Outstanding 31-60 days past due.")
    days_61_90: str = Field(default="0.00", description="Outstanding 61-90 days past due.")
    days_90_plus: str = Field(default="0.00", description="Outstanding more than 90 days past due.")

    @classmethod
    def from_aging_dict(cls, aging: dict[str, str] | None) -> StatementAgingBuckets:
        data = aging or {}
        return cls(
            days_1_30=str(data.get("days_1_30", "0.00")),
            days_31_60=str(data.get("days_31_60", "0.00")),
            days_61_90=str(data.get("days_61_90", "0.00")),
            days_90_plus=str(data.get("days_90_plus", "0.00")),
        )


class StatementLineItemRow(BaseModel):
    description: str
    quantity: int
    unit_price: str
    total_price: str


class StatementLedgerRow(BaseModel):
    """One chronological ledger line (invoice, payment, credit note, or refund)."""

    row_type: StatementRowTypeLiteral = Field(
        description="INVOICE | PAYMENT | CREDIT_NOTE | REFUND. UI maps to colored type badges."
    )
    reference_id: str = Field(description="UUID of the source invoice, payment, credit note, or refund.")
    reference_number: str = Field(
        description="Human-readable id shown in UI Invoice ID column (e.g. INV-1051, PAY-00012)."
    )
    issue_date: str = Field(description="ISO date (YYYY-MM-DD). Shown as Issue Date.")
    payment_date: str | None = Field(
        default=None,
        description="ISO date when applicable (payments/refunds); null for invoices.",
    )
    order_ref: str | None = Field(
        default=None,
        description="Linked order reference (e.g. SWC-BK-01234) when the row is invoice-backed.",
    )
    status: str = Field(
        description="Row status for UI badge (e.g. UNPAID, OVERDUE, PAID, DEPOSITED, ISSUED, COMPLETED)."
    )
    amount: str = Field(
        description="Signed movement amount for the period (invoice +, payment/credit -). Decimal string."
    )
    balance: str | None = Field(
        default=None,
        description="Running balance after this row (opening balance + cumulative movements). Decimal string.",
    )
    line_items: list[StatementLineItemRow] = Field(
        default_factory=list,
        description="Present on INVOICE rows when include_line_item_detail was true at preview/generate time.",
    )


class StatementLedgerSnapshot(BaseModel):
    """Full statement body: summary figures plus ledger table rows."""

    opening_balance: str
    closing_balance: str
    total_invoice_amount: str
    total_paid: str
    total_unpaid: str
    total_overdue: str
    aging: StatementAgingBuckets
    currency: str = "GBP"
    truncated: bool = Field(
        False,
        description="True when ledger rows were capped at MAX_LEDGER_ROWS for the period.",
    )
    rows: list[StatementLedgerRow] = Field(
        default_factory=list,
        description="Chronological ledger lines for the statement period.",
    )

    @classmethod
    def from_ledger_dict(cls, data: dict[str, Any] | None) -> StatementLedgerSnapshot | None:
        if not data:
            return None
        payload = dict(data)
        payload["aging"] = StatementAgingBuckets.from_aging_dict(payload.get("aging"))
        return cls.model_validate(payload)


def ledger_snapshot_from_ledger(ledger: Any) -> StatementLedgerSnapshot:
    """Build API ledger snapshot from StatementLedgerResult (includes running balances)."""
    running = ledger.opening_balance
    rows: list[StatementLedgerRow] = []
    for row in ledger.rows:
        running = running + row.amount
        rows.append(
            StatementLedgerRow(
                row_type=row.row_type,
                reference_id=row.reference_id,
                reference_number=row.reference_number,
                issue_date=row.issue_date.isoformat(),
                payment_date=row.payment_date.isoformat() if row.payment_date else None,
                order_ref=row.order_ref,
                status=row.status,
                amount=str(row.display_amount),
                balance=str(running),
                line_items=[
                    StatementLineItemRow(
                        description=li.description,
                        quantity=li.quantity,
                        unit_price=li.unit_price,
                        total_price=li.total_price,
                    )
                    for li in row.line_items
                ],
            )
        )
    return StatementLedgerSnapshot(
        opening_balance=str(ledger.opening_balance),
        closing_balance=str(ledger.closing_balance),
        total_invoice_amount=str(ledger.total_invoice_amount),
        total_paid=str(ledger.total_paid),
        total_unpaid=str(ledger.total_unpaid),
        total_overdue=str(ledger.total_overdue),
        aging=StatementAgingBuckets.from_aging_dict(ledger.aging),
        currency=ledger.currency,
        truncated=ledger.truncated,
        rows=rows,
    )


class StatementListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    statement_number: str = Field(description="Auto-generated id, e.g. ST-000001.")
    organization_id: str
    period_start: date
    period_end: date
    opening_balance: str
    closing_balance: str = Field(description="Closing balance for the period; shown as Balance in list UI.")
    pdf_status: str = Field(description="PENDING | GENERATING | READY | FAILED")
    created_at: datetime
    created_by_user_type: str
    created_by_user_id: str | None = None
    generated_at: datetime | None = None


class StatementDetailResponse(StatementListItem):
    total_invoice_amount: str
    total_paid: str
    total_unpaid: str
    total_overdue: str
    aging: StatementAgingBuckets
    include_line_item_detail: bool
    include_credit_notes: bool
    include_payment_history: bool
    provider: StatementProviderInfo = Field(default_factory=StatementProviderInfo)
    client_name: str = Field(description="Organization trading or legal name.")
    client_address: str = Field(description="Organization registered address, comma-separated.")
    client_email: str | None = Field(
        default=None,
        description="Best-effort billing contact email for the organization (may be null).",
    )
    snapshot: StatementLedgerSnapshot | None = Field(
        default=None,
        description="Frozen ledger at generation time. Same shape as preview `ledger`.",
    )
    failure_reason: str | None = None


class StatementPreviewResponse(BaseModel):
    organization_id: str
    period_start: str = Field(description="ISO date YYYY-MM-DD.")
    period_end: str = Field(description="ISO date YYYY-MM-DD.")
    provider: StatementProviderInfo = Field(default_factory=StatementProviderInfo)
    client_name: str
    client_address: str
    client_email: str | None = None
    ledger: StatementLedgerSnapshot


class StatementSummaryResponse(BaseModel):
    opening_balance: str
    closing_balance: str
    total_invoice_amount: str
    total_paid: str
    total_unpaid: str
    total_overdue: str
    aging: StatementAgingBuckets
    currency: str = "GBP"
    truncated: bool = False


class StatementPdfStatusResponse(BaseModel):
    statement_id: str
    status: str
    job_id: str | None = None
    failure_reason: str | None = None
    generated_at: str | None = None


class StatementSignedUrlRequest(BaseModel):
    disposition: Literal["inline", "attachment"] = "attachment"


class StatementSignedUrlResponse(BaseModel):
    url: str
    expires_at: str
    disposition: str


class StatementSendEmailRequest(BaseModel):
    recipient_email: EmailStr | None = Field(
        default=None,
        description="Recipient address. Alias: email (same as credit note send-to-client).",
    )
    email: EmailStr | None = Field(
        default=None,
        description="Optional alias for recipient_email (credit note send parity).",
    )

    @model_validator(mode="after")
    def resolve_recipient_email(self) -> StatementSendEmailRequest:
        resolved = self.recipient_email or self.email
        if not resolved:
            raise ValueError("recipient_email is required")
        self.recipient_email = resolved
        return self


class StatementSendEmailResponse(BaseModel):
    recipient_email: str
    status: str


class StatementScheduleCreateRequest(BaseModel):
    frequency: Literal["MONTHLY_FIRST", "QUARTERLY", "CUSTOM"]
    valid_from: date | None = Field(
        default=None,
        description="Required for CUSTOM. Optional for MONTHLY_FIRST/QUARTERLY (defaults to today in timezone).",
    )
    valid_to: date | None = Field(
        default=None,
        description="Required for CUSTOM. Optional for MONTHLY_FIRST/QUARTERLY (defaults to ongoing).",
    )
    recipient_email: EmailStr
    timezone: str = "Europe/London"
    interval_days: int | None = Field(
        default=None,
        ge=7,
        le=366,
        description=(
            "Optional for CUSTOM. When omitted, one statement is generated for valid_from..valid_to "
            "on valid_to (06:00 org timezone). When set, repeats every N days within the window."
        ),
    )
    include_line_item_detail: bool = False
    include_credit_notes: bool = True
    include_payment_history: bool = True


class StatementScheduleResponse(BaseModel):
    id: str
    organization_id: str
    frequency: str
    valid_from: date
    valid_to: date
    recipient_email: str
    timezone: str
    interval_days: int | None = Field(
        default=None,
        description="Present when frequency is CUSTOM (days between runs)",
    )
    status: str
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None


def schedule_to_response(schedule: object) -> StatementScheduleResponse:
    from app.modules.account_statements.enums import StatementScheduleFrequency
    from app.modules.account_statements.scheduling import is_once_custom_schedule, parse_interval_days

    freq = str(getattr(schedule, "frequency", ""))
    stored = getattr(schedule, "custom_cron", None)
    interval: int | None = None
    if freq == StatementScheduleFrequency.CUSTOM.value and stored and not is_once_custom_schedule(str(stored)):
        interval = parse_interval_days(str(stored))
    return StatementScheduleResponse(
        id=str(getattr(schedule, "id")),
        organization_id=str(getattr(schedule, "organization_id")),
        frequency=freq,
        valid_from=getattr(schedule, "valid_from"),
        valid_to=getattr(schedule, "valid_to"),
        recipient_email=str(getattr(schedule, "recipient_email")),
        timezone=str(getattr(schedule, "timezone")),
        interval_days=interval,
        status=str(getattr(schedule, "status")),
        next_run_at=getattr(schedule, "next_run_at", None),
        last_run_at=getattr(schedule, "last_run_at", None),
    )


def statement_to_list_item(stmt: object) -> StatementListItem:
    return StatementListItem(
        id=stmt.id,
        statement_number=stmt.statement_number,
        organization_id=stmt.organization_id,
        period_start=stmt.period_start,
        period_end=stmt.period_end,
        opening_balance=str(stmt.opening_balance),
        closing_balance=str(stmt.closing_balance),
        pdf_status=stmt.pdf_status,
        created_at=stmt.created_at,
        created_by_user_type=stmt.created_by_user_type,
        created_by_user_id=stmt.created_by_user_id,
        generated_at=stmt.generated_at,
    )


def _resolve_client_email(org: object | None) -> str | None:
    if org is None:
        return None
    for attr in ("billing_email", "accounts_email", "contact_email", "email"):
        value = getattr(org, attr, None)
        if value and str(value).strip():
            return str(value).strip()
    return None


def statement_to_detail(stmt: object, *, org: object | None = None) -> StatementDetailResponse:
    client_name = ""
    client_address = ""
    if org is not None:
        client_name = getattr(org, "trading_name", "") or getattr(org, "legal_entity_name", "") or ""
        parts = [
            getattr(org, "reg_address_line_1", "") or "",
            getattr(org, "reg_city", "") or "",
            getattr(org, "reg_postcode", "") or "",
            getattr(org, "reg_country", None) or "United Kingdom",
        ]
        client_address = ", ".join(p for p in parts if p)
    return StatementDetailResponse(
        **statement_to_list_item(stmt).model_dump(),
        total_invoice_amount=str(stmt.total_invoice_amount),
        total_paid=str(stmt.total_paid),
        total_unpaid=str(stmt.total_unpaid),
        total_overdue=str(stmt.total_overdue),
        aging=StatementAgingBuckets.from_aging_dict(stmt.aging_json),
        include_line_item_detail=stmt.include_line_item_detail,
        include_credit_notes=stmt.include_credit_notes,
        include_payment_history=stmt.include_payment_history,
        provider=StatementProviderInfo(),
        client_name=client_name,
        client_address=client_address,
        client_email=_resolve_client_email(org),
        snapshot=StatementLedgerSnapshot.from_ledger_dict(stmt.snapshot_json),
        failure_reason=stmt.failure_reason,
    )
