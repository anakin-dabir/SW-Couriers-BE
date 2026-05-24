from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Self

from pydantic import ConfigDict, EmailStr, Field, field_validator, model_validator

from app.common.deps import AuthUser
from app.common.enums import UserRole
from app.common.exceptions import ValidationError
from app.common.schemas import BaseResponseSchema, BaseSchema, CurrencyAmount, PaginationParams
from app.common.validators import normalize_optional_uuid
from app.modules.orders.enums import (
    ClientTypeEnum,
    DeliveryStopStatus,
    DisposalReason,
    OrderStatus,
    PackageStatus,
    ReturnResolution,
    StopNoteType,
    SummaryPeriodPreset,
)
from app.modules.orders.stop_note_utils import normalize_stop_note_type
from app.modules.organizations.enums import PaymentModel


class PackageCreateItem(BaseSchema):
    length_cm: float = Field(..., gt=0)
    width_cm: float = Field(..., gt=0)
    height_cm: float = Field(..., gt=0)
    declared_weight_kg: float = Field(..., gt=0)
    declared_value: Decimal = Field(..., ge=0)


class DeliveryStopCreateItem(BaseSchema):
    recipient_first_name: str = Field(..., min_length=1, max_length=255)
    recipient_last_name: str = Field(..., min_length=1, max_length=255)
    recipient_phone: str = Field(..., min_length=3, max_length=50)
    recipient_email: EmailStr | None = None
    line_1: str = Field(..., min_length=1, max_length=255)
    line_2: str | None = Field(default=None, max_length=255)
    city: str = Field(..., min_length=1, max_length=100)
    postcode: str = Field(..., min_length=1, max_length=20)
    latitude: float | None = None
    longitude: float | None = None
    service_tier_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description=(
            "When pricing the stop, the server loads the organisation's full pricing_plans array and picks the entry "
            "whose plain_name matches this string (case-insensitive; exact first, then substring). "
            "Use this or service_tier_id — at least one is required."
        ),
    )
    service_tier_id: str | None = Field(
        default=None,
        description=(
            "When pricing the stop, the server loads the organisation's full pricing_plans array and picks the entry "
            "whose id_price_tier equals this string. Same value as on the org plan row (not necessarily service_tier.id). "
            "Use this or service_tier_name — at least one is required."
        ),
    )
    signature_required: bool = False
    safe_place_allowed: bool = False
    customer_note: str | None = Field(default=None, max_length=250)
    packages: list[PackageCreateItem] = Field(default_factory=list, min_length=1)

    @model_validator(mode="after")
    def validate_service_tier(self) -> DeliveryStopCreateItem:
        if not self.service_tier_name and not self.service_tier_id:
            raise ValueError("Either service_tier_name or service_tier_id (pricing_plans id_price_tier) must be provided")
        return self


class OrderCreateRequest(BaseSchema):
    client_type: ClientTypeEnum = Field(
        default=ClientTypeEnum.B2B,
        description="B2B: org-scoped order. B2C: consumer order (B2C portal).",
    )
    organization_id: str | None = Field(
        default=None,
        description=(
            "Organisation the order is priced and billed under. Required when client_type is B2B"
        ),
    )
    contact_user_id: str = Field(
        ...,
        min_length=1,
        description="Portal user id the order is for (users.id) — the order customer; must match the caller for B2B self-serve, or be an active org contact when an admin places a B2B order.",
    )
    requested_pickup_date: date | None = Field(
        default=None,
        description="Optional requested pickup date for operations.",
    )
    pickup_address_id: str = Field(
        ...,
        min_length=1,
        description="Saved pickup id from /v1/pickup-addresses (FK on the order to pickup_addresses.id).",
    )
    payment_method: PaymentModel = Field(
        ...,
        description="CARD | BANK_TRANSFER | CREDIT_ACCOUNT | CASH — must match an enabled payment method on the organisation",
    )
    payment_method_id: str = Field(
        ...,
        min_length=1,
        description="UUID of the organisation's org_payment_methods row; its payment_model must match payment_method",
    )
    credit_card_id: str | None = Field(
        default=None,
        description="UUID of a row in credit_cards (Braintree vault) for this organisation — required when payment_method is CARD",
    )
    payment_method_nonce: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description=(
            "Braintree payment method nonce from threeDSecure.verifyCard for the order total — required when payment_method is CARD"
        ),
    )
    delivery_stops: list[DeliveryStopCreateItem] = Field(default_factory=list, min_length=1)

    @model_validator(mode="after")
    def validate_organization_id_when_b2b(self) -> OrderCreateRequest:
        if self.client_type == ClientTypeEnum.B2B:
            if not self.organization_id or not str(self.organization_id).strip():
                raise ValueError("organization_id is required when client_type is B2B")
        return self

    @model_validator(mode="after")
    def validate_payment_pair(self) -> OrderCreateRequest:
        if self.payment_method == PaymentModel.CARD:
            if not self.credit_card_id:
                raise ValueError("credit_card_id is required when payment_method is CARD")
            if not self.payment_method_nonce or not str(self.payment_method_nonce).strip():
                raise ValueError("payment_method_nonce is required when payment_method is CARD")
        else:
            if self.credit_card_id is not None:
                raise ValueError("credit_card_id must be omitted when payment_method is not CARD")
            if self.payment_method_nonce is not None:
                raise ValueError("payment_method_nonce must be omitted when payment_method is not CARD")
        return self


class OrderPriceBreakdownRequest(BaseSchema):
    client_type: ClientTypeEnum = Field(
        default=ClientTypeEnum.B2B,
        description="B2B: org-scoped pricing. B2C: consumer pricing (same rules as order create).",
    )
    organization_id: str | None = Field(
        default=None,
        description="Organisation used to load pricing_plans, discounts, VAT, and limits. Required when client_type is B2B.",
    )
    delivery_stops: list[DeliveryStopCreateItem] = Field(default_factory=list, min_length=1)

    @model_validator(mode="after")
    def validate_organization_id_when_b2b(self) -> Self:
        if self.client_type == ClientTypeEnum.B2B and (
            not self.organization_id or not str(self.organization_id).strip()
        ):
            raise ValueError("organization_id is required when client_type is B2B")
        return self


class OrderPriceBreakdownPlanSnapshot(BaseSchema):
    model_config = ConfigDict(extra="ignore")

    id_price_tier: str | None = None
    plain_name: str | None = None
    plain_type: str | None = None
    days: list | dict | str | float | int | bool | None = None
    base_price: str
    price_per_package: str
    price_per_kg: str
    tier_name_at_order_time: str | None = None
    # Presentation metadata carried through from the live effective-tier row so the FE can
    # render the tier badge (color + icon + name) without an extra lookup.
    service_tier_id: str | None = None
    global_tier_id: str | None = None
    color: str | None = None
    icon: str | None = None
    description: str | None = None
    duration_days: int | None = None
    error_margin_kg: int | None = None
    available_for: str | None = None
    scope_type: str | None = None
    source_scope_type: str | None = None
    scope_org_id: str | None = None
    is_default: bool | None = None
    is_override: bool | None = None

    @field_validator("id_price_tier", mode="before")
    @classmethod
    def coerce_id_price_tier(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class OrderPriceBreakdownWeightCharge(BaseSchema):
    price_per_kg: str
    weight_kg: float | None = None
    amount: str


class OrderPriceBreakdownPackage(BaseSchema):
    id: str | None = None
    package_id: str | None = None
    package_index: int
    declared_weight_kg: float | None = None
    per_package_charge: str
    weight_charge: OrderPriceBreakdownWeightCharge
    total: str


class OrderPriceBreakdownDiscount(BaseSchema):
    type: str
    service_tier_id: str | None = None
    value: str
    amount: str
    order_count: int | None = None


class OrderPriceBreakdownStop(BaseSchema):
    id: str | None = None
    tracking_id: str | None = None
    stop_index: int
    service_tier: str | None = None
    service_tier_id: str | None = None
    pricing_plan: OrderPriceBreakdownPlanSnapshot
    base_price: str
    packages: list[OrderPriceBreakdownPackage]
    packages_count: int
    packages_subtotal: str
    pre_discount_subtotal: str
    discounts: list[OrderPriceBreakdownDiscount]
    total_discount: str
    subtotal_after_discount: str
    min_charge: str
    min_charge_applied: bool
    subtotal: str
    vat_rate: str
    vat_rate_pct: str
    vat_amount: str
    total: str

    @field_validator("service_tier_id", mode="before")
    @classmethod
    def coerce_service_tier_id(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class OrderPriceBreakdownDetail(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    order_id: str | None = None
    currency: str
    computed_at: str
    stops: list[OrderPriceBreakdownStop]
    packages_count: int
    subtotal: str
    vat_amount: str
    total: str


class OrderPriceBreakdownResponse(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    subtotal: str
    vat_amount: str
    total_amount: str
    breakdown: OrderPriceBreakdownDetail


def validate_create_order_for_actor(user: AuthUser, body: OrderCreateRequest | OrderPriceBreakdownRequest) -> None:
    if user.role in (UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value):
        return
    if user.role == UserRole.CUSTOMER_B2B.value:
        if body.client_type != ClientTypeEnum.B2B:
            raise ValidationError("client_type must be B2B for B2B portal users")
        return
    if user.role == UserRole.CUSTOMER_B2C.value:
        if body.client_type != ClientTypeEnum.B2C:
            raise ValidationError("client_type must be B2C for B2C portal users")
        return


class OrderDraftPayload(BaseSchema):
    model_config = ConfigDict(extra="ignore")

    client_type: ClientTypeEnum = ClientTypeEnum.B2B
    organization_id: str = Field(
        description="Organisation UUID (organizations.id).",
    )
    contact_user_id: str | None = Field(
        default=None,
        description="Portal user UUID (users.id) the order is for.",
    )
    requested_pickup_date: date | None = None
    pickup_address_id: str | None = Field(
        default=None,
        description="Saved pickup UUID from GET/POST /v1/pickup-addresses (pickup_addresses.id).",
    )
    payment_method: PaymentModel | None = None
    payment_method_id: str | None = Field(
        default=None,
        description="UUID of an org_payment_methods row.",
    )
    credit_card_id: str | None = Field(
        default=None,
        description="UUID of a credit_cards row when paying by card.",
    )
    delivery_stops: list[DeliveryStopCreateItem] | None = None
    total_amount: Decimal | None = Field(
        default=None,
        ge=0,
        decimal_places=2,
        description=(
            "Snapshot of the Step-4 price-breakdown grand total. Stored on the draft only; "
            "ignored when the draft is later submitted as an order (the order's price "
            "breakdown is re-computed authoritatively at create time)."
        ),
    )

    @field_validator(
        "organization_id",
        "contact_user_id",
        "pickup_address_id",
        "payment_method_id",
        "credit_card_id",
        mode="before",
    )
    @classmethod
    def validate_optional_uuid_fields(cls, value: object | None, info) -> str | None:
        field_name = info.field_name if info.field_name is not None else "id"
        return normalize_optional_uuid(value, field=field_name)


class DraftContactUserInfo(BaseSchema):
    id: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    contact_role: str | None = None


class DraftResponse(BaseResponseSchema):
    organization_id: str | None = None
    customer_id: str | None = None
    contact_user: DraftContactUserInfo | None = None
    payload: dict = Field(default_factory=dict)
    total_amount: Decimal | None = None


class DraftListItem(BaseSchema):
    id: str
    created_at: datetime
    draft_id: str | None = None
    order_id: str | None = None
    organization_id: str | None = None
    customer_id: str | None = None
    pickup_address_id: str | None = None
    contact_name: str | None = None
    created_by: str | None = None
    pickup_address: str | None = None
    package_count: int | None = None
    delivery_stop_count: int | None = None
    total_value: Decimal | None = None


class DraftListResponse(BaseSchema):
    items: list[DraftListItem]
    total: int
    offset: int
    limit: int


class DraftListParams(PaginationParams):
    organization_id: str | None = Field(
        default=None,
        description="Organisation scope. When omitted, the token organisation is used (non-admins) or a token or explicit value is required for admins.",
    )
    search: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Search by draft id, pickup address, or creator name",
    )
    date_from: date | None = Field(default=None, description="Filter created_at >= this date")
    date_to: date | None = Field(default=None, description="Filter created_at <= this date")

    @model_validator(mode="after")
    def validate_date_range(self) -> DraftListParams:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot be later than date_to")
        return self


class PackageEntry(BaseResponseSchema):
    order_id: str
    delivery_stop_id: str | None = None
    package_id: str | None = None
    status: PackageStatus
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    declared_weight_kg: float | None = None
    weight_kg: float | None = None
    declared_value: Decimal | None = None
    is_damaged: bool = False
    price_breakdown: dict | None = None


class OrderDetailPackageEntry(BaseResponseSchema):
    order_id: str
    delivery_stop_id: str | None = None
    package_id: str | None = None
    status: PackageStatus
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    declared_weight_kg: float | None = None
    weight_kg: float | None = None
    declared_value: Decimal | None = None
    is_damaged: bool = False


class DeliveryStopEntry(BaseResponseSchema):
    order_id: str
    tracking_id: str | None = None
    recipient_first_name: str | None = None
    recipient_last_name: str | None = None
    recipient_phone: str | None = None
    recipient_email: str | None = None
    line_1: str | None = None
    line_2: str | None = None
    city: str | None = None
    postcode: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    service_tier: str | None = Field(
        default=None,
        description="Human label from the matched pricing_plans entry (plain_name) after the stop was priced.",
    )
    service_tier_id: str | None = Field(
        default=None,
        description=(
            "Not persisted on the stop row (null). The plan id you matched on create is repeated under "
            "price_breakdown.pricing_plan.id_price_tier together with plain_type, plain_name, and rates."
        ),
    )
    signature_required: bool = False
    safe_place_allowed: bool = False
    status: DeliveryStopStatus
    packages_count: int = 0
    packages: list[PackageEntry] = Field(default_factory=list)
    price_breakdown: dict | None = None


class OrderDetailStopEntry(BaseResponseSchema):
    order_id: str
    tracking_id: str | None = None
    recipient_first_name: str | None = None
    recipient_last_name: str | None = None
    recipient_phone: str | None = None
    recipient_email: str | None = None
    line_1: str | None = None
    line_2: str | None = None
    city: str | None = None
    postcode: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    service_tier: str | None = Field(
        default=None,
        description="Human label from the matched pricing_plans entry (plain_name) after the stop was priced.",
    )
    service_tier_id: str | None = Field(
        default=None,
        description=(
            "Not persisted on the stop row (null). The matched plan id and type live on the order under "
            "price_breakdown.stops[].pricing_plan (id_price_tier, plain_type, plain_name, and amounts)."
        ),
    )
    signature_required: bool = False
    safe_place_allowed: bool = False
    status: DeliveryStopStatus
    packages_count: int = 0


class UserBrief(BaseSchema):
    id: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None


class OrderDetailResponse(BaseResponseSchema):
    order_id: str
    master_label_id: str | None = None
    organization_id: str
    customer_id: str | None = None
    pickup_address_id: str | None = None
    pickup_address: str | None = Field(
        default=None,
        description="Single-line formatted address from the linked pickup_addresses row (includes country when set).",
    )
    pickup_line_1: str | None = Field(default=None, description="Pickup address line 1.")
    pickup_line_2: str | None = Field(default=None, description="Pickup address line 2.")
    pickup_city: str | None = Field(default=None, description="Pickup city.")
    pickup_state: str | None = Field(default=None, description="Pickup county / state.")
    pickup_country: str | None = Field(default=None, description="Pickup country.")
    pickup_postcode: str | None = Field(default=None, description="Postcode from the linked pickup address.")
    pickup_contact_name: str | None = Field(
        default=None,
        description="Display name from the organisation's primary org_contacts row (linked user's first and last name).",
    )
    pickup_contact_phone: str | None = Field(
        default=None,
        description="Phone from the organisation's primary org_contacts.contact_number.",
    )
    requested_pickup_date: date | None = None
    status: OrderStatus
    payment_method: PaymentModel | None = None
    payment_method_id: str | None = None
    card_last_four: str | None = Field(
        default=None,
        description="Last four digits of the saved CreditCard charged for this order. Populated only when payment_method is CARD.",
    )
    created_by_id: str | None = None
    created_by: UserBrief | None = Field(
        default=None,
        description="Resolved user who created the order (id, first_name, last_name, email).",
    )
    contact_user_id: str | None = None
    contact_user: UserBrief | None = Field(
        default=None,
        description="Resolved pickup contact user (id, first_name, last_name, email).",
    )
    linked_invoice_id: str | None = Field(
        default=None,
        description="Id of the Invoice generated for this order, when one exists.",
    )
    linked_invoice_number: str | None = Field(
        default=None,
        description="Human-readable invoice number (e.g. INV-000123) for the linked invoice.",
    )
    subtotal: Decimal = Decimal("0")
    vat_amount: Decimal = Decimal("0")
    total_amount: Decimal = Decimal("0")
    price_breakdown: dict | None = None
    delivery_stops: list[OrderDetailStopEntry] = Field(default_factory=list)


class EntityStatusEventItem(BaseSchema):
    id: str
    created_at: datetime
    from_status: str | None = None
    to_status: str
    display_label: str
    actor_user_id: str | None = None


class DeliveryStopTimelineSlice(BaseSchema):
    delivery_stop_id: str
    tracking_id: str | None = None
    events: list[EntityStatusEventItem] = Field(default_factory=list)


class PackageTimelineSlice(BaseSchema):
    package_id: str
    package_reference: str
    delivery_stop_id: str | None = None
    events: list[EntityStatusEventItem] = Field(default_factory=list)


class OrderTimelineResponse(BaseSchema):
    order_id: str
    order_events: list[EntityStatusEventItem] = Field(default_factory=list)
    delivery_stops: list[DeliveryStopTimelineSlice] = Field(default_factory=list)
    packages: list[PackageTimelineSlice] = Field(default_factory=list)


class DeliveryStopDetailPackageEntry(BaseResponseSchema):
    order_id: str
    delivery_stop_id: str | None = None
    package_id: str | None = None
    status: PackageStatus
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    declared_weight_kg: float | None = None
    weight_kg: float | None = None
    declared_value: Decimal | None = None
    is_damaged: bool = False
    events: list[EntityStatusEventItem] = Field(default_factory=list)


class StopPodPhotoEntry(BaseSchema):
    id: str
    image_key: str
    image_url: str | None = None
    sort_order: int = 0


class StopPodSummary(BaseSchema):
    photos_count: int = 0
    signature_image_key: str | None = None
    signature_image_url: str | None = None
    signature_required_snapshot: bool = False
    completed_at: datetime | None = None
    photos: list[StopPodPhotoEntry] = Field(default_factory=list)


class StopReturnEvidenceEntry(BaseSchema):
    id: str
    image_key: str
    image_url: str | None = None
    sort_order: int = 0


class StopReturnEvidenceSummary(BaseSchema):
    photos_count: int = 0
    photos: list[StopReturnEvidenceEntry] = Field(default_factory=list)


class StopAttemptEntry(BaseSchema):
    id: str
    attempt_number: int
    attempted_at: datetime
    driver_id: str | None = None
    driver_name: str | None = None
    vehicle_id: str | None = None
    vehicle_name: str | None = None
    route_id: str | None = None
    failure_reason: str | None = None
    notes: str | None = None
    is_final: bool = False


class DeliveryStopDetailResponse(BaseResponseSchema):
    order_id: str
    order_reference: str | None = Field(
        default=None,
        description="Human-readable order id (e.g. SWC-ORD-000073).",
    )
    organization_id: str | None = None
    stop_index: int | None = None
    tracking_id: str | None = None
    recipient_first_name: str | None = None
    recipient_last_name: str | None = None
    recipient_phone: str | None = None
    recipient_email: str | None = None
    line_1: str | None = None
    line_2: str | None = None
    city: str | None = None
    postcode: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    service_tier: str | None = None
    service_tier_id: str | None = None
    pricing_plan: dict | None = None
    signature_required: bool = False
    safe_place_allowed: bool = False
    status: DeliveryStopStatus
    scheduled_delivery_date: date | None = None
    actual_delivery_date: date | None = None
    delivery_attempts: int = 0
    max_delivery_attempts: int = 3
    packages_count: int = 0
    packages: list[DeliveryStopDetailPackageEntry] = Field(default_factory=list)
    events: list[EntityStatusEventItem] = Field(default_factory=list)
    pod: StopPodSummary | None = None
    return_evidence: StopReturnEvidenceSummary | None = None
    failed_attempts: list[StopAttemptEntry] = Field(default_factory=list)
    return_attempts: list[StopAttemptEntry] = Field(default_factory=list)


class MasterLabelEntry(BaseSchema):
    master_label_id: str
    pickup_address: str | None = None
    barcode_value: str
    qr_value: str
    delivery_stops_count: int = 0
    total_packages: int = 0
    total_weight_kg: float | None = None
    total_volume_m3: float | None = None


class PickupLabelEntry(BaseSchema):
    package_id: str
    tracking_id: str
    recipient_name: str
    recipient_address: str
    pickup_address: str | None = None
    return_address: str | None = None
    signature_required: bool = False
    weight_kg: float | None = None
    dimensions_cm: str | None = None
    volume_m3: float | None = None
    delivery_days: int | None = None
    delivery_label: str | None = None


class OrderLabelsResponse(BaseSchema):
    id: str
    order_id: str
    master_label: MasterLabelEntry
    pickup_labels: list[PickupLabelEntry] = Field(default_factory=list)


class CreatedByEntry(BaseSchema):
    id: str
    name: str


class OrderClientEntry(BaseSchema):
    id: str
    name: str | None = None
    reference: str | None = None
    type: str = "B2B"


class OrderListItem(BaseSchema):
    id: str
    created_at: datetime
    order_id: str
    organization_id: str
    client: OrderClientEntry | None = None
    pickup_address_id: str | None = None
    contact_name: str | None = None
    pickup_address: str
    pickup_postcode: str | None = Field(default=None, max_length=20, description="Postcode from the linked pickup address.")
    total_amount: CurrencyAmount = Field(default=Decimal("0"), description="Order total including VAT (GBP).")
    created_by: CreatedByEntry | None = None
    status: OrderStatus
    package_count: int = 0
    delivery_stop_count: int = 0


class OrderListResponse(BaseSchema):
    items: list[OrderListItem]
    total: int
    offset: int
    limit: int


class OrderListParams(PaginationParams):
    search: str | None = Field(default=None, min_length=1, max_length=255, description="Search by order_id or pickup address")
    status: list[OrderStatus] | None = Field(default=None, min_length=1, description="Order status filter (multi-select)")
    date_from: date | None = Field(default=None, description="Filter created_at >= this date")
    date_to: date | None = Field(default=None, description="Filter created_at <= this date")
    organization_id: str | None = Field(
        default=None,
        description="Organisation to list orders for. When omitted, the token organisation is used (non-admins) or a token or explicit value is required for admins.",
    )

    @model_validator(mode="after")
    def validate_date_range(self) -> OrderListParams:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot be later than date_to")
        return self


class DeliveryStopsResponse(BaseSchema):
    order_id: str
    master_label_id: str | None = None
    items: list[DeliveryStopEntry]


class StopPackagesResponse(BaseSchema):
    order_id: str
    stop_id: str
    tracking_id: str | None = None
    items: list[PackageEntry]


class StopNoteImageEntry(BaseResponseSchema):
    stop_note_id: str
    image_key: str
    sort_order: int
    image_url: str | None = Field(
        default=None,
        description="Time-limited signed URL for the image when applicable.",
    )


class StopNoteEntry(BaseResponseSchema):
    """One operational note on a delivery stop (customer, package issue, or admin instruction)."""

    delivery_stop_id: str = Field(description="Parent delivery stop UUID.")
    note_type: str = Field(
        description=(
            "Persisted category for UI styling: `CUSTOMER` (customer / booking instruction), "
            "`PACKAGE_ISSUE_NOTE` (damaged or scoped package issue; see `package_ids`), "
            "`ADMIN` (operations instruction)."
        ),
    )
    message: str = Field(description="Note body shown on the delivery notes screen.")
    is_blocking: bool = Field(description="When true, driver apps may require acknowledgement before proceeding.")
    sort_order: int = Field(description="Display order among notes on the same stop.")
    package_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Package row UUIDs (`packages.id`) linked to this note. "
            "Only populated for `PACKAGE_ISSUE_NOTE`; empty for `CUSTOMER` and `ADMIN`. "
            "Stale IDs are omitted on read."
        ),
    )
    images: list[StopNoteImageEntry] = Field(
        default_factory=list,
        description="Gallery attachments (e.g. damage photos for package issue notes).",
    )


class StopNotesResponse(BaseSchema):
    order_id: str
    stop_id: str
    items: list[StopNoteEntry] = Field(default_factory=list)


class StopNoteCreateRequest(BaseSchema):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "note_type": "ADMIN",
                    "message": "Call customer 10 minutes before arrival.",
                    "is_blocking": True,
                    "sort_order": 0,
                },
                {
                    "note_type": "CUSTOMER",
                    "message": "Leave parcel with neighbour at number 12 if unavailable.",
                    "is_blocking": False,
                    "sort_order": 0,
                },
                {
                    "note_type": "PACKAGE_ISSUE_NOTE",
                    "message": "Parcel received with damaged outer packaging.",
                    "is_blocking": False,
                    "sort_order": 1,
                    "package_ids": [
                        "3fa85f64-5717-4562-b3fc-2c963f66afa17",
                        "4fa85f64-5717-4562-b3fc-2c963f66afa12",
                    ],
                },
            ]
        }
    )

    note_type: str = Field(
        ...,
        min_length=1,
        max_length=30,
        description=(
            "`ADMIN` (alias `ADMIN_NOTE`), `CUSTOMER` (aliases `CLIENT`, `CLIENT_NOTE`), or `PACKAGE_ISSUE_NOTE`. "
            "Stored value is always the canonical enum string."
        ),
    )
    message: str = Field(..., min_length=1, max_length=1000)
    is_blocking: bool = Field(
        default=False,
        description="Blocking notes may require driver acknowledgement in the route app.",
    )
    sort_order: int = Field(default=0, ge=0)
    package_ids: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of `packages.id` UUIDs for `PACKAGE_ISSUE_NOTE` only (UI: package chips). "
            "Must be omitted or null for `ADMIN` / `CUSTOMER`."
        ),
    )

    @model_validator(mode="after")
    def package_ids_allowed_only_for_issue_note(self) -> Self:
        try:
            persisted = normalize_stop_note_type(self.note_type)
        except ValidationError as exc:
            raise ValueError(exc.message) from exc
        if self.package_ids:
            if persisted != StopNoteType.PACKAGE_ISSUE_NOTE.value:
                raise ValueError(
                    "package_ids is only allowed when note_type is PACKAGE_ISSUE_NOTE "
                    "(customer and admin notes must omit package_ids)."
                )
        return self


class StopNoteUpdateRequest(BaseSchema):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"message": "Updated admin instruction only."},
                {"note_type": "CUSTOMER", "message": "Leave with concierge if no answer."},
                {
                    "note_type": "PACKAGE_ISSUE_NOTE",
                    "message": "Damage limited to outer box; contents inspected.",
                    "package_ids": ["3fa85f64-5717-4562-b3fc-2c963f66afa6"],
                },
            ]
        }
    )

    note_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=30,
        description="When set, normalized the same way as create (aliases allowed).",
    )
    message: str | None = Field(default=None, min_length=1, max_length=1000)
    is_blocking: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)
    package_ids: list[str] | None = Field(
        default=None,
        description=(
            "Omit to leave unchanged. Set to adjust linked packages when `note_type` is or becomes "
            "`PACKAGE_ISSUE_NOTE`. Use explicit null/empty only through PATCH body per API rules."
        ),
    )

    @model_validator(mode="after")
    def package_ids_match_declared_note_type(self) -> Self:
        if self.note_type is None:
            return self
        try:
            persisted = normalize_stop_note_type(self.note_type)
        except ValidationError as exc:
            raise ValueError(exc.message) from exc
        if self.package_ids:
            if persisted != StopNoteType.PACKAGE_ISSUE_NOTE.value:
                raise ValueError(
                    "package_ids is only allowed when note_type is PACKAGE_ISSUE_NOTE "
                    "(customer and admin notes must omit package_ids)."
                )
        return self


class OrderSummaryStat(BaseSchema):
    current: int = 0
    previous: int = 0
    change_pct: float | None = Field(
        default=None,
        description="Percent change from previous period; null when previous period had no value",
    )


class FloatSummaryStat(BaseSchema):
    current: float | None = Field(default=None, description="Current period value")
    previous: float | None = Field(default=None, description="Comparison period value")
    change_pct: float | None = Field(
        default=None,
        description="Percent change from comparison period; null when previous period had no value",
    )


class OrderSummaryResponse(BaseSchema):
    period_from: date | None = None
    period_to: date | None = None
    previous_period_from: date | None = None
    previous_period_to: date | None = None
    comparison_label: str = Field(
        ...,
        description="What previous period values represent (e.g. yesterday, previous week)",
    )
    total_orders: OrderSummaryStat
    pickups_on_route: OrderSummaryStat
    delivered: OrderSummaryStat
    cancelled: OrderSummaryStat
    failed: OrderSummaryStat
    returned: OrderSummaryStat


class FailedDeliveriesSummaryResponse(BaseSchema):
    period_from: date | None = None
    period_to: date | None = None
    previous_period_from: date | None = None
    previous_period_to: date | None = None
    comparison_label: str = Field(
        ...,
        description="What previous period values represent (e.g. yesterday, previous week)",
    )
    total_failed: OrderSummaryStat
    missing: OrderSummaryStat
    damaged: OrderSummaryStat
    cancelled: OrderSummaryStat
    customer_not_home: OrderSummaryStat
    refused: OrderSummaryStat
    disposed: OrderSummaryStat


class ReturnsSummaryResponse(BaseSchema):
    period_from: date | None = None
    period_to: date | None = None
    previous_period_from: date | None = None
    previous_period_to: date | None = None
    comparison_label: str = Field(
        ...,
        description="What previous period values represent (e.g. yesterday, previous week)",
    )
    total_returns: OrderSummaryStat
    returns_in_transit: OrderSummaryStat
    disposed_packages: OrderSummaryStat
    returned_packages: OrderSummaryStat
    initiated: OrderSummaryStat
    avg_resolution_days: FloatSummaryStat


class FailedDeliveryPackageEntry(BaseSchema):
    id: str
    package_id: str
    status: PackageStatus
    reason: str | None = None
    status_events: list[EntityStatusEventItem] = Field(default_factory=list)


class FailedDeliveryStopItem(BaseSchema):
    delivery_stop_id: str
    tracking_id: str | None = None
    postcode: str | None = None
    order_id: str
    order_reference: str
    stop_status: DeliveryStopStatus
    attempt_number: int = 0
    max_attempts: int = 3
    previous_attempt_at: datetime | None = None
    next_attempt_at: datetime | None = None
    stop_status_events: list[EntityStatusEventItem] = Field(default_factory=list)
    packages: list[FailedDeliveryPackageEntry] = Field(default_factory=list)


class ReturnPackageEntry(BaseSchema):
    id: str
    package_id: str
    status: PackageStatus
    return_reason: str | None = None
    initiated_at: datetime | None = None
    status_events: list[EntityStatusEventItem] = Field(default_factory=list)


class ReturnStopItem(BaseSchema):
    delivery_stop_id: str
    tracking_id: str | None = None
    postcode: str | None = None
    order_id: str
    order_reference: str
    stop_status: DeliveryStopStatus | None = None
    attempt_number: int = 0
    max_attempts: int = 3
    initiated_at: datetime | None = None
    stop_status_events: list[EntityStatusEventItem] = Field(default_factory=list)
    packages: list[ReturnPackageEntry] = Field(default_factory=list)


class SummaryDateRangeParams(BaseSchema):
    period: SummaryPeriodPreset | None = Field(
        default=None,
        description=(
            "Preset window: TODAY, LAST_7_DAYS, LAST_WEEK (Mon–Sun prior week), "
            "LAST_30_DAYS, LAST_MONTH (previous calendar month). When set, `date_from`/`date_to` are ignored."
        ),
    )
    date_from: date | None = Field(default=None, description="Start of the reporting period (inclusive)")
    date_to: date | None = Field(default=None, description="End of the reporting period (inclusive)")
    organization_id: str | None = Field(
        default=None,
        description="Organisation scope. When omitted, the token organisation is used (non-admins) or a token or explicit value is required for admins.",
    )

    @model_validator(mode="after")
    def validate_date_range(self) -> Self:
        if self.period is None and (self.date_from is None or self.date_to is None):
            raise ValueError("Either `period` or both `date_from` and `date_to` is required")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot be later than date_to")
        return self


class FailedDeliveryListParams(PaginationParams):
    organization_id: str | None = Field(
        default=None,
        description="Organisation scope. When omitted, the token organisation is used (non-admins) or a token or explicit value is required for admins.",
    )
    search: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Search by tracking ID, postal code or order reference",
    )
    package_status: list[PackageStatus] | None = Field(
        default=None,
        min_length=1,
        description="Filter by failure package status (multi-select)",
    )
    attempt_number: list[int] | None = Field(
        default=None,
        min_length=1,
        description="Filter by attempt number 0-3 (multi-select)",
    )
    date_from: date | None = Field(default=None, description="Filter delivery_stop created_at >= this date")
    date_to: date | None = Field(default=None, description="Filter delivery_stop created_at <= this date")

    @model_validator(mode="after")
    def validate_filters(self) -> FailedDeliveryListParams:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot be later than date_to")
        if self.attempt_number is not None:
            for n in self.attempt_number:
                if n < 0 or n > 3:
                    raise ValueError("attempt_number must be between 0 and 3")
        return self


class ReturnListParams(PaginationParams):
    organization_id: str | None = Field(
        default=None,
        description="Organisation scope. When omitted, the token organisation is used (non-admins) or a token or explicit value is required for admins.",
    )
    search: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Search by tracking ID, postal code or order reference",
    )
    status: list[PackageStatus] | None = Field(
        default=None,
        min_length=1,
        description="Filter by return package status (multi-select)",
    )
    attempt_number: list[int] | None = Field(
        default=None,
        min_length=1,
        description="Filter by attempt number 0-3 (multi-select)",
    )
    date_from: date | None = Field(default=None, description="Filter initiated_at >= this date")
    date_to: date | None = Field(default=None, description="Filter initiated_at <= this date")

    @model_validator(mode="after")
    def validate_date_range(self) -> ReturnListParams:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot be later than date_to")
        if self.attempt_number is not None:
            for n in self.attempt_number:
                if n < 0 or n > 3:
                    raise ValueError("attempt_number must be between 0 and 3")
        return self


class RescheduleStopRequest(BaseSchema):
    scheduled_for: date = Field(..., description="New attempt date for this delivery stop (must be today or later)")

    @model_validator(mode="after")
    def validate_future(self) -> RescheduleStopRequest:
        from datetime import date as _date

        if self.scheduled_for < _date.today():
            raise ValueError("scheduled_for cannot be in the past")
        return self


class StopActionResponse(BaseSchema):
    delivery_stop_id: str
    tracking_id: str | None = None
    stop_status: DeliveryStopStatus
    scheduled_for: date | None = None
    affected_package_ids: list[str] = Field(default_factory=list)


class OrderCancelRequest(BaseSchema):
    notes: str | None = Field(default=None, max_length=2000)


class OrderCancelResponse(BaseSchema):
    id: str
    order_id: str
    status: OrderStatus


class DeliveryStopCancelResponse(BaseSchema):
    order_id: str
    delivery_stop_id: str
    tracking_id: str | None = None
    stop_status: DeliveryStopStatus
    order_status: OrderStatus
    affected_package_ids: list[str] = Field(default_factory=list)


class PackageActionResponse(BaseSchema):
    id: str
    package_id: str
    delivery_stop_id: str | None = None
    status: PackageStatus
    stop_status: DeliveryStopStatus | None = None
    order_status: OrderStatus | None = None


class ResolveReturnRequest(BaseSchema):
    resolution: ReturnResolution = Field(..., description="RETURN_TO_SENDER or DISPOSE")

    return_dispatch_date: date | None = Field(
        default=None,
        description="Required when resolution is RETURN_TO_SENDER — date the return shipment leaves",
    )
    return_cost: Decimal | None = Field(
        default=None,
        ge=0,
        description="Cost charged for the return (RETURN_TO_SENDER only). Ignored when waive_return_cost is true.",
    )
    waive_return_cost: bool = Field(
        default=False,
        description="If true, no return cost is charged regardless of return_cost",
    )
    return_notes: str | None = Field(default=None, max_length=2000)

    disposal_reason: DisposalReason | None = Field(
        default=None,
        description="Required when resolution is DISPOSE",
    )
    resolution_notes: str | None = Field(
        default=None,
        max_length=2000,
        description="Free-text disposal notes (DISPOSE only)",
    )

    @model_validator(mode="after")
    def validate_resolution_payload(self) -> ResolveReturnRequest:
        if self.resolution == ReturnResolution.RETURN_TO_SENDER:
            if self.return_dispatch_date is None:
                raise ValueError("return_dispatch_date is required when resolution is RETURN_TO_SENDER")
            if self.disposal_reason is not None or self.resolution_notes is not None:
                raise ValueError("disposal_reason / resolution_notes are only allowed when resolution is DISPOSE")
            if not self.waive_return_cost and self.return_cost is None:
                raise ValueError("return_cost is required when waive_return_cost is false")
        else:
            if self.disposal_reason is None:
                raise ValueError("disposal_reason is required when resolution is DISPOSE")
            if (
                self.return_dispatch_date is not None
                or self.return_cost is not None
                or self.waive_return_cost
            ):
                raise ValueError(
                    "return_dispatch_date, return_cost and waive_return_cost are only allowed when resolution is RETURN_TO_SENDER"
                )
        return self


class ReturnEvidenceImageEntry(BaseResponseSchema):
    delivery_stop_id: str
    image_key: str
    image_url: str | None = None
    sort_order: int


class ResolveReturnResponse(BaseSchema):
    delivery_stop_id: str
    tracking_id: str | None = None
    stop_status: DeliveryStopStatus
    return_resolution: ReturnResolution
    return_resolved_at: datetime | None = None
    return_dispatch_date: date | None = None
    return_cost: Decimal | None = None
    return_cost_waived: bool = False
    return_notes: str | None = None
    disposal_reason: DisposalReason | None = None
    affected_package_ids: list[str] = Field(default_factory=list)
    evidence_images: list[ReturnEvidenceImageEntry] = Field(default_factory=list)


class PackageUpdateItem(BaseSchema):
    id: str = Field(..., description="UUID of the package to update")
    length_cm: float | None = Field(default=None, gt=0)
    width_cm: float | None = Field(default=None, gt=0)
    height_cm: float | None = Field(default=None, gt=0)
    declared_weight_kg: float | None = Field(default=None, gt=0)
    declared_value: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def at_least_one_field(self) -> PackageUpdateItem:
        provided = {k for k in ("length_cm", "width_cm", "height_cm", "declared_weight_kg", "declared_value") if getattr(self, k) is not None}
        if not provided:
            raise ValueError("Provide at least one field to update on the package")
        return self


class UpdateStopPackagesRequest(BaseSchema):
    packages: list[PackageUpdateItem] = Field(..., min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self) -> UpdateStopPackagesRequest:
        ids = [p.id for p in self.packages]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate package id in update payload")
        return self


class UpdateStopPackagesResponse(BaseSchema):
    order_id: str
    delivery_stop_id: str
    tracking_id: str | None = None
    service_tier: str | None = Field(
        default=None,
        description="Human label from the matched pricing_plans entry (plain_name) on the stop.",
    )
    service_tier_id: str | None = Field(
        default=None,
        description="Null on the stop row; matched plan id stays in the stop's price_breakdown.pricing_plan.id_price_tier.",
    )
    packages: list[PackageEntry] = Field(default_factory=list)
    stop_price_breakdown: dict | None = None
    order_subtotal: Decimal = Decimal("0")
    order_vat_amount: Decimal = Decimal("0")
    order_total_amount: Decimal = Decimal("0")
    order_price_breakdown: dict | None = None


class UpdateStopPreferencesRequest(BaseSchema):
    signature_required: bool | None = None
    safe_place_allowed: bool | None = None

    @model_validator(mode="after")
    def at_least_one(self) -> Self:
        if self.signature_required is None and self.safe_place_allowed is None:
            raise ValueError("Provide signature_required and/or safe_place_allowed")
        return self


class UpdateStopServiceTierRequest(BaseSchema):
    service_tier_id: str = Field(..., min_length=1)


class UpdateStopDetailsRequest(BaseSchema):
    recipient_first_name: str | None = Field(default=None, min_length=1, max_length=255)
    recipient_last_name: str | None = Field(default=None, min_length=1, max_length=255)
    recipient_phone: str | None = Field(default=None, min_length=1, max_length=50)
    recipient_email: EmailStr | None = None
    line_1: str | None = Field(default=None, min_length=1, max_length=255)
    line_2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    postcode: str | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def at_least_one(self) -> Self:
        provided = {
            k
            for k in (
                "recipient_first_name",
                "recipient_last_name",
                "recipient_phone",
                "recipient_email",
                "line_1",
                "line_2",
                "city",
                "postcode",
            )
            if getattr(self, k) is not None
        }
        if not provided:
            raise ValueError("Provide at least one field to update on the delivery stop")
        return self
