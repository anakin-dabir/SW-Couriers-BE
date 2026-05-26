from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import structlog
from fastapi import UploadFile
from fastapi.requests import Request
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import AuthUser
from app.common.enums import ClientType, Job, LogEvent, UserRole, UserStatus
from app.common.enums.permission import PermissionLevel, Resource
from app.common.exceptions import AuthenticationError, ConflictError, ForbiddenError, InvalidStateTransitionError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.common.utils import build_email_link
from app.core.config import settings
from app.core.queue import QueuePriority, enqueue
from app.core.security import hash_password
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.auth.service import AuthService
from app.modules.delivery_attempts.domain import compact_attempt_fees, default_fee_entries
from app.modules.delivery_attempts.repository import DeliveryAttemptConfigRepository
from app.modules.notifications.enums import NotificationType
from app.modules.notifications.repository import OrgNotificationPreferenceRepository
from app.modules.org_credit_suspension.repository import OrgCreditConfigRepository, OrgSuspensionConfigRepository
from app.modules.org_credit_suspension.v1.schemas import (
    OrgCreditConfigResponse,
    OrgSuspensionConfigResponse,
    _serialize_triggers,
)
from app.modules.org_discounts.enums import DiscountType
from app.modules.org_discounts.repository import OrgDiscountConfigRepository
from app.modules.org_discounts.v1.schemas import (
    OrgDiscountConfigInput,
    OrgDiscountConfigItemResponse,
    OrgDiscountConfigResponse,
    OrgDiscountConfigUpsert,
    _serialize_volume_tiers,
)
from app.modules.organizations.access import assert_caller_org_scope, assert_org_profile_access, is_platform_admin_role
from app.modules.organizations.enums import (
    ContactRole,
    ContactStatus,
    OrganizationStatus,
    OrgDocumentActivityType,
    OrgDocumentCategory,
    OrgDocumentShareStatus,
    OrgDocumentStatus,
    OrgDocumentType,
    PaymentModel,
)
from app.modules.organizations.models import Organization, OrgDocumentShare, OrgDraft, OrgPaymentConfig, OrgPaymentMethod
from app.modules.organizations.repository import (
    OrganizationRepository,
    OrgContactRepository,
    OrgDocumentActivityRepository,
    OrgDocumentRepository,
    OrgDocumentShareRepository,
    OrgDraftRepository,
    OrgListRow,
    OrgPaymentConfigRepository,
    OrgPaymentMethodRepository,
)
from app.modules.organizations.v1.schemas import (
    AccountManagerResponse,
    AssignAccountManagerRequest,
    BookingServiceTierItemResponse,
    BookingServiceTiersResponse,
    ContactCreatedEntry,
    ContactPermission,
    ContractUploadResponse,
    CreateOrgWithContactsRequest,
    CreateOrgWithContactsResponse,
    OrgAccountManagerResponse,
    OrganizationListItemResponse,
    OrganizationResponse,
    OrganizationStatusChange,
    OrganizationUpdate,
    OrganizationUpdateResponse,
    OrgContactCreate,
    OrgContactDetailResponse,
    OrgContactListResponse,
    OrgContactUpdate,
    OrgDocumentActivityResponse,
    OrgDocumentOperationsRequest,
    OrgDocumentResponse,
    OrgDocumentShareCreate,
    OrgDocumentShareExtendExpiry,
    OrgDocumentShareResponse,
    OrgDocumentShareRevoke,
    OrgDocumentUpdate,
    OrgDraftContactInput,
    OrgDraftCreateRequest,
    OrgDraftListItem,
    OrgDraftPublishRequest,
    OrgDraftResponse,
    OrgPaymentConfigCreate,
    OrgPaymentConfigResponse,
    OrgPaymentConfigUpdate,
    OrgPaymentConfigUpdateEmbedded,
    OrgPaymentDetailsResponse,
    OrgPaymentMethodCreate,
    OrgPaymentMethodResponse,
    OrgProfileSavePayload,
    OrgSelfUpdate,
    PaymentMethodStats,
    ProfileCompletionItem,
    ProfileCompletionResponse,
    RegisteredAddressSchema,
    SharedDocumentAccessResponse,
    SharedDocumentInfoResponse,
    TradingAddressSchema,
)
from app.modules.permission.service import PermissionService
from app.modules.pickup_addresses.repository import PickupAddressRepository
from app.modules.pickup_addresses.service import PickupAddressService
from app.modules.pickup_addresses.types import PickupAddressOwner
from app.modules.pickup_addresses.v1.schemas import CreatePickupAddressesRequest, PickupAddressResponse
from app.modules.organizations.superfast_tier import (
    ensure_superfast_in_pricing_plans,
    reject_superfast_deselect,
    validate_superfast_plan_constraints,
)
from app.modules.service_tiers.enums import ServiceTierScopeType, ServiceTierStatus
from app.modules.service_tiers.repository import ServiceTierRepository
from app.modules.user.models import User
from app.modules.user.repository import UserRepository
from app.storage.upload import (
    ALLOWED_ORG_DOCUMENT_TYPES,
    MAX_ORG_DOCUMENT_SIZE,
    delete_from_r2,
    delete_image,
    generate_document_url,
    generate_image_url,
    read_and_validate,
    upload_image,
    upload_to_r2,
)

_CONTRACT_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
DOC_ACTIVITY_EXPORT_MAX_ROWS = 50_000
DOC_ACTIVITY_EXPORT_DEFAULT_DAYS = 90

logger = structlog.get_logger()


def _resolve_doc_activity_export_range(
    date_from: datetime | None,
    date_to: datetime | None,
) -> tuple[datetime, datetime]:
    """Default to the last N days when both export bounds are omitted."""
    now = datetime.now(UTC)
    if date_from is None and date_to is None:
        return now - timedelta(days=DOC_ACTIVITY_EXPORT_DEFAULT_DAYS), now
    resolved_to = date_to if date_to is not None else now
    resolved_from = (
        date_from if date_from is not None else resolved_to - timedelta(days=DOC_ACTIVITY_EXPORT_DEFAULT_DAYS)
    )
    return resolved_from, resolved_to


def _b2b_invite_email_link(raw_token: str) -> str:
    link = build_email_link(ClientType.CUSTOMER_B2B, "accept-invite", raw_token)
    if link:
        return link
    base = (settings.VERIFICATION_LINK_BASE_URL).rstrip("/")
    return f"{base}/accept-invite?token={raw_token}"


# Valid status transitions: {current -> set of allowed targets}
_ALLOWED_TRANSITIONS: dict[OrganizationStatus, set[OrganizationStatus]] = {
    OrganizationStatus.ACTIVE: {OrganizationStatus.ON_HOLD, OrganizationStatus.SUSPENDED, OrganizationStatus.INACTIVE},
    OrganizationStatus.ON_HOLD: {OrganizationStatus.ACTIVE, OrganizationStatus.SUSPENDED, OrganizationStatus.INACTIVE},
    OrganizationStatus.SUSPENDED: {OrganizationStatus.ACTIVE, OrganizationStatus.INACTIVE},
    OrganizationStatus.INACTIVE: {OrganizationStatus.ACTIVE},
}


def _build_registered_address_full(org) -> str | None:
    """Concatenate address fields into a single display line."""
    parts = [
        org.reg_address_line_1,
        org.reg_address_line_2,
        org.reg_city,
        org.reg_state,
        org.reg_postcode,
        org.reg_country,
    ]
    return ", ".join(p for p in parts if p)


def _pricing_type_from_plans(pricing_plans) -> str | None:
    """Return the plain_type of the selected plan, falling back to the first plan."""
    if not pricing_plans:
        return None
    selected = next((p for p in pricing_plans if p.get("selected")), None)
    plan = selected or pricing_plans[0]
    return plan.get("plain_type")


async def _validate_and_enrich_pricing_plans(
    plans: list,
    tier_repo,
) -> list[dict]:
    """Validate each plan's tier ID exists, auto-populate base_price from the ServiceTier,
    and return a JSON-safe list of dicts ready for storage.

    For standard plans  — price_per_package is overwritten with the tier's global price.
    For custom plans    — price_per_package is kept as-is; base_price records the global price.
    """
    result = []
    for plan in plans:
        tier_id = plan.id_price_tier if hasattr(plan, "id_price_tier") else plan["id_price_tier"]
        tier = await tier_repo.get_by_id(tier_id)
        if tier is None:
            raise ValidationError(f"Pricing tier '{tier_id}' does not exist.")
        if str(tier.scope_type) != ServiceTierScopeType.GLOBAL.value or tier.scope_org_id is not None:
            raise ValidationError(f"Pricing tier '{tier_id}' must reference a GLOBAL service tier.")
        if str(tier.status) != ServiceTierStatus.ACTIVE.value:
            raise ValidationError(f"Pricing tier '{tier_id}' is not active.")

        # Build a mutable JSON-safe dict from either Pydantic model or raw dict
        if hasattr(plan, "model_dump"):
            entry = plan.model_dump(mode="json")
        else:
            entry = {k: str(v) if hasattr(v, "__round__") and not isinstance(v, (int, float, bool)) else v for k, v in plan.items()}

        reference = (tier.base_price + tier.price_per_package).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        tier_price = str(reference)

        if entry.get("plain_type") == "custom":
            client_base = entry.get("base_price")
            if client_base is not None and str(client_base).strip() != "":
                entry["base_price"] = str(
                    Decimal(str(client_base)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                )
            else:
                entry["base_price"] = str(
                    tier.base_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                )
        else:
            # Snapshot of the tier's reference list price (base + per-package; excludes per-kg)
            entry["base_price"] = tier_price
            entry["price_per_package"] = tier_price

        result.append(entry)

    superfast = await tier_repo.find_global_superfast()
    if superfast is None:
        raise ValidationError("System tier Superfast is not configured.")
    reject_superfast_deselect(result, superfast_id=str(superfast.id))
    result = ensure_superfast_in_pricing_plans(result, superfast)
    validate_superfast_plan_constraints(result, superfast_id=str(superfast.id))
    return result


def _to_list_item(row: OrgListRow) -> OrganizationListItemResponse:
    """Convert an OrgListRow dataclass to OrganizationListItemResponse."""
    org = row.org
    onboarded_by: str | None = None
    if row.onboarded_by_first_name or row.onboarded_by_last_name:
        onboarded_by = f"{row.onboarded_by_first_name or ''} {row.onboarded_by_last_name or ''}".strip()

    account_manager: str | None = None
    if row.account_manager_first_name or row.account_manager_last_name:
        account_manager = f"{row.account_manager_first_name or ''} {row.account_manager_last_name or ''}".strip()

    secondary_account_manager: str | None = None
    if row.secondary_account_manager_first_name or row.secondary_account_manager_last_name:
        secondary_account_manager = f"{row.secondary_account_manager_first_name or ''} {row.secondary_account_manager_last_name or ''}".strip()

    additional_account_manager: str | None = None
    if row.additional_account_manager_first_name or row.additional_account_manager_last_name:
        additional_account_manager = f"{row.additional_account_manager_first_name or ''} {row.additional_account_manager_last_name or ''}".strip()

    # Parse CSV of payment model strings → list[PaymentModel]
    payment_models: list[PaymentModel] | None = None
    if row.payment_models_csv:
        payment_models = [PaymentModel(m) for m in row.payment_models_csv.split(",") if m]

    return OrganizationListItemResponse(
        id=org.id,
        reference=org.reference,
        trading_name=org.trading_name,
        legal_entity_name=org.legal_entity_name,
        industry=org.industry,
        company_size=org.company_size,
        status=org.status,
        vat_number=org.vat_number,
        is_vat_registered=bool(org.vat_number and org.vat_number.strip()),
        registered_address_full=_build_registered_address_full(org),
        pricing_type=_pricing_type_from_plans(org.pricing_plans),
        payment_models=payment_models,
        credit_limit=row.credit_limit,
        owner_account_email=row.owner_account_email,
        onboarded_by=onboarded_by,
        onboarded_by_role=row.onboarded_by_role,
        account_manager=account_manager,
        account_manager_role=row.account_manager_role,
        secondary_account_manager=secondary_account_manager,
        secondary_account_manager_role=row.secondary_account_manager_role,
        additional_account_manager=additional_account_manager,
        additional_account_manager_role=row.additional_account_manager_role,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


def _flatten_address(
    org_data: dict,
    registered_address: RegisteredAddressSchema | None,
    trading_address: TradingAddressSchema | None = None,
) -> dict:
    """Replace nested address objects with flat columns."""
    org_data.pop("registered_address", None)
    org_data.pop("trading_address", None)

    if registered_address is not None:
        org_data["reg_address_line_1"] = registered_address.address_line_1
        org_data["reg_address_line_2"] = registered_address.address_line_2
        org_data["reg_city"] = registered_address.city
        org_data["reg_state"] = registered_address.state
        org_data["reg_postcode"] = registered_address.postcode
        org_data["reg_country"] = registered_address.country

    if trading_address is not None:
        org_data["trading_address_line_1"] = trading_address.address_line_1
        org_data["trading_address_line_2"] = trading_address.address_line_2
        org_data["trading_address_city"] = trading_address.city
        org_data["trading_address_state"] = trading_address.state
        org_data["trading_address_postcode"] = trading_address.postcode
        org_data["trading_address_country"] = trading_address.country

    return org_data


def _registered_address_from_org(org: Organization) -> RegisteredAddressSchema | None:
    """Build registered address schema from persisted org columns (for trading_same_as_registered)."""
    line1 = org.reg_address_line_1 or ""
    if not line1.strip():
        return None
    city = org.reg_city or ""
    if not city.strip():
        return None
    postcode = org.reg_postcode or ""
    if not postcode.strip():
        return None
    line2 = org.reg_address_line_2
    if line2 is not None:
        line2 = line2.strip() or None
    state = org.reg_state
    if state is not None:
        state = state.strip() or None
    return RegisteredAddressSchema(
        address_line_1=line1.strip(),
        address_line_2=line2,
        city=city.strip(),
        state=state,
        postcode=postcode.strip(),
        country=(org.reg_country or "United Kingdom").strip() or "United Kingdom",
    )


class OrganizationService(BaseService):
    """Business logic for Organization management."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._org_repo = OrganizationRepository(session)
        self._contact_repo = OrgContactRepository(session)
        self._config_repo = OrgPaymentConfigRepository(session)
        self._payment_method_repo = OrgPaymentMethodRepository(session)
        self._credit_config_repo = OrgCreditConfigRepository(session)
        self._suspension_config_repo = OrgSuspensionConfigRepository(session)
        self._discount_config_repo = OrgDiscountConfigRepository(session)
        self._user_repo = UserRepository(session)
        self._pricing_tier_repo = ServiceTierRepository(session)
        self._auth_service = AuthService(session, request=request)
        self._perm_service = PermissionService(session, request=request)
        self._audit = AuditService(session, request=request)

    # ── Create ────────────────────────────────────────────────────────────────

    async def create_org_with_contacts(
        self,
        request: CreateOrgWithContactsRequest,
        inviter: AuthUser,
        contract_file: UploadFile | None = None,
        contract_title: str | None = None,
        contract_expiry_date: date | None = None,
        logo_file: UploadFile | None = None,
    ) -> CreateOrgWithContactsResponse:
        """Create an organization and one or more contacts in one flow.

        For each contact:
        - Creates a User (CUSTOMER_B2B, status=pending_verification)
        - Creates an OrgContact row linked to that user
        - Generates a 1-day invite token and enqueues the invite email

        If contract_file is provided it is saved locally (dev) or to R2 (prod)
        and the path/URL is stored in contract_reference.
        """
        # Check all emails are unique (against existing users)
        for contact in request.contacts:
            email = contact.email.strip().lower()
            if await self._user_repo.email_exists(email):
                raise ConflictError(f"User with email '{email}' already exists.")

        # Build org data dict — flatten nested address object
        org_data = request.organization.model_dump(exclude={"registered_address", "pricing_plans"})
        # Validate tier IDs, auto-populate base_price, enforce standard price, ensure Superfast.
        raw_plans = list(request.organization.pricing_plans or [])
        org_data["pricing_plans"] = await _validate_and_enrich_pricing_plans(raw_plans, self._pricing_tier_repo)
        org_data["reference"] = await self._org_repo.generate_reference()
        org_data["onboarded_by_user_id"] = inviter.id
        # account_manager_user_id is passed through from OrganizationCreate if provided
        org_data = _flatten_address(org_data, request.organization.registered_address, request.organization.trading_address)

        org = await self._org_repo.create(org_data)

        from app.modules.organizations.pricing_plans_contract_sync import replace_org_contract_from_pricing_plans

        await replace_org_contract_from_pricing_plans(
            self._session,
            organization_id=org.id,
            plans=list(org_data["pricing_plans"]),
        )

        # Upload contract PDF to R2 if provided at creation time
        contract_url: str | None = None
        if contract_file is not None and contract_file.filename:
            contract_result = await self.upload_contract(
                org.id,
                contract_file,
                inviter.id,
                title=contract_title,
                expiry_date=contract_expiry_date,
            )
            org.contract_reference = contract_result.contract_reference
            contract_url = contract_result.contract_url

        # Upload logo to Cloudflare Images if provided at creation time
        if logo_file is not None and logo_file.filename:
            content, _ = await read_and_validate(
                logo_file,
                allowed_types={"image/jpeg", "image/png"},
                max_size=10 * 1024 * 1024,
                label="Logo",
            )
            result = await upload_image(
                content,
                filename=logo_file.filename,
                metadata={"kind": "org_logo", "org_id": org.id},
            )
            await self._org_repo.update_by_id(org.id, {"logo_cf_image_id": result.id})
            org.logo_cf_image_id = result.id

        dummy_password = hash_password("INVITED_USER_PLACEHOLDER")

        created_contacts: list[ContactCreatedEntry] = []
        primary_owner_assigned = False

        for contact in request.contacts:
            email = contact.email.strip().lower()

            # Create User
            user = await self._user_repo.create(
                {
                    "email": email,
                    "first_name": contact.first_name,
                    "last_name": contact.last_name,
                    "role": UserRole.CUSTOMER_B2B,
                    "status": UserStatus.PENDING_VERIFICATION,
                    "organization_id": org.id,
                    "password_hash": dummy_password,
                }
            )

            is_primary_owner = contact.contact_role == ContactRole.ACCOUNT_OWNER and not primary_owner_assigned
            if is_primary_owner:
                primary_owner_assigned = True

            # Create OrgContact row — identity (name/email) sourced from user row
            org_contact = await self._contact_repo.create(
                {
                    "organization_id": org.id,
                    "contact_number": contact.contact_number,
                    "contact_role": contact.contact_role,
                    "status": ContactStatus.PENDING,
                    "is_primary": is_primary_owner,
                    "user_id": user.id,
                }
            )
            await _apply_permission_overrides(
                self._perm_service,
                user.id,
                inviter.id,
                contact.permissions,
                contact_role=contact.contact_role,
            )

            # Create invite (1-day expiry)
            ir = await self._auth_service.create_invite(
                inviter,
                user.id,
                expires_days=1,
                organization_id=org.id,
            )
            if not ir.throttled:
                invite_link = _b2b_invite_email_link(ir.raw_token or "")
                await enqueue(
                    Job.SEND_INVITE_EMAIL,
                    invite_id=ir.public_invite_id,
                    to_email=email,
                    first_name=getattr(user, "first_name", None) or email,
                    invite_link=invite_link,
                    expires_days=1,
                    priority=QueuePriority.DEFAULT,
                )

            created_contacts.append(
                ContactCreatedEntry(
                    contact_id=org_contact.id,
                    user_id=user.id,
                    email=email,
                    contact_role=contact.contact_role,
                    invite_token=ir.raw_token or "",
                )
            )

        await self._audit.log(
            action="organization.created",
            entity_type="organization",
            entity_id=org.id,
            user_id=inviter.id,
            new_value={
                "contacts": [{"email": c.email, "role": c.contact_role} for c in created_contacts],
            },
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_CREATED,
            severity="NOTICE",
            organization_id=org.id,
            entity_ref=org.reference,
            user_role=inviter.role,
        )

        logger.info(
            LogEvent.ORGANIZATION_CREATED,
            org_id=org.id,
            trading_name=org.trading_name,
            reference=org.reference,
            contact_count=len(created_contacts),
            admin_user_id=inviter.id,
        )

        # Optionally create payment config (shared settings + methods) in the same transaction
        payment_config_response = None
        if request.payment_config is not None:
            payment_config_response = await OrgPaymentConfigService(self._session, self._request).create_payment_config(
                org_id=org.id,
                data=request.payment_config,
                admin_user_id=inviter.id,
            )

        # Optionally create credit config in the same transaction
        credit_config_response = None
        if request.credit_config is not None:
            cc_data = request.credit_config.model_dump()
            cc_data["organization_id"] = org.id
            credit_config = await self._credit_config_repo.create(cc_data)
            credit_config_response = OrgCreditConfigResponse.model_validate(credit_config)

        # Optionally create suspension config in the same transaction
        suspension_config_response = None
        if request.suspension_config is not None:
            sc_data = request.suspension_config.model_dump(exclude={"trigger_conditions"})
            sc_data["organization_id"] = org.id
            sc_data["trigger_conditions"] = _serialize_triggers(request.suspension_config.trigger_conditions)
            suspension_config = await self._suspension_config_repo.create(sc_data)
            suspension_config_response = OrgSuspensionConfigResponse.model_validate(suspension_config)

        # Optionally create discount config in the same transaction
        discount_config_response = None
        if request.discount_config is not None:
            discount_config_response = await OrgDiscountConfigService(self._session, self._request).create_discount_config(
                org_id=org.id,
                data=request.discount_config,
                admin_user_id=inviter.id,
            )

        # Optionally create pickup addresses in the same transaction
        pickup_address_responses: list[PickupAddressResponse] | None = None
        if request.pickup_addresses:
            pickup_svc = PickupAddressService(self._session, self._request)
            pickup_address_responses = await pickup_svc.create_addresses_for_organization(
                PickupAddressOwner(organization_id=org.id),
                CreatePickupAddressesRequest(request.pickup_addresses),
                actor_user_id=inviter.id,
            )

        org_response = OrganizationResponse.model_validate(org)
        org_response.logo_url = self.get_logo_url(org.logo_cf_image_id)

        return CreateOrgWithContactsResponse(
            organization=org_response,
            contacts=created_contacts,
            payment_config=payment_config_response,
            credit_config=credit_config_response,
            suspension_config=suspension_config_response,
            discount_config=discount_config_response,
            pickup_addresses=pickup_address_responses,
            contract_url=contract_url,
            contract_url_expires_in_seconds=3600 if contract_url else None,
            message=f"Organization created with {len(created_contacts)} contact(s). Invite(s) sent.",
        )

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_organization(
        self,
        org_id: str,
        caller_role: str | None = None,
        caller_contact_role: ContactRole | None = None,
    ) -> OrganizationResponse:
        """Return org details.

        When called by a CUSTOMER_B2B, caller_contact_role must be non-None
        (meaning they have an active org_contact row for this org). None means
        they are not a member — raise ForbiddenError.
        """
        if caller_role is not None and not is_platform_admin_role(caller_role) and caller_contact_role is None:
            raise ForbiddenError("You do not have access to this organisation.")
        org = await self._org_repo.get_by_id_or_404(org_id)
        response = OrganizationResponse.model_validate(org)
        response.logo_url = self.get_logo_url(org.logo_cf_image_id)
        if org.contract_reference:
            try:
                response.contract_url = generate_document_url(org.contract_reference, expiry_seconds=3600)
            except Exception:
                logger.warning(
                    "organization.contract_url_generation_failed",
                    org_id=org_id,
                    key=org.contract_reference,
                    exc_info=True,
                )
        if org.onboarded_by_user_id:
            onboarded_by_user = await self._user_repo.get_by_id(org.onboarded_by_user_id)
            if onboarded_by_user:
                name_parts = [onboarded_by_user.first_name or "", onboarded_by_user.last_name or ""]
                response.onboarded_by = " ".join(p for p in name_parts if p).strip() or None
                response.onboarded_by_role = onboarded_by_user.role

        def _manager_name(u: object) -> str | None:
            parts = [(getattr(u, "first_name", None) or ""), (getattr(u, "last_name", None) or "")]
            return " ".join(p for p in parts if p).strip() or None

        am_ids = [
            uid
            for uid in (
                org.account_manager_user_id,
                org.secondary_account_manager_user_id,
                org.additional_account_manager_user_id,
            )
            if uid
        ]
        if am_ids:
            am_users = await self._user_repo.get_by_ids(am_ids)
            if org.account_manager_user_id and org.account_manager_user_id in am_users:
                u = am_users[org.account_manager_user_id]
                response.account_manager_name = _manager_name(u)
                response.account_manager_email = u.email
            if org.secondary_account_manager_user_id and org.secondary_account_manager_user_id in am_users:
                u = am_users[org.secondary_account_manager_user_id]
                response.secondary_account_manager_name = _manager_name(u)
                response.secondary_account_manager_email = u.email
            if org.additional_account_manager_user_id and org.additional_account_manager_user_id in am_users:
                u = am_users[org.additional_account_manager_user_id]
                response.additional_account_manager_name = _manager_name(u)
                response.additional_account_manager_email = u.email

        return response

    async def get_booking_service_tiers(
        self,
        org_id: str,
        *,
        available_for: str,
        caller: AuthUser,
        caller_contact_role: ContactRole | None,
    ) -> BookingServiceTiersResponse:
        """Permitted + resolved tier rows for the booking UI (contract or global fallback)."""
        await assert_org_profile_access(self._session, caller, org_id, caller_contact_role, PermissionLevel.READ)
        from app.modules.service_tiers.booking_tiers import BookingServiceTierResolver

        resolver = BookingServiceTierResolver(self._session)
        rows, from_contract = await resolver.list_booking_tiers(
            organization_id=org_id,
            available_for=available_for,
        )
        items = [
            BookingServiceTierItemResponse(
                id=r.id,
                global_template_id=r.global_template_id,
                org_tier_id=r.org_tier_id,
                mode=r.mode,
                is_default=r.is_default,
                tier_name=r.tier_name,
                description=r.description,
                duration_days=r.duration_days,
                error_margin_kg=r.error_margin_kg,
                price_per_kg=str(r.price_per_kg),
                price_per_package=str(r.price_per_package),
                base_price=str(r.base_price),
                available_for=r.available_for,
                color=r.color,
                icon=r.icon,
                source=r.source,
            )
            for r in rows
        ]
        return BookingServiceTiersResponse(
            items=items,
            resolution_source="contract" if from_contract else "global_fallback",
        )

    @staticmethod
    def _payment_config_complete_for_profile(pc: OrgPaymentConfig | None) -> bool:
        """Payment config is complete when the shared config row exists."""
        return pc is not None

    async def get_profile_completion(self, org_id: str, caller: AuthUser) -> ProfileCompletionResponse:
        """Compute onboarding checklist for the B2B profile widget (weights sum to 100)."""
        org = await self._org_repo.get_by_id_or_404(org_id)
        user = await self._user_repo.get_by_id_or_404(caller.id)

        my_contact = await self._contact_repo.get_active_contact_for_user(org_id, caller.id)
        setup_done = my_contact is not None and my_contact.status == ContactStatus.ACTIVE
        setup_missing = [] if setup_done else ["active_org_contact"]

        logo_done = bool(org.logo_cf_image_id)
        logo_missing = [] if logo_done else ["company_logo"]

        company_missing: list[str] = []
        if not (org.trading_name or "").strip():
            company_missing.append("trading_name")
        if not (org.legal_entity_name or "").strip():
            company_missing.append("legal_entity_name")
        if not (org.companies_house_number or "").strip():
            company_missing.append("companies_house_number")
        if not (org.eori_number or "").strip():
            company_missing.append("eori_number")
        if not (org.vat_number or "").strip():
            company_missing.append("vat_number")
        if not (org.reg_address_line_1 or "").strip():
            company_missing.append("registered_address.address_line_1")
        if not (org.reg_city or "").strip():
            company_missing.append("registered_address.city")
        if not (org.reg_postcode or "").strip():
            company_missing.append("registered_address.postcode")
        company_done = len(company_missing) == 0

        contacts = await self._contact_repo.list_with_user(org_id)
        primary = next((c for c in contacts if c.is_primary), None)
        if primary is None:
            primary = next((c for c in contacts if c.contact_role == ContactRole.ACCOUNT_OWNER and c.user), None)
        primary_missing: list[str] = []
        primary_done = False
        if primary and primary.user:
            u = primary.user
            phone_ok = bool((primary.contact_number or "").strip()) or bool((u.phone or "").strip())
            name_ok = bool((u.first_name or "").strip() and (u.last_name or "").strip())
            primary_done = phone_ok and name_ok
            if not name_ok:
                primary_missing.extend(["primary_contact.first_name", "primary_contact.last_name"])
            if not phone_ok:
                primary_missing.append("primary_contact.phone")
        else:
            primary_missing.append("primary_contact")

        security_done = not user.force_password_change
        security_missing = [] if security_done else ["password_update_required"]

        recipient_pref_rows = await OrgNotificationPreferenceRepository(self._session).get_for_organization(org_id, notification_type=NotificationType.RECIPIENT)
        notify_done = len(recipient_pref_rows) > 0
        notify_missing = [] if notify_done else ["recipient_notification_preferences"]

        pc = await self._config_repo.get_by_organization(org_id)
        billing_done = self._payment_config_complete_for_profile(pc)
        billing_missing = [] if billing_done else ["payment_configuration"]

        pickup_owner = PickupAddressOwner(organization_id=org_id)
        pickup_addresses = await PickupAddressService(self._session, request=self._request).list_for_organization(pickup_owner)
        pickup_done = any(a.is_default for a in pickup_addresses)
        pickup_missing = [] if pickup_done else ["default_pickup_address"]

        items = [
            ProfileCompletionItem(
                key="setup_account",
                label="Setup Account",
                weight=10,
                completed=setup_done,
                missing_fields=setup_missing,
                hint="Activate at least one organization contact account.",
            ),
            ProfileCompletionItem(
                key="company_logo",
                label="Upload Company Logo",
                weight=5,
                completed=logo_done,
                missing_fields=logo_missing,
                hint="Upload a JPEG/PNG logo.",
            ),
            ProfileCompletionItem(
                key="company_information",
                label="Company Information (Name, Address, VAT, EORI)",
                weight=15,
                completed=company_done,
                missing_fields=company_missing,
                hint="Complete legal details, EORI, VAT, and registered address.",
            ),
            ProfileCompletionItem(
                key="primary_contact_info",
                label="Primary Contact Info",
                weight=10,
                completed=primary_done,
                missing_fields=primary_missing,
                hint="Primary contact must have full name and phone number.",
            ),
            ProfileCompletionItem(
                key="security_setup",
                label="Security Setup (Password)",
                weight=20,
                completed=security_done,
                missing_fields=security_missing,
                hint="Complete initial password setup.",
            ),
            ProfileCompletionItem(
                key="receiver_notifications",
                label="Receiver Notification Preference",
                weight=10,
                completed=notify_done,
                missing_fields=notify_missing,
                hint="Save at least one receiver notification preference.",
            ),
            ProfileCompletionItem(
                key="billing_details",
                label="Billing Details / Bank Info",
                weight=20,
                completed=billing_done,
                missing_fields=billing_missing,
                hint="Configure payment details for the organization.",
            ),
            ProfileCompletionItem(
                key="pickup_addresses",
                label="Pickup Addresses",
                weight=10,
                completed=pickup_done,
                missing_fields=pickup_missing,
                hint="Add at least one default pickup address.",
            ),
        ]
        completed_weight = sum(i.weight for i in items if i.completed)
        total_weight = sum(i.weight for i in items)
        percent = int((completed_weight * 100) / total_weight) if total_weight else 0
        return ProfileCompletionResponse(
            percent_complete=min(100, percent),
            completed_weight=completed_weight,
            total_weight=total_weight,
            items=items,
        )

    async def get_org_stats(self) -> dict[str, int]:
        """Get B2B client organization statistics.

        Returns counts for: total, active, pending_activation, inactive, suspended.

        pending_activation = organizations with status=ACTIVE but with PENDING contacts.
        """
        from sqlalchemy import and_

        from app.modules.organizations.models import OrgContact

        # Get all organizations grouped by status
        stmt = select(
            func.count().label("total"),
            func.count().filter(Organization.status == OrganizationStatus.ACTIVE).label("active_count"),
            func.count().filter(Organization.status == OrganizationStatus.INACTIVE).label("inactive"),
            func.count().filter(Organization.status == OrganizationStatus.SUSPENDED).label("suspended"),
        )

        row = (await self._session.execute(stmt)).one()
        total = row.total
        active_count = row.active_count
        inactive = row.inactive
        suspended = row.suspended

        # For pending_activation: count organizations with status=ACTIVE but with PENDING contacts
        pending_stmt = select(func.count(func.distinct(Organization.id))).where(
            and_(
                Organization.status == OrganizationStatus.ACTIVE,
                Organization.id.in_(select(OrgContact.organization_id).where(OrgContact.status == ContactStatus.PENDING)),
            )
        )

        pending_row = (await self._session.execute(pending_stmt)).scalar_one()
        pending_activation = pending_row

        return {
            "total": total,
            "active": active_count - pending_activation,
            "pending_activation": pending_activation,
            "inactive": inactive,
            "suspended": suspended,
        }

    async def get_payment_details(
        self,
        org_id: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> OrgPaymentDetailsResponse:
        """Aggregate financial and activity stats for the organization payment dashboard."""
        from app.modules.invoices.models import Invoice
        from app.modules.orders.models import Order

        # 1. Fetch Orders stats (Total Charged, Total Orders, Success/Failure)
        bookings_filter = [Order.organization_id == org_id, Order.status != "draft"]
        if start_date:
            bookings_filter.append(Order.created_at >= datetime.combine(start_date, datetime.min.time()).replace(tzinfo=UTC))
        if end_date:
            bookings_filter.append(Order.created_at < datetime.combine(end_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=UTC))

        bookings_stmt = select(
            func.count(Order.id).label("total_orders"),
            func.sum(Order.total_amount).label("total_charged"),
            # Order has no `payment_status` column. For card orders, treat
            # presence of a captured transaction id as successful payment.
            func.count(Order.id)
            .filter(
                and_(
                    Order.payment_method == PaymentModel.CARD,
                    Order.braintree_transaction_id.is_not(None),
                )
            )
            .label("successful"),
            # Card orders without a transaction id are treated as failed/unfinished.
            func.count(Order.id)
            .filter(
                and_(
                    Order.payment_method == PaymentModel.CARD,
                    Order.braintree_transaction_id.is_(None),
                )
            )
            .label("failed"),
        ).where(*bookings_filter)

        res = await self._session.execute(bookings_stmt)
        b_stats = res.one()

        total_charged = b_stats.total_charged or Decimal("0.00")
        total_orders = b_stats.total_orders or 0
        successful = b_stats.successful or 0
        failed = b_stats.failed or 0

        success_rate = 0.0
        if (successful + failed) > 0:
            success_rate = (successful / (successful + failed)) * 100

        # 2. Fetch Invoices stats
        invoices_filter = [Invoice.organization_id == org_id, Invoice.status == "SENT"]
        if start_date:
            invoices_filter.append(Invoice.issue_date >= start_date)
        if end_date:
            invoices_filter.append(Invoice.issue_date <= end_date)

        invoices_stmt = select(
            func.count(Invoice.id).label("invoice_count"),
            func.sum(Invoice.total).label("total_invoiced"),
            func.sum(Invoice.paid_amount).label("paid_amount"),
            func.sum(Invoice.total - Invoice.paid_amount).filter(Invoice.payment_status == "OVERDUE").label("overdue_amount"),
        ).where(*invoices_filter)

        i_res = await self._session.execute(invoices_stmt)
        i_stats = i_res.one()

        total_invoiced = i_stats.total_invoiced or Decimal("0.00")
        paid_amount = i_stats.paid_amount or Decimal("0.00")
        unpaid_amount = total_invoiced - paid_amount
        overdue_amount = i_stats.overdue_amount or Decimal("0.00")
        invoice_count = int(i_stats.invoice_count or 0)

        # 3. Credit Config & Utilization
        credit_config = await self._credit_config_repo.get_by_org(org_id)
        credit_limit = credit_config.approved_credit_limit if credit_config else None

        used_credit = None
        available_credit = None
        utilization = None

        if credit_limit is not None:
            # We use unpaid Sent invoices as the current 'used credit'
            used_credit = unpaid_amount
            available_credit = max(Decimal("0.00"), credit_limit - used_credit)
            if credit_limit > 0:
                utilization = float(used_credit / credit_limit) * 100

        # 4. Method distribution — `orders.payment_method_id` → `org_payment_methods`
        # (canonical payment model for the org). Fall back to `orders.payment_method`
        # when the FK is unset (older rows). Vaulted Braintree cards use a separate
        # app `payment_methods` table and are not used for this breakdown.
        payment_model_bucket = func.coalesce(
            OrgPaymentMethod.payment_model,
            Order.payment_method,
        ).label("payment_model")
        dist_stmt = (
            select(
                payment_model_bucket,
                func.count(Order.id).label("order_count"),
                func.sum(Order.total_amount).label("total_charged"),
            )
            .outerjoin(
                OrgPaymentMethod,
                Order.payment_method_id == OrgPaymentMethod.id,
            )
            .where(*bookings_filter)
            .group_by(payment_model_bucket)
        )

        dist_res = await self._session.execute(dist_stmt)
        dist_rows = dist_res.all()

        # Sum only the filtered rows so the denominator matches total_charged above
        dist_total = sum((row.total_charged or Decimal("0.00")) for row in dist_rows if row.payment_model is not None)

        method_distribution = []
        for row in dist_rows:
            if row.payment_model is None:
                continue
            row_charged = row.total_charged or Decimal("0.00")
            usage_pct = (row_charged / dist_total * 100) if dist_total > 0 else 0
            method_distribution.append(
                PaymentMethodStats(
                    model=row.payment_model,
                    usage_percentage=float(usage_pct),
                    total_charged=row.total_charged or Decimal("0.00"),
                    order_count=row.order_count,
                )
            )

        # 5. Fetch payment config (VAT, attempt fees) and payment methods
        payment_config_orm = await self._config_repo.get_by_organization(org_id)
        methods = await self._payment_method_repo.list_by_org(org_id)
        if methods:
            seen_models = {m.model for m in method_distribution}
            for pm in methods:
                if pm.payment_model not in seen_models:
                    method_distribution.append(
                        PaymentMethodStats(
                            model=pm.payment_model,
                            usage_percentage=0.0,
                            total_charged=Decimal("0.00"),
                            order_count=0,
                        )
                    )

        # 6. Compute next_due_date from the default payment method's billing schedule
        next_due_date: date | None = None
        default_method = next((m for m in (methods or []) if m.is_default), None)
        if default_method:
            from app.modules.organizations.enums import BillingSchedule

            today = date.today()
            if default_method.billing_schedule == BillingSchedule.IMMEDIATE:
                next_due_date = today
            elif default_method.billing_schedule == BillingSchedule.FIXED_MONTHLY_DATE:
                day = default_method.billing_day_of_month or 1
                # Next occurrence of that day-of-month
                if today.day < day:
                    next_due_date = today.replace(day=day)
                else:
                    # Roll to next month
                    if today.month == 12:
                        next_due_date = date(today.year + 1, 1, day)
                    else:
                        next_due_date = date(today.year, today.month + 1, day)
            elif default_method.billing_schedule == BillingSchedule.DAYS_AFTER_ORDER:
                days_after = default_method.billing_days_after_order or 30
                next_due_date = today + timedelta(days=days_after)

        payment_method_responses = [OrgPaymentMethodResponse.model_validate(m) for m in (methods or [])]

        payment_config_response: OrgPaymentConfigResponse | None = None
        if payment_config_orm is not None:
            payment_config_response = OrgPaymentConfigResponse.model_validate(payment_config_orm)
            payment_config_response.payment_methods = payment_method_responses

        return OrgPaymentDetailsResponse(
            total_charged=total_charged,
            total_orders=total_orders,
            successful_payments=successful,
            failed_payments=failed,
            payment_success_rate=float(success_rate),
            total_invoiced=total_invoiced,
            paid_invoices_amount=paid_amount,
            unpaid_invoices_amount=unpaid_amount,
            overdue_amount=overdue_amount,
            invoice_count=invoice_count,
            credit_limit=credit_limit,
            used_credit=used_credit,
            available_credit=available_credit,
            credit_utilization_pct=utilization,
            next_due_date=next_due_date,
            payment_config=payment_config_response,
            payment_methods=payment_method_responses,
            method_distribution=method_distribution,
        )

    async def list_organizations(
        self,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        status: list[OrganizationStatus] | None = None,
        vat_registered: bool | None = None,
        pricing_type: str | None = None,
        payment_model: list[PaymentModel] | None = None,
        onboarded_by_user_id: list[str] | None = None,
        created_from: date | None = None,
        created_to: date | None = None,
        sort: str = "newest",
        caller: AuthUser | None = None,
    ) -> tuple[list[OrganizationListItemResponse], int]:
        """List organisations.

        CUSTOMER_B2B callers see only their own org (derived from JWT org_id claim).
        ADMIN callers see all orgs with full search/filter support.
        """
        if caller is not None and caller.role == UserRole.CUSTOMER_B2B:
            if not caller.organization_id:
                return [], 0
            org = await self._org_repo.get_by_id(caller.organization_id)
            if org is None:
                return [], 0
            return [
                _to_list_item(
                    OrgListRow(
                        org=org,
                        payment_models_csv=None,
                        credit_limit=None,
                        owner_account_email=None,
                        onboarded_by_first_name=None,
                        onboarded_by_last_name=None,
                        onboarded_by_role=None,
                        account_manager_first_name=None,
                        account_manager_last_name=None,
                        account_manager_role=None,
                        secondary_account_manager_first_name=None,
                        secondary_account_manager_last_name=None,
                        secondary_account_manager_role=None,
                        additional_account_manager_first_name=None,
                        additional_account_manager_last_name=None,
                        additional_account_manager_role=None,
                    )
                )
            ], 1

        rows, total = await self._org_repo.search_for_list(
            page=page,
            size=size,
            search=search,
            status=status,
            vat_registered=vat_registered,
            pricing_type=pricing_type,
            payment_model=payment_model,
            onboarded_by_user_id=onboarded_by_user_id,
            created_from=created_from,
            created_to=created_to,
            sort=sort,
        )
        return [_to_list_item(r) for r in rows], total

    # ── Update ────────────────────────────────────────────────────────────────

    # Fields a B2B ACCOUNT_OWNER is allowed to update on their own org.
    _B2B_ALLOWED_FIELDS: frozenset[str] = frozenset(
        {
            "trading_name",
            "legal_entity_name",
            "industry",
            "company_size",
            "date_of_incorporation",
            "description",
            "phone",
            "website",
            "companies_house_number",
            "eori_number",
            "vat_number",
            "registered_address",
            "trading_address",
        }
    )

    async def update_organization(
        self,
        org_id: str,
        data: OrganizationUpdate | OrgSelfUpdate,
        caller: AuthUser,
        caller_contact_role: ContactRole | None,
    ) -> OrganizationUpdateResponse:
        """Update organization fields.

        - Admin: can update any field. reason is mandatory.
        - CUSTOMER_B2B: requires ORG_PROFILE WRITE or ACCOUNT_OWNER; only B2B_ALLOWED_FIELDS
          are applied; admin-only fields (pricing, contract, limits) are silently stripped.
        """
        await assert_org_profile_access(self._session, caller, org_id, caller_contact_role, PermissionLevel.WRITE)

        caller_id = caller.id
        caller_role = caller.role if isinstance(caller.role, str) else caller.role.value

        org = await self._org_repo.get_by_id_or_404(org_id)

        old_values = {
            "trading_name": org.trading_name,
            "legal_entity_name": org.legal_entity_name,
            "companies_house_number": org.companies_house_number,
            "vat_number": org.vat_number,
            "notes": org.notes,
        }

        dump_exclude: set[str] = {"reason", "registered_address", "payment_config", "pickup_addresses"}
        if isinstance(data, OrgSelfUpdate):
            dump_exclude.add("trading_same_as_registered_address")

        trading_schema: TradingAddressSchema | None = None
        if isinstance(data, OrgSelfUpdate) and data.trading_same_as_registered_address:
            reg_src: RegisteredAddressSchema | None = None
            if "registered_address" in data.model_fields_set and data.registered_address is not None:
                reg_src = data.registered_address
            else:
                reg_src = _registered_address_from_org(org)
            if reg_src is None:
                raise ValidationError(
                    "trading_same_as_registered_address requires registered_address in this request " "or a complete registered address already stored on the organisation."
                )
            trading_schema = TradingAddressSchema(
                address_line_1=reg_src.address_line_1,
                address_line_2=reg_src.address_line_2,
                city=reg_src.city,
                state=reg_src.state,
                postcode=reg_src.postcode,
                country=reg_src.country or "United Kingdom",
            )
        elif "trading_address" in data.model_fields_set:
            trading_schema = data.trading_address

        # Flatten nested address object, exclude reason, address sub-schema, and
        # payment_config (handled separately below) from the org column payload
        update_payload = data.model_dump(
            exclude=dump_exclude,
            exclude_unset=True,
        )
        # Validate tier IDs, auto-populate base_price, enforce standard price
        if update_payload.get("pricing_plans"):
            update_payload["pricing_plans"] = await _validate_and_enrich_pricing_plans(update_payload["pricing_plans"], self._pricing_tier_repo)
        update_payload = _flatten_address(
            update_payload,
            data.registered_address if "registered_address" in data.model_fields_set else None,
            trading_schema,
        )

        # Strip admin-only fields for B2B callers
        if not is_platform_admin_role(caller_role):
            allowed = self._B2B_ALLOWED_FIELDS
            allowed = allowed | {f"reg_{k}" for k in ("address_line_1", "address_line_2", "city", "state", "postcode", "country")}
            allowed = allowed | {f"trading_address_{k}" for k in ("line_1", "line_2", "city", "state", "postcode", "country")}
            update_payload = {k: v for k, v in update_payload.items() if k in allowed}

        if update_payload:
            updated_org = await self._org_repo.update_by_id(org_id, update_payload)
            new_values = {k: getattr(updated_org, k) for k in old_values}

            await self._audit.log(
                action="organization.updated",
                entity_type="organization",
                entity_id=org_id,
                user_id=caller_id,
                old_value=old_values,
                new_value=new_values,
                reason=getattr(data, "reason", None),
                category=AuditCategory.ACCOUNT,
                event_type=AuditEventType.ACCOUNT_UPDATED,
                severity="NOTICE",
                organization_id=org_id,
                entity_ref=org.reference,
                user_role=caller_role,
            )
            logger.info("organization.updated", org_id=org_id, caller_id=caller_id)
        else:
            updated_org = org

        if isinstance(data, OrganizationUpdate) and is_platform_admin_role(caller_role) and "pricing_plans" in data.model_fields_set:
            from app.modules.organizations.pricing_plans_contract_sync import replace_org_contract_from_pricing_plans

            pl = updated_org.pricing_plans
            await replace_org_contract_from_pricing_plans(
                self._session,
                organization_id=org_id,
                plans=list(pl) if pl else [],
            )

        # Atomically update payment config if provided
        payment_config_response: OrgPaymentConfigResponse | None = None
        payment_config_data: OrgPaymentConfigUpdateEmbedded | None = getattr(data, "payment_config", None)
        if payment_config_data is not None and isinstance(data, OrganizationUpdate):
            pc_update = OrgPaymentConfigUpdate(
                **payment_config_data.model_dump(exclude_none=True),
                reason=data.reason,
            )
            payment_config_response = await OrgPaymentConfigService(self._session, self._request).update_payment_config(
                org_id=org_id,
                data=pc_update,
                admin_user_id=caller_id,
            )
        else:
            # Always fetch existing payment config so the response is complete
            existing_config = await self._config_repo.get_by_organization(org_id)
            if existing_config is not None:
                payment_config_response = await OrgPaymentConfigService(self._session, self._request)._build_response(existing_config)

        org_response = OrganizationResponse.model_validate(updated_org)
        org_response.logo_url = self.get_logo_url(updated_org.logo_cf_image_id)
        if updated_org.contract_reference:
            try:
                org_response.contract_url = generate_document_url(updated_org.contract_reference, expiry_seconds=3600)
            except Exception:
                logger.warning(
                    "organization.contract_url_generation_failed",
                    org_id=org_id,
                    key=updated_org.contract_reference,
                    exc_info=True,
                )
        return OrganizationUpdateResponse(
            organization=org_response,
            payment_config=payment_config_response,
        )

    async def update_profile_full(
        self,
        org_id: str,
        data: OrgProfileSavePayload,
        caller: AuthUser,
        caller_contact_role: ContactRole | None,
        logo: UploadFile | None = None,
    ) -> OrganizationUpdateResponse:
        """Single-request profile save: update fields and optional logo."""
        response = await self.update_organization(org_id, data, caller=caller, caller_contact_role=caller_contact_role)
        if "pickup_addresses" in data.model_fields_set and data.pickup_addresses is not None:
            await self._replace_org_pickup_addresses(
                org_id=org_id,
                pickup_addresses=data.pickup_addresses,
                actor_user_id=caller.id,
            )
        if logo is None:
            return response
        response.organization = await self.update_logo(org_id, logo, actor_user_id=caller.id)
        return response

    async def _replace_org_pickup_addresses(
        self,
        *,
        org_id: str,
        pickup_addresses: list,
        actor_user_id: str,
    ) -> None:
        """Replace organization pickup addresses in one save operation."""
        owner = PickupAddressOwner(organization_id=org_id)
        pickup_repo = PickupAddressRepository(self._session)
        existing = await pickup_repo.list_for_scope(organization_id=org_id, user_id=None)
        for row in existing:
            await pickup_repo.hard_delete(row.id, organization_id=org_id)
        create_request = CreatePickupAddressesRequest(root=pickup_addresses)
        await PickupAddressService(self._session, request=self._request).create_addresses_for_organization(
            owner=owner,
            request=create_request,
            actor_user_id=actor_user_id,
        )

    # ── Status change ─────────────────────────────────────────────────────────

    async def change_status(
        self,
        org_id: str,
        data: OrganizationStatusChange,
        admin_user_id: str,
    ) -> OrganizationResponse:
        org = await self._org_repo.get_by_id_or_404(org_id)

        allowed = _ALLOWED_TRANSITIONS.get(org.status, set())
        if data.status not in allowed:
            raise InvalidStateTransitionError(
                current_state=org.status.value,
                target_state=data.status.value,
                entity="Organization",
            )

        old_status = org.status
        updated_org = await self._org_repo.update_by_id(org_id, {"status": data.status})

        await self._audit.log(
            action="organization.status_changed",
            entity_type="organization",
            entity_id=org_id,
            user_id=admin_user_id,
            old_value={"status": old_status.value},
            new_value={"status": data.status.value},
            reason=data.reason,
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_STATUS_CHANGED,
            severity="NOTICE",
            organization_id=org_id,
            entity_ref=org.reference,
            user_role=UserRole.ADMIN.value,
        )

        logger.info(
            "organization.status_changed",
            org_id=org_id,
            old_status=old_status,
            new_status=data.status,
            admin_user_id=admin_user_id,
        )
        response = OrganizationResponse.model_validate(updated_org)
        response.logo_url = self.get_logo_url(updated_org.logo_cf_image_id)
        if updated_org.contract_reference:
            try:
                response.contract_url = generate_document_url(updated_org.contract_reference, expiry_seconds=3600)
            except Exception:
                logger.warning(
                    "organization.contract_url_generation_failed",
                    org_id=org_id,
                    key=updated_org.contract_reference,
                    exc_info=True,
                )
        return response

    async def place_on_hold(
        self,
        org_id: str,
        reason: str | None,
        admin_user_id: str,
    ) -> OrganizationResponse:
        """Place an organisation on hold — new bookings blocked, existing shipments continue."""
        data = OrganizationStatusChange(status=OrganizationStatus.ON_HOLD, reason=reason or "Placed on hold by admin")
        return await self.change_status(org_id, data, admin_user_id=admin_user_id)

    async def suspend_org(
        self,
        org_id: str,
        reason: str | None,
        admin_user_id: str,
    ) -> OrganizationResponse:
        """Suspend an organisation — pauses all active bookings and blocks new activity."""
        data = OrganizationStatusChange(status=OrganizationStatus.SUSPENDED, reason=reason or "Suspended by admin")
        return await self.change_status(org_id, data, admin_user_id=admin_user_id)

    async def deactivate_permanently(
        self,
        org_id: str,
        reason: str,
        confirm_name: str,
        admin_user_id: str,
    ) -> OrganizationResponse:
        """Permanently deactivate an organisation — requires company name confirmation."""
        org = await self._org_repo.get_by_id_or_404(org_id)
        if org.trading_name.strip().lower() != confirm_name.strip().lower():
            raise ValidationError("Company name confirmation does not match.")
        data = OrganizationStatusChange(status=OrganizationStatus.INACTIVE, reason=reason)
        return await self.change_status(org_id, data, admin_user_id=admin_user_id)

    # ── Delete ────────────────────────────────────────────────────────────────

    async def delete_organization(self, org_id: str, admin_user_id: str) -> None:
        org = await self._org_repo.get_by_id_or_404(org_id)

        await self._org_repo.soft_delete(
            org_id,
            status_field="status",
            target_status=OrganizationStatus.INACTIVE.value,
        )

        await self._audit.log(
            action="organization.deleted",
            entity_type="organization",
            entity_id=org_id,
            user_id=admin_user_id,
            old_value={"status": org.status.value},
            new_value={"status": OrganizationStatus.INACTIVE.value},
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_DEACTIVATED,
            severity="CRITICAL",
            organization_id=org_id,
            entity_ref=org.reference,
            user_role=UserRole.ADMIN.value,
        )

        logger.info("organization.deleted", org_id=org_id, admin_user_id=admin_user_id)

    # ── Contract upload ────────────────────────────────────────────────────────

    async def upload_contract(
        self,
        org_id: str,
        file: UploadFile,
        admin_user_id: str,
        *,
        title: str | None = None,
        expiry_date: date | None = None,
    ) -> ContractUploadResponse:
        """Upload or replace the signed contract PDF for an organisation.

        Validates the file is a PDF (max 10 MB), uploads it to R2, stores the
        R2 key in contract_reference, and returns a presigned download URL.
        Any previously stored contract is deleted from R2 on a best-effort basis.
        Admin only.
        """
        org = await self._org_repo.get_by_id_or_404(org_id)

        content, content_type = await read_and_validate(
            file,
            allowed_types={"application/pdf"},
            max_size=_CONTRACT_MAX_SIZE,
            label="Contract",
        )

        key = f"organizations/{org_id}/contracts/{org.reference}_{uuid.uuid4().hex[:8]}.pdf"
        await upload_to_r2(key, content, content_type)

        # Best-effort: remove the old contract file from R2
        old_key = org.contract_reference
        if old_key and old_key != key:
            try:
                await delete_from_r2(old_key)
            except Exception:
                logger.warning(
                    "organization.contract_old_delete_failed",
                    org_id=org_id,
                    old_key=old_key,
                )

        update_data: dict = {"contract_reference": key}
        if title is not None:
            update_data["contract_title"] = title
        if expiry_date is not None:
            update_data["contract_expiry_date"] = expiry_date
        await self._org_repo.update_by_id(org_id, update_data)

        await self._audit.log(
            action="organization.contract_uploaded",
            entity_type="organization",
            entity_id=org_id,
            user_id=admin_user_id,
            old_value={"contract_reference": old_key},
            new_value={"contract_reference": key},
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.DOCUMENT_UPLOADED,
            severity="NOTICE",
            organization_id=org_id,
            entity_ref=org.reference,
            user_role=UserRole.ADMIN.value,
        )

        logger.info("organization.contract_uploaded", org_id=org_id, key=key, admin_user_id=admin_user_id)

        url = generate_document_url(key, expiry_seconds=3600)
        return ContractUploadResponse(
            org_id=org_id,
            contract_reference=key,
            contract_url=url,
            contract_url_expires_in_seconds=3600,
        )

    async def update_contract_metadata(
        self,
        org_id: str,
        r2_key: str | None,
        title: str,
        expiry_date: date,
        admin_user_id: str,
    ) -> None:
        """Mirror contract metadata onto the organizations row after a document upload.

        Writes contract_reference, contract_title, contract_expiry_date and logs
        the change to the audit trail.
        """
        org = await self._org_repo.get_by_id_or_404(org_id)
        old_key = org.contract_reference
        await self._org_repo.update_by_id(
            org_id,
            {
                "contract_reference": r2_key,
                "contract_title": title,
                "contract_expiry_date": expiry_date,
            },
        )
        await self._audit.log(
            action="organization.contract_metadata_updated",
            entity_type="organization",
            entity_id=org_id,
            user_id=admin_user_id,
            old_value={"contract_reference": old_key},
            new_value={"contract_reference": r2_key, "contract_title": title},
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.DOCUMENT_UPLOADED,
            severity="NOTICE",
            organization_id=org_id,
            entity_ref=org.reference,
            user_role=UserRole.ADMIN.value,
        )

    # ── Logo (profile image) ──────────────────────────────────────────────────

    def get_logo_url(self, logo_cf_image_id: str | None, *, expiry_seconds: int = 3600) -> str | None:
        """Return a signed Cloudflare Images URL for the org logo, or None if unset."""
        if not logo_cf_image_id:
            return None
        try:
            return generate_image_url(logo_cf_image_id, expiry_seconds=expiry_seconds)
        except Exception:
            logger.warning("organization.logo_url_generation_failed", logo_cf_image_id=logo_cf_image_id)
            return None

    async def update_logo(
        self,
        org_id: str,
        file: UploadFile,
        actor_user_id: str,
    ) -> OrganizationResponse:
        """Upload or replace the logo for an organisation via Cloudflare Images.

        Accepts JPEG or PNG, max 2 MB. The previous logo is deleted from Cloudflare
        on a best-effort basis. Caller must be authorised (admin or ORG_PROFILE WRITE / owner).
        """
        org = await self._org_repo.get_by_id_or_404(org_id)

        content, _ = await read_and_validate(
            file,
            allowed_types={"image/jpeg", "image/png"},
            max_size=2 * 1024 * 1024,
            label="Logo",
        )

        result = await upload_image(
            content,
            filename=file.filename or "org-logo",
            metadata={"kind": "org_logo", "org_id": org_id},
        )

        old_image_id = org.logo_cf_image_id
        updated = await self._org_repo.update_by_id(org_id, {"logo_cf_image_id": result.id})

        # Best-effort: remove old image from Cloudflare
        if old_image_id:
            try:
                await delete_image(old_image_id)
            except Exception:
                logger.warning(
                    "organization.logo_old_delete_failed",
                    org_id=org_id,
                    old_image_id=old_image_id,
                )

        await self._audit.log(
            action="organization.logo_updated",
            entity_type="organization",
            entity_id=org_id,
            user_id=actor_user_id,
            old_value={"logo_cf_image_id": old_image_id},
            new_value={"logo_cf_image_id": result.id},
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_UPDATED,
            organization_id=org_id,
            entity_ref=org.reference,
            user_role=UserRole.ADMIN.value,
        )
        logger.info("organization.logo_updated", org_id=org_id, actor_user_id=actor_user_id)

        response = OrganizationResponse.model_validate(updated)
        response.logo_url = self.get_logo_url(updated.logo_cf_image_id)
        return response

    # ── Account manager ───────────────────────────────────────────────────────

    async def list_account_managers(
        self,
        *,
        search: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[AccountManagerResponse], int]:
        """Return all admin users eligible to be assigned as account managers.

        Includes ADMIN and SUPER_ADMIN roles. Optional name/email search.
        """
        users, total = await self._user_repo.list_account_managers(search=search, page=page, size=size)
        managers = [
            AccountManagerResponse(
                id=u.id,
                first_name=u.first_name,
                last_name=u.last_name,
                full_name=f"{u.first_name} {u.last_name}".strip(),
                email=u.email,
                phone=getattr(u, "phone", None),
                role=u.role.value if hasattr(u.role, "value") else str(u.role),
            )
            for u in users
        ]
        return managers, total

    async def get_account_manager(self, org_id: str) -> OrgAccountManagerResponse:
        """Return the account manager assigned to this organisation, or null if unassigned."""
        org = await self._org_repo.get_by_id_or_404(org_id)

        if not org.account_manager_user_id:
            return OrgAccountManagerResponse(org_id=org_id, account_manager=None)

        manager = await self._user_repo.get_by_id(org.account_manager_user_id)
        if manager is None:
            return OrgAccountManagerResponse(org_id=org_id, account_manager=None)

        return OrgAccountManagerResponse(
            org_id=org_id,
            account_manager=AccountManagerResponse(
                id=manager.id,
                first_name=manager.first_name,
                last_name=manager.last_name,
                full_name=f"{manager.first_name} {manager.last_name}".strip(),
                email=manager.email,
                phone=getattr(manager, "phone", None),
                role=manager.role.value if hasattr(manager.role, "value") else str(manager.role),
            ),
        )

    async def assign_account_manager(
        self,
        org_id: str,
        data: AssignAccountManagerRequest,
        admin_user_id: str,
    ) -> OrgAccountManagerResponse:
        """Assign or unassign the account manager for an organisation.

        Validates the target user exists and has an admin-level role before assigning.
        Pass account_manager_user_id=None to unassign.
        """
        org = await self._org_repo.get_by_id_or_404(org_id)

        new_manager_id = data.account_manager_user_id
        if new_manager_id is not None:
            manager = await self._user_repo.get_by_id(new_manager_id)
            if manager is None:
                raise NotFoundError(resource="user", id=new_manager_id)
            if manager.role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
                raise ValidationError("Account manager must be an admin user.")

        old_manager_id = org.account_manager_user_id
        await self._org_repo.update_by_id(org_id, {"account_manager_user_id": new_manager_id})

        await self._audit.log(
            action="organization.account_manager_assigned" if new_manager_id else "organization.account_manager_unassigned",
            entity_type="organization",
            entity_id=org_id,
            user_id=admin_user_id,
            old_value={"account_manager_user_id": old_manager_id},
            new_value={"account_manager_user_id": new_manager_id},
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_UPDATED,
            organization_id=org_id,
            entity_ref=org.reference,
            user_role=UserRole.ADMIN.value,
        )
        logger.info(
            "organization.account_manager_changed",
            org_id=org_id,
            old_manager_id=old_manager_id,
            new_manager_id=new_manager_id,
            admin_user_id=admin_user_id,
        )

        return await self.get_account_manager(org_id)

    async def get_contract_url(
        self,
        org_id: str,
        caller_role: str,
        caller_contact_role: ContactRole | None,
    ) -> ContractUploadResponse:
        """Return a fresh presigned download URL for an organisation's contract PDF.

        Admin or same-org CUSTOMER_B2B. Raises 404 if no contract has been uploaded.
        """
        if not is_platform_admin_role(caller_role) and caller_contact_role is None:
            raise ForbiddenError("You do not have access to this organisation.")

        org = await self._org_repo.get_by_id_or_404(org_id)

        if not org.contract_reference:
            raise NotFoundError(resource="contract", id=org_id)

        url = generate_document_url(org.contract_reference, expiry_seconds=3600)
        return ContractUploadResponse(
            org_id=org_id,
            contract_reference=org.contract_reference,
            contract_url=url,
            contract_url_expires_in_seconds=3600,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_full_access(caller_role: str, caller_contact_role: ContactRole | None) -> bool:
    """Return True if the caller should receive un-redacted (full PII) contact details."""
    return is_platform_admin_role(caller_role) or caller_contact_role is not None


# Minimum permission floor enforced for every ACCOUNT_OWNER OrgContact at creation time.
_ACCOUNT_OWNER_FLOOR_PERMISSIONS: dict[Resource, PermissionLevel] = {
    Resource.DASHBOARD: PermissionLevel.READ,
    Resource.ORDERS: PermissionLevel.WRITE,
    Resource.CARD_PAYMENT: PermissionLevel.WRITE,
    Resource.BILLING: PermissionLevel.WRITE,
    Resource.NOTIFICATIONS: PermissionLevel.WRITE,
    Resource.REQUEST_CREDIT: PermissionLevel.WRITE,
    Resource.DOCUMENTS: PermissionLevel.WRITE,
    Resource.CONTACTS: PermissionLevel.WRITE,
    Resource.AUDIT_LOG: PermissionLevel.WRITE,
    Resource.ORG_PROFILE: PermissionLevel.WRITE,
}


async def _apply_permission_overrides(
    perm_service: PermissionService,
    user_id: str,
    granted_by: str,
    overrides: list[ContactPermission] | None,
    *,
    contact_role: ContactRole | None = None,
) -> None:
    """Apply per-user permission overrides.

    For ``ACCOUNT_OWNER`` contacts, the :data:`_ACCOUNT_OWNER_FLOOR_PERMISSIONS`
    map is treated as a floor: each entry is raised to at least the floor level
    when the caller-supplied override is missing or lower. Other contact roles
    receive only the caller-supplied overrides.
    """
    permissions = {Resource(o.resource): PermissionLevel(o.level) for o in (overrides or [])}
    if contact_role == ContactRole.ACCOUNT_OWNER:
        for resource, floor_level in _ACCOUNT_OWNER_FLOOR_PERMISSIONS.items():
            if permissions.get(resource, PermissionLevel.NONE) < floor_level:
                permissions[resource] = floor_level
    if not permissions:
        return
    await perm_service.bulk_set_permissions(
        target_user_id=user_id,
        permissions=permissions,
        granted_by=granted_by,
    )


class OrgContactService(BaseService):
    """Business logic for managing contacts within a client organisation."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._org_repo = OrganizationRepository(session)
        self._contact_repo = OrgContactRepository(session)
        self._user_repo = UserRepository(session)
        self._auth_service = AuthService(session, request=request)
        self._perm_service = PermissionService(session, request=request)
        self._audit = AuditService(session, request=request)

    # ── Read ──────────────────────────────────────────────────────────────────

    async def list_contacts(
        self,
        org_id: str,
        caller_role: str,
        caller_contact_role: ContactRole | None,
    ) -> OrgContactListResponse:
        """List active contacts for an org, split into owner + team members.

        The ACCOUNT_OWNER is returned separately (permissions not included —
        owner permissions are not editable). All other contacts include their
        full resolved permission set.

        GDPR scoping:
        - ADMIN or same-org CUSTOMER_B2B: full details (name, email, phone)
        - Other authenticated callers: name + role only
        """
        await self._org_repo.get_by_id_or_404(org_id)
        contacts = await self._contact_repo.list_with_user(org_id)

        full_access = _is_full_access(caller_role, caller_contact_role)

        owner: OrgContactDetailResponse | None = None
        team_members: list[OrgContactDetailResponse] = []

        for c in contacts:
            is_owner = c.contact_role == ContactRole.ACCOUNT_OWNER
            # Owner: no permissions returned (not configurable)
            resolved = None if is_owner else (await self._perm_service.resolve_permissions(c.user) if c.user else None)
            entry = OrgContactDetailResponse.from_orm_contact(c, redact_pii=not full_access, resolved_permissions=resolved)
            if is_owner and owner is None:
                owner = entry
            else:
                team_members.append(entry)

        return OrgContactListResponse(owner=owner, team_members=team_members)

    async def get_contact(
        self,
        org_id: str,
        contact_id: str,
        caller_role: str,
        caller_contact_role: ContactRole | None,
    ) -> OrgContactDetailResponse:
        """Fetch a single contact scoped to the org."""
        contact = await self._contact_repo.get_with_user(org_id, contact_id)
        if contact is None:
            raise NotFoundError(resource="org_contacts", id=contact_id)

        full_access = _is_full_access(caller_role, caller_contact_role)
        resolved = await self._perm_service.resolve_permissions(contact.user) if contact.user else None
        return OrgContactDetailResponse.from_orm_contact(contact, redact_pii=not full_access, resolved_permissions=resolved)

    async def resolve_contact_user_for_support_password(self, org_id: str, contact_id: str) -> User:
        await self._org_repo.get_by_id_or_404(org_id)
        contact = await self._contact_repo.get_with_user(org_id, contact_id)
        if contact is None or contact.user is None:
            raise NotFoundError(resource="org_contact", id=contact_id)
        return contact.user

    # ── Create ────────────────────────────────────────────────────────────────

    async def add_contact(
        self,
        org_id: str,
        data: OrgContactCreate,
        caller: AuthUser,
        caller_contact_role: ContactRole | None,
    ) -> OrgContactDetailResponse:
        """Add a new contact to an org + create their User + send invite.

        Permission: ADMIN/SUPER_ADMIN, or same-org ACCOUNT_OWNER, or same-org
        CUSTOMER_B2B with CONTACTS WRITE.
        Also applies any custom permission overrides declared in data.permissions.
        """
        await self._assert_can_manage_contacts(
            caller=caller,
            org_id=org_id,
            caller_contact_role=caller_contact_role,
        )
        caller_id = caller.id
        caller_role = caller.role

        await self._org_repo.get_by_id_or_404(org_id)

        email = data.email.strip().lower()
        if await self._user_repo.email_exists(email):
            raise ConflictError(f"User with email '{email}' already exists.")

        dummy_password = hash_password("INVITED_USER_PLACEHOLDER")
        user = await self._user_repo.create(
            {
                "email": email,
                "first_name": data.first_name,
                "last_name": data.last_name,
                "role": UserRole.CUSTOMER_B2B,
                "status": UserStatus.PENDING_VERIFICATION,
                "organization_id": org_id,
                "password_hash": dummy_password,
            }
        )

        contact = await self._contact_repo.create(
            {
                "organization_id": org_id,
                "contact_number": data.contact_number,
                "contact_role": data.contact_role,
                "status": ContactStatus.PENDING,
                "is_primary": False,
                "user_id": user.id,
            }
        )

        # Apply custom permission overrides before sending invite
        await _apply_permission_overrides(
            self._perm_service,
            user.id,
            caller_id,
            data.permissions,
            contact_role=data.contact_role,
        )

        ir = await self._auth_service.create_invite(
            caller,
            user.id,
            expires_days=1,
            organization_id=org_id,
        )
        if not ir.throttled:
            invite_link = _b2b_invite_email_link(ir.raw_token or "")
            await enqueue(
                Job.SEND_INVITE_EMAIL,
                invite_id=ir.public_invite_id,
                to_email=email,
                first_name=user.first_name,
                invite_link=invite_link,
                expires_days=1,
                priority=QueuePriority.DEFAULT,
            )

        await self._audit.log(
            action="org_contact.added",
            entity_type="org_contact",
            entity_id=contact.id,
            user_id=caller_id,
            new_value={
                "org_id": org_id,
                "email": email,
                "contact_role": data.contact_role,
                "user_id": user.id,
            },
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_UPDATED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=caller_role,
        )
        logger.info("org_contact.added", org_id=org_id, contact_id=contact.id, caller_id=caller_id)

        # Reload with user relationship populated
        loaded = await self._contact_repo.get_with_user(org_id, contact.id)
        resolved = await self._perm_service.resolve_permissions(loaded.user) if loaded and loaded.user else None  # type: ignore[union-attr]
        return OrgContactDetailResponse.from_orm_contact(loaded, redact_pii=False, resolved_permissions=resolved)  # type: ignore[arg-type]

    # ── Update ────────────────────────────────────────────────────────────────

    async def update_contact(
        self,
        org_id: str,
        contact_id: str,
        data: OrgContactUpdate,
        caller: AuthUser,
        caller_contact_role: ContactRole | None,
    ) -> OrgContactDetailResponse:
        """Edit first_name, last_name, contact_number, contact_role, and/or permission overrides."""
        await self._assert_can_manage_contacts(
            caller=caller,
            org_id=org_id,
            caller_contact_role=caller_contact_role,
        )
        caller_id = caller.id
        caller_role = caller.role

        contact = await self._contact_repo.get_with_user(org_id, contact_id)
        if contact is None:
            raise NotFoundError(resource="org_contacts", id=contact_id)

        contact_payload: dict = {}
        if data.contact_number is not None:
            contact_payload["contact_number"] = data.contact_number
        if data.contact_role is not None:
            contact_payload["contact_role"] = data.contact_role

        if contact_payload:
            contact = await self._contact_repo.update_by_id(contact_id, contact_payload)

        user_payload: dict = {}
        if data.first_name is not None:
            user_payload["first_name"] = data.first_name
        if data.last_name is not None:
            user_payload["last_name"] = data.last_name

        if user_payload:
            if contact.user_id is None:
                raise ValidationError("Cannot update name: contact has no linked user yet.")
            await self._user_repo.update_by_id(contact.user_id, user_payload)

        if data.permissions is not None:
            if contact.user_id is None:
                raise ValidationError("Cannot set permissions: contact has no linked user yet.")
            effective_contact_role = data.contact_role or contact.contact_role
            await _apply_permission_overrides(
                self._perm_service,
                contact.user_id,
                caller_id,
                data.permissions,
                contact_role=effective_contact_role,
            )
        elif data.contact_role == ContactRole.ACCOUNT_OWNER and contact.user_id is not None:
            # Enforce owner baseline even when no explicit permissions payload is sent.
            await self._perm_service.set_permission(
                target_user_id=contact.user_id,
                resource=Resource.AUDIT_LOG,
                level=PermissionLevel.READ,
                granted_by=caller_id,
            )

        audit_new_value = {k: str(v) for k, v in {**contact_payload, **user_payload}.items()}
        await self._audit.log(
            action="org_contact.updated",
            entity_type="org_contact",
            entity_id=contact_id,
            user_id=caller_id,
            new_value=audit_new_value,
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_UPDATED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=caller_role,
        )
        logger.info("org_contact.updated", org_id=org_id, contact_id=contact_id, caller_id=caller_id)

        loaded = await self._contact_repo.get_with_user(org_id, contact_id)
        resolved = await self._perm_service.resolve_permissions(loaded.user) if loaded and loaded.user else None  # type: ignore[union-attr]
        return OrgContactDetailResponse.from_orm_contact(loaded, redact_pii=False, resolved_permissions=resolved)  # type: ignore[arg-type]

    # ── Remove (soft-delete) ──────────────────────────────────────────────────

    async def remove_contact(
        self,
        org_id: str,
        contact_id: str,
        caller: AuthUser,
        caller_contact_role: ContactRole | None,
    ) -> None:
        """Soft-delete a contact (sets status=INACTIVE).

        Blocks removal of the last active contact in an org.
        """
        await self._assert_can_manage_contacts(
            caller=caller,
            org_id=org_id,
            caller_contact_role=caller_contact_role,
        )
        caller_id = caller.id
        caller_role = caller.role

        contact = await self._contact_repo.get_with_user(org_id, contact_id)
        if contact is None:
            raise NotFoundError(resource="org_contacts", id=contact_id)

        active_count = await self._contact_repo.count_active(org_id)
        if active_count <= 1:
            raise ValidationError("Cannot remove the last active contact of an organisation.")

        await self._contact_repo.update_by_id(contact_id, {"status": ContactStatus.INACTIVE, "is_primary": False})

        await self._audit.log(
            action="org_contact.removed",
            entity_type="org_contact",
            entity_id=contact_id,
            user_id=caller_id,
            new_value={"status": ContactStatus.INACTIVE},
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_UPDATED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=caller_role,
        )
        logger.info("org_contact.removed", org_id=org_id, contact_id=contact_id, caller_id=caller_id)

    # ── Set primary ───────────────────────────────────────────────────────────

    async def set_primary(
        self,
        org_id: str,
        contact_id: str,
        caller: AuthUser,
        caller_contact_role: ContactRole | None,
    ) -> OrgContactDetailResponse:
        """Atomically mark one contact as primary, clearing all others."""
        await self._assert_can_manage_contacts(
            caller=caller,
            org_id=org_id,
            caller_contact_role=caller_contact_role,
        )
        caller_id = caller.id
        caller_role = caller.role

        contact = await self._contact_repo.get_with_user(org_id, contact_id)
        if contact is None:
            raise NotFoundError(resource="org_contacts", id=contact_id)

        # Single atomic UPDATE: SET is_primary = (id = :contact_id) for all contacts
        # in this org — avoids the clear-then-set race condition.
        await self._contact_repo.set_primary_atomic(org_id, contact_id)

        await self._audit.log(
            action="org_contact.primary_set",
            entity_type="org_contact",
            entity_id=contact_id,
            user_id=caller_id,
            new_value={"is_primary": True, "org_id": org_id},
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_UPDATED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=caller_role,
        )
        logger.info("org_contact.primary_set", org_id=org_id, contact_id=contact_id, caller_id=caller_id)

        loaded = await self._contact_repo.get_with_user(org_id, contact_id)
        if loaded is None:
            raise NotFoundError(resource="org_contacts", id=contact_id)
        return OrgContactDetailResponse.from_orm_contact(loaded, redact_pii=False)

    async def _assert_can_manage_contacts(
        self,
        *,
        caller: AuthUser,
        org_id: str,
        caller_contact_role: ContactRole | None,
    ) -> None:
        """Require admin/super-admin, account owner, or CONTACTS WRITE for same-org B2B members."""
        if is_platform_admin_role(caller.role):
            return
        # All B2B contact management paths are scoped to caller org.
        assert_caller_org_scope(caller, org_id)
        if caller_contact_role == ContactRole.ACCOUNT_OWNER:
            return
        await self._perm_service.check_permission(caller, Resource.CONTACTS, PermissionLevel.WRITE)


# ── OrgPaymentConfigService ───────────────────────────────────────────────────


class OrgPaymentConfigService(BaseService):
    """CRUD service for organisation payment configuration.

    OrgPaymentConfig (shared settings: VAT, delivery/return attempts, weight)
    is one-to-one with Organization.

    OrgPaymentMethod (per-model settings: billing schedule, bank details, etc.)
    is one-to-many — an org may have multiple enabled payment models.

    All writes are Admin-only; reads are accessible to Admin or any active org member.
    """

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._config_repo = OrgPaymentConfigRepository(session)
        self._method_repo = OrgPaymentMethodRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._global_attempt_repo = DeliveryAttemptConfigRepository(session)
        self._audit = AuditService(session, request=request)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _serialize_fees(self, fees) -> list[dict]:
        return [{"attempt": f.attempt, "fee": str(f.fee)} for f in fees]

    async def _default_attempt_schedule_payload(self) -> dict:
        """Resolve default attempt config from global singleton; fall back to zero-fee defaults."""
        global_config = await self._global_attempt_repo.get_singleton()
        if global_config is None:
            max_delivery_attempts = 3
            max_return_attempts = 3
            delivery_attempt_fees = default_fee_entries(max_delivery_attempts)
            return_attempt_fees = default_fee_entries(max_return_attempts)
        else:
            max_delivery_attempts = int(global_config.max_delivery_attempts)
            max_return_attempts = int(global_config.max_return_attempts)
            delivery_attempt_fees = list(global_config.delivery_attempt_fees or default_fee_entries(max_delivery_attempts))
            return_attempt_fees = list(global_config.return_attempt_fees or default_fee_entries(max_return_attempts))
        return {
            "max_delivery_attempts": max_delivery_attempts,
            "delivery_attempt_fees": delivery_attempt_fees,
            "max_return_attempts": max_return_attempts,
            "return_attempt_fees": return_attempt_fees,
        }

    async def _build_response(self, config: OrgPaymentConfig) -> OrgPaymentConfigResponse:
        """Build OrgPaymentConfigResponse including all payment methods."""
        methods = await self._method_repo.list_by_org(config.organization_id)
        method_responses = [OrgPaymentMethodResponse.model_validate(m) for m in methods]
        data = OrgPaymentConfigResponse.model_validate(config)
        data.payment_methods = method_responses
        return data

    # ── Create ────────────────────────────────────────────────────────────────

    async def create_payment_config(
        self,
        org_id: str,
        data: OrgPaymentConfigCreate,
        admin_user_id: str,
    ) -> OrgPaymentConfigResponse:
        """Create shared config + all payment methods atomically.

        Raises ConflictError if shared config already exists.
        """
        existing = await self._config_repo.get_by_organization(org_id)
        if existing is not None:
            raise ConflictError("A payment configuration already exists for this organisation. Use PATCH to update it.")

        # Create shared config row
        shared_payload = data.model_dump(exclude={"payment_methods"})
        shared_payload["delivery_attempt_fees"] = self._serialize_fees(data.delivery_attempt_fees)
        shared_payload["return_attempt_fees"] = self._serialize_fees(data.return_attempt_fees)
        shared_payload["organization_id"] = org_id
        config = await self._config_repo.create(shared_payload)

        # Create one payment method row per entry; ensure exactly one default
        has_explicit_default = any(m.is_default for m in data.payment_methods)
        for i, method_data in enumerate(data.payment_methods):
            method_payload = method_data.model_dump()
            method_payload["organization_id"] = org_id
            # If no method is marked default, make the first one the default
            if not has_explicit_default:
                method_payload["is_default"] = i == 0
            await self._method_repo.create(method_payload)

        await self._audit.log(
            action="org_payment_config.created",
            entity_type="org_payment_config",
            entity_id=config.id,
            user_id=admin_user_id,
            new_value={
                "payment_models": [m.payment_model for m in data.payment_methods],
                "vat_rate": data.vat_rate,
            },
            category=AuditCategory.BILLING,
            event_type=AuditEventType.ACCOUNT_UPDATED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
        )
        logger.info("org_payment_config.created", org_id=org_id, config_id=config.id, admin_id=admin_user_id)

        return await self._build_response(config)

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_payment_config(
        self,
        org_id: str,
        caller_role: str,
        caller_contact_role: ContactRole | None,
    ) -> OrgPaymentConfigResponse:
        """Return the payment config + all payment methods.

        - Admin: any org.
        - CUSTOMER_B2B: only their own org (caller_contact_role must be non-None).
        """
        if not is_platform_admin_role(caller_role) and caller_contact_role is None:
            raise ForbiddenError("You do not have access to this organisation's payment configuration.")

        config = await self._config_repo.get_by_organization(org_id)
        if config is None:
            await self._org_repo.get_by_id_or_404(org_id)
            payload = {"organization_id": org_id, **(await self._default_attempt_schedule_payload())}
            config = await self._config_repo.create(payload)
        return await self._build_response(config)

    # ── Update shared config ──────────────────────────────────────────────────

    async def update_payment_config(
        self,
        org_id: str,
        data: OrgPaymentConfigUpdate,
        admin_user_id: str,
    ) -> OrgPaymentConfigResponse:
        """Update the shared payment config fields (VAT, attempt fees, weight). Admin only.

        Auto-creates the config row if it doesn't exist yet (org was created without one).
        """
        await self._org_repo.get_by_id_or_404(org_id)
        config = await self._config_repo.get_by_organization(org_id)
        if config is None:
            payload = {"organization_id": org_id, **(await self._default_attempt_schedule_payload())}
            config = await self._config_repo.create(payload)

        incoming = data.model_dump(exclude={"reason"}, exclude_none=True)

        # Compact/serialize fees if provided and keep max_* in sync.
        if "delivery_attempt_fees" in incoming:
            compacted = compact_attempt_fees(incoming["delivery_attempt_fees"], "delivery_attempt_fees")
            requested_max = incoming.get("max_delivery_attempts")
            if requested_max is not None and requested_max != len(compacted):
                raise ValidationError(f"max_delivery_attempts={requested_max} does not match compacted delivery_attempt_fees " f"length {len(compacted)}.")
            incoming["delivery_attempt_fees"] = compacted
            incoming["max_delivery_attempts"] = len(compacted)
        elif "max_delivery_attempts" in incoming:
            existing_delivery_fees = list(config.delivery_attempt_fees or [])
            if len(existing_delivery_fees) != incoming["max_delivery_attempts"]:
                raise ValidationError("max_delivery_attempts cannot be changed without delivery_attempt_fees. " "Send the full fee list to keep attempts contiguous.")

        if "return_attempt_fees" in incoming:
            compacted = compact_attempt_fees(incoming["return_attempt_fees"], "return_attempt_fees")
            requested_max = incoming.get("max_return_attempts")
            if requested_max is not None and requested_max != len(compacted):
                raise ValidationError(f"max_return_attempts={requested_max} does not match compacted return_attempt_fees " f"length {len(compacted)}.")
            incoming["return_attempt_fees"] = compacted
            incoming["max_return_attempts"] = len(compacted)
        elif "max_return_attempts" in incoming:
            existing_return_fees = list(config.return_attempt_fees or [])
            if len(existing_return_fees) != incoming["max_return_attempts"]:
                raise ValidationError("max_return_attempts cannot be changed without return_attempt_fees. " "Send the full fee list to keep attempts contiguous.")

        old_value = {"vat_rate": config.vat_rate, "max_delivery_attempts": config.max_delivery_attempts}
        updated = await self._config_repo.update_by_id(config.id, incoming)

        await self._audit.log(
            action="org_payment_config.updated",
            entity_type="org_payment_config",
            entity_id=config.id,
            user_id=admin_user_id,
            old_value=old_value,
            new_value={k: str(v) for k, v in incoming.items() if "fees" not in k},
            reason=data.reason,
            category=AuditCategory.BILLING,
            event_type=AuditEventType.ACCOUNT_UPDATED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
        )
        logger.info("org_payment_config.updated", org_id=org_id, config_id=config.id, admin_id=admin_user_id)

        return await self._build_response(updated)

    # ── Payment method CRUD ───────────────────────────────────────────────────

    async def add_payment_method(
        self,
        org_id: str,
        data: OrgPaymentMethodCreate,
        admin_user_id: str,
    ) -> OrgPaymentConfigResponse:
        """Add a payment method to an org.  Raises ConflictError if the model already exists."""
        config = await self._config_repo.get_by_organization_or_404(org_id)

        existing_method = await self._method_repo.get_by_org_and_model(org_id, data.payment_model)
        if existing_method is not None:
            raise ConflictError(f"Payment method {data.payment_model} already exists for this organisation.")

        # If this method is marked as default, clear existing default first
        if data.is_default:
            await self._method_repo.clear_default(org_id)

        method_payload = data.model_dump()
        method_payload["organization_id"] = org_id
        method = await self._method_repo.create(method_payload)

        await self._audit.log(
            action="org_payment_method.added",
            entity_type="org_payment_method",
            entity_id=method.id,
            user_id=admin_user_id,
            new_value={"payment_model": data.payment_model, "is_default": data.is_default},
            category=AuditCategory.BILLING,
            event_type=AuditEventType.ACCOUNT_UPDATED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
        )
        logger.info("org_payment_method.added", org_id=org_id, method_id=method.id, model=data.payment_model)

        return await self._build_response(config)

    async def update_payment_method(
        self,
        org_id: str,
        payment_model: PaymentModel,
        data,
        admin_user_id: str,
    ) -> OrgPaymentConfigResponse:
        """Update a specific payment method by model name."""
        config = await self._config_repo.get_by_organization_or_404(org_id)
        method = await self._method_repo.get_by_org_and_model(org_id, payment_model)
        if method is None:
            raise NotFoundError(resource="OrgPaymentMethod", id=f"{org_id}/{payment_model}")

        incoming = data.model_dump(exclude_none=True)

        # If setting this method as default, clear all others first
        if incoming.get("is_default"):
            await self._method_repo.clear_default(org_id)

        updated = await self._method_repo.update_by_id(method.id, incoming)

        await self._audit.log(
            action="org_payment_method.updated",
            entity_type="org_payment_method",
            entity_id=method.id,
            user_id=admin_user_id,
            new_value={k: str(v) for k, v in incoming.items()},
            category=AuditCategory.BILLING,
            event_type=AuditEventType.ACCOUNT_UPDATED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
        )
        logger.info("org_payment_method.updated", org_id=org_id, method_id=method.id, model=payment_model)

        return await self._build_response(config)

    async def remove_payment_method(
        self,
        org_id: str,
        payment_model: PaymentModel,
        admin_user_id: str,
    ) -> None:
        """Remove a payment method.  Cannot remove the last method or the default method."""
        method = await self._method_repo.get_by_org_and_model(org_id, payment_model)
        if method is None:
            raise NotFoundError(resource="OrgPaymentMethod", id=f"{org_id}/{payment_model}")

        all_methods = await self._method_repo.list_by_org(org_id)
        if len(all_methods) <= 1:
            raise ValidationError("Cannot remove the last payment method. Add another method first.")
        if method.is_default:
            raise ValidationError("Cannot remove the default payment method. Set another method as default first.")

        await self._method_repo.hard_delete(method.id)

        await self._audit.log(
            action="org_payment_method.removed",
            entity_type="org_payment_method",
            entity_id=method.id,
            user_id=admin_user_id,
            old_value={"payment_model": payment_model},
            category=AuditCategory.BILLING,
            event_type=AuditEventType.ACCOUNT_UPDATED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
        )
        logger.info("org_payment_method.removed", org_id=org_id, method_id=method.id, model=payment_model)

    # ── Delete entire config ──────────────────────────────────────────────────

    async def delete_payment_config(
        self,
        org_id: str,
        admin_user_id: str,
    ) -> None:
        """Hard-delete the shared config (CASCADE removes all payment methods). Admin only."""
        config = await self._config_repo.get_by_organization_or_404(org_id)

        await self._config_repo.hard_delete(config.id)

        await self._audit.log(
            action="org_payment_config.deleted",
            entity_type="org_payment_config",
            entity_id=config.id,
            user_id=admin_user_id,
            category=AuditCategory.BILLING,
            event_type=AuditEventType.ACCOUNT_UPDATED,
            severity="CRITICAL",
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
        )
        logger.info("org_payment_config.deleted", org_id=org_id, config_id=config.id, admin_id=admin_user_id)


# ── Org Document Service ───────────────────────────────────────────────────────

_DOC_URL_TTL = 3600  # 1 hour
_EXPIRING_SOON_DAYS = 30


def _make_doc_r2_key(org_id: str, filename: str) -> str:
    """Build a deterministic, filesystem-safe R2 object key for a document."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe = re.sub(r"[^\w.\-]", "_", filename)[:100]
    return f"organizations/{org_id}/documents/{ts}_{safe}"


def _compute_doc_status(expiry_date) -> OrgDocumentStatus:
    """Derive status from expiry_date. No expiry → always ACTIVE."""
    if expiry_date is None:
        return OrgDocumentStatus.ACTIVE
    today = datetime.now(UTC).date()
    if expiry_date < today:
        return OrgDocumentStatus.EXPIRED
    if expiry_date <= today + timedelta(days=_EXPIRING_SOON_DAYS):
        return OrgDocumentStatus.EXPIRING_SOON
    return OrgDocumentStatus.ACTIVE


def _build_doc_response(doc, url: str) -> OrgDocumentResponse:
    """Build OrgDocumentResponse from an OrgDocument ORM row + presigned URL."""
    return OrgDocumentResponse(
        id=doc.id,
        organization_id=doc.organization_id,
        reference=doc.reference,
        title=doc.title,
        document_type=doc.document_type,
        category=doc.category,
        status=doc.status,
        issuing_authority=doc.issuing_authority,
        issue_date=doc.issue_date,
        expiry_date=doc.expiry_date,
        description=doc.description,
        confidentiality_level=doc.confidentiality_level,
        tags=doc.tags,
        uploaded_by=doc.uploaded_by,
        uploaded_by_email=doc.uploaded_by_email,
        r2_key=doc.r2_key,
        document_url=url,
        document_url_expires_in_seconds=_DOC_URL_TTL,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        version=doc.version,
    )


# Contract-category document types (shown in "Upload Contract" form and Contracts & Agreements list)
CONTRACT_DOCUMENT_TYPES: list[OrgDocumentType] = [
    OrgDocumentType.MSA,
    OrgDocumentType.SLA,
    OrgDocumentType.NDA,
    OrgDocumentType.DPA,
    OrgDocumentType.PRICING,
]


def _extract_request_context(request: Request | None) -> dict[str, str | None]:
    if not request:
        return {"ip_address": None, "browser": None, "device": None, "os": None}

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip_address = forwarded.split(",")[0].strip()
    else:
        ip_address = request.client.host if request.client else None

    user_agent_str = request.headers.get("user-agent", "")

    browser = request.headers.get("x-browser")
    device = request.headers.get("x-device")
    os_name = request.headers.get("x-os")

    if user_agent_str and (not browser or not device or not os_name):
        from user_agents import parse

        parsed_ua = parse(user_agent_str)
        if not browser:
            browser = f"{parsed_ua.browser.family} {parsed_ua.browser.version_string}".strip()
        if not device:
            raw_device = f"{parsed_ua.device.family} {parsed_ua.device.brand or ''} {parsed_ua.device.model or ''}".strip()
            device = raw_device if raw_device and raw_device.lower() != "other" else "Desktop"
        if not os_name:
            os_name = f"{parsed_ua.os.family} {parsed_ua.os.version_string}".strip()

    return {
        "ip_address": ip_address,
        "browser": browser,
        "device": device,
        "os": os_name,
    }


class OrgDocumentService(BaseService):
    """CRUD for organisation contract & agreement documents."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = OrgDocumentRepository(session)
        self._activity_repo = OrgDocumentActivityRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._user_repo = UserRepository(session)
        self._audit = AuditService(session, request=request)

    # ── Simple upload (Upload Contract form) ───────────────────────────────────

    async def upload_document(
        self,
        org_id: str,
        file: UploadFile,
        title: str,
        document_type: OrgDocumentType,
        expiry_date,
        admin_user_id: str,
        category: OrgDocumentCategory | None = None,
    ) -> OrgDocumentResponse:
        """Validate file, upload to R2, persist metadata, return presigned URL.

        Simple form: title, document_type, expiry_date are the only required fields.
        Extended classification fields default to null.
        Pass category=CONTRACTS when uploading via the Upload Contract form.
        """
        await self._org_repo.get_by_id_or_404(org_id)

        admin_user = await self._user_repo.get_by_id(admin_user_id)
        admin_email = admin_user.email if admin_user else None

        content, mime_type = await read_and_validate(
            file,
            allowed_types=ALLOWED_ORG_DOCUMENT_TYPES,
            max_size=MAX_ORG_DOCUMENT_SIZE,
            label="Document",
        )

        filename = file.filename or "document"
        key = _make_doc_r2_key(org_id, filename)
        await upload_to_r2(key, content, mime_type)

        doc_reference = await self._repo.generate_reference()
        status = _compute_doc_status(expiry_date)
        doc_payload: dict = {
            "organization_id": org_id,
            "reference": doc_reference,
            "title": title,
            "document_type": document_type,
            "status": status,
            "expiry_date": expiry_date,
            "r2_key": key,
            "uploaded_by": admin_user_id,
            "uploaded_by_email": admin_email,
            "is_active": True,
        }
        if category is not None:
            doc_payload["category"] = category
        doc = await self._repo.create(doc_payload)

        activity_payload = {
            "organization_id": org_id,
            "document_id": doc.id,
            "activity_type": OrgDocumentActivityType.UPLOADED,
            "actor_email": admin_email,
            "actor_role": "Admin",
            "document_name": title,
            "details": "Document uploaded",
        }
        activity_payload.update(_extract_request_context(self._request))
        await self._activity_repo.create(activity_payload)

        await self._audit.log(
            action="org_document.uploaded",
            entity_type="OrgDocument",
            entity_id=doc.id,
            user_id=admin_user_id,
            new_value={"title": title, "document_type": str(document_type), "r2_key": key},
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.DOCUMENT_UPLOADED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
        )
        logger.info("org_document.uploaded", doc_id=doc.id, org_id=org_id, key=key)

        url = generate_document_url(key, expiry_seconds=_DOC_URL_TTL)
        return _build_doc_response(doc, url)

    # ── Full upload (Document Operations form) ────────────────────────────────

    async def upload_document_operations(
        self,
        org_id: str,
        file: UploadFile,
        data: OrgDocumentOperationsRequest,
        admin_user_id: str,
        request: Request | None = None,
    ) -> OrgDocumentResponse:
        """Full document operations upload — all classification and settings fields.

        Validates file, uploads to R2, persists full metadata, logs activity.
        When notify_client=True the caller is responsible for enqueuing the notification.
        """
        await self._org_repo.get_by_id_or_404(org_id)

        admin_user = await self._user_repo.get_by_id(admin_user_id)
        admin_email = admin_user.email if admin_user else None

        content, mime_type = await read_and_validate(
            file,
            allowed_types=ALLOWED_ORG_DOCUMENT_TYPES,
            max_size=MAX_ORG_DOCUMENT_SIZE,
            label="Document",
        )

        filename = file.filename or "document"
        key = _make_doc_r2_key(org_id, filename)
        await upload_to_r2(key, content, mime_type)

        doc_reference = await self._repo.generate_reference()
        status = _compute_doc_status(data.expiry_date)
        doc = await self._repo.create(
            {
                "organization_id": org_id,
                "reference": doc_reference,
                "title": data.title,
                "document_type": data.document_type,
                "category": data.category,
                "status": status,
                "issuing_authority": data.issuing_authority,
                "issue_date": data.issue_date,
                "expiry_date": data.expiry_date,
                "description": data.description,
                "confidentiality_level": data.confidentiality_level,
                "tags": data.tags,
                "r2_key": key,
                "uploaded_by": admin_user_id,
                "uploaded_by_email": admin_email,
                "is_active": True,
            }
        )

        details = "Document uploaded"
        if data.notify_client:
            details = "Document uploaded; client notified"

        activity_payload = {
            "organization_id": org_id,
            "document_id": doc.id,
            "activity_type": OrgDocumentActivityType.UPLOADED,
            "actor_email": admin_email,
            "actor_role": "Admin",
            "document_name": data.title,
            "details": details,
        }
        activity_payload.update(_extract_request_context(self._request))
        await self._activity_repo.create(activity_payload)

        await self._audit.log(
            action="org_document.uploaded",
            entity_type="OrgDocument",
            entity_id=doc.id,
            user_id=admin_user_id,
            new_value={
                "title": data.title,
                "document_type": str(data.document_type),
                "category": str(data.category),
                "r2_key": key,
            },
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.DOCUMENT_UPLOADED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
        )
        logger.info("org_document.uploaded_full", doc_id=doc.id, org_id=org_id, key=key)

        url = generate_document_url(key, expiry_seconds=_DOC_URL_TTL)
        return _build_doc_response(doc, url)

    # ── List ───────────────────────────────────────────────────────────────────

    async def list_documents(
        self,
        org_id: str,
        caller_role: str,
        caller_contact_role: ContactRole | None,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
        category: OrgDocumentCategory | None = None,
        category_in: list[OrgDocumentCategory] | None = None,
        document_type: OrgDocumentType | None = None,
        document_type_in: list[OrgDocumentType] | None = None,
    ) -> tuple[list[OrgDocumentResponse], int, dict]:
        """Return paginated active documents plus unfiltered stats cards.

        Admin: any org. CUSTOMER_B2B: must be a member (caller_contact_role not None).

        Returns (items, total_filtered, stats_dict).
        stats_dict is always computed from the full (unfiltered) document set.
        """
        if not is_platform_admin_role(caller_role) and caller_contact_role is None:
            raise ForbiddenError("You are not a member of this organisation.")

        await self._org_repo.get_by_id_or_404(org_id)

        docs, total = await self._repo.list_by_org(
            org_id,
            page=page,
            size=size,
            search=search,
            category=category,
            category_in=category_in or None,
            document_type=document_type,
            document_type_in=document_type_in or None,
        )
        items = [_build_doc_response(d, generate_document_url(d.r2_key, expiry_seconds=_DOC_URL_TTL)) for d in docs]
        stats = await self._repo.get_document_stats(org_id)
        return items, total, stats

    async def list_contracts(
        self,
        org_id: str,
        caller_role: str,
        caller_contact_role: ContactRole | None,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
        document_type: list[OrgDocumentType] | None = None,
        status: list[OrgDocumentStatus] | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[OrgDocumentResponse], int]:
        """Return paginated contract documents (MSA/SLA/NDA/DPA/PRICING) for an org.

        Always pre-filtered to CONTRACT_DOCUMENT_TYPES. Callers can further filter
        by one or more types, one or more statuses, upload date range, or keyword search.
        Admin: any org. CUSTOMER_B2B: must be a member.
        """
        if not is_platform_admin_role(caller_role) and caller_contact_role is None:
            raise ForbiddenError("You are not a member of this organisation.")

        await self._org_repo.get_by_id_or_404(org_id)

        # Resolve the type filter — must stay within contract types
        effective_type_in: list[OrgDocumentType] = document_type if document_type else CONTRACT_DOCUMENT_TYPES

        docs, total = await self._repo.list_by_org(
            org_id,
            page=page,
            size=size,
            search=search,
            document_type_in=effective_type_in,
            status_in=status if status else None,
            date_from=date_from,
            date_to=date_to,
        )
        items = [_build_doc_response(d, generate_document_url(d.r2_key, expiry_seconds=_DOC_URL_TTL)) for d in docs]
        return items, total

    # ── Get single ─────────────────────────────────────────────────────────────

    async def get_document(
        self,
        org_id: str,
        doc_id: str,
        caller_role: str,
        caller_contact_role: ContactRole | None,
        caller_user_id: str | None = None,
        actor_role_label: str = "User",
        request: Request | None = None,
    ) -> OrgDocumentResponse:
        """Return a fresh presigned URL for one document and log the download."""
        if not is_platform_admin_role(caller_role) and caller_contact_role is None:
            raise ForbiddenError("You are not a member of this organisation.")

        doc = await self._repo.get_active_by_org_and_id(org_id, doc_id)

        actor_email: str | None = None
        if caller_user_id:
            caller_user = await self._user_repo.get_by_id(caller_user_id)
            actor_email = caller_user.email if caller_user else None

        activity_payload = {
            "organization_id": org_id,
            "document_id": doc.id,
            "activity_type": OrgDocumentActivityType.DOWNLOADED,
            "actor_email": actor_email,
            "actor_role": actor_role_label,
            "document_name": doc.title,
            "details": "Document downloaded",
        }
        activity_payload.update(_extract_request_context(self._request))
        await self._activity_repo.create(activity_payload)

        url = generate_document_url(doc.r2_key, expiry_seconds=_DOC_URL_TTL)
        return _build_doc_response(doc, url)

    # ── Update metadata ────────────────────────────────────────────────────────

    async def update_document(
        self,
        org_id: str,
        doc_id: str,
        data: OrgDocumentUpdate,
        admin_user_id: str,
    ) -> OrgDocumentResponse:
        """Update document metadata. Admin only. Recomputes status when expiry_date changes."""
        doc = await self._repo.get_active_by_org_and_id(org_id, doc_id)

        old_value = {
            "title": doc.title,
            "document_type": str(doc.document_type),
            "expiry_date": str(doc.expiry_date),
        }

        updates: dict = {}
        if data.title is not None:
            updates["title"] = data.title
        if data.document_type is not None:
            updates["document_type"] = data.document_type
        if data.category is not None:
            updates["category"] = data.category
        if data.issuing_authority is not None:
            updates["issuing_authority"] = data.issuing_authority
        if data.issue_date is not None:
            updates["issue_date"] = data.issue_date
        if data.expiry_date is not None:
            updates["expiry_date"] = data.expiry_date
        if data.description is not None:
            updates["description"] = data.description
        if data.confidentiality_level is not None:
            updates["confidentiality_level"] = data.confidentiality_level
        if data.tags is not None:
            updates["tags"] = data.tags

        # Recompute status when expiry_date changes
        new_expiry = updates.get("expiry_date", doc.expiry_date)
        updates["status"] = _compute_doc_status(new_expiry)

        doc = await self._repo.update_by_id(doc_id, updates)

        await self._audit.log(
            action="org_document.updated",
            entity_type="OrgDocument",
            entity_id=doc_id,
            user_id=admin_user_id,
            old_value=old_value,
            new_value={k: str(v) for k, v in updates.items()},
            reason=data.reason,
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.DOCUMENT_UPLOADED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
        )
        logger.info("org_document.updated", doc_id=doc_id, org_id=org_id, fields=list(updates.keys()))

        url = generate_document_url(doc.r2_key, expiry_seconds=_DOC_URL_TTL)
        return _build_doc_response(doc, url)

    # ── Delete ─────────────────────────────────────────────────────────────────

    async def delete_document(
        self,
        org_id: str,
        doc_id: str,
        admin_user_id: str,
        request: Request | None = None,
    ) -> None:
        """Soft-delete the DB row and remove the file from R2. Admin only."""
        doc = await self._repo.get_active_by_org_and_id(org_id, doc_id)
        r2_key = doc.r2_key
        doc_title = doc.title

        admin_user = await self._user_repo.get_by_id(admin_user_id)
        admin_email = admin_user.email if admin_user else None

        await self._repo.soft_delete(doc)

        try:
            await delete_from_r2(r2_key)
        except Exception:
            logger.warning("org_document.r2_delete_failed", doc_id=doc_id, key=r2_key)

        activity_payload = {
            "organization_id": org_id,
            "document_id": doc_id,
            "activity_type": OrgDocumentActivityType.DELETED,
            "actor_email": admin_email,
            "actor_role": "Admin",
            "document_name": doc_title,
            "details": "Document deleted",
        }
        activity_payload.update(_extract_request_context(self._request))
        await self._activity_repo.create(activity_payload)

        await self._audit.log(
            action="org_document.deleted",
            entity_type="OrgDocument",
            entity_id=doc_id,
            user_id=admin_user_id,
            old_value={"title": doc_title, "r2_key": r2_key},
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.DOCUMENT_DELETED,
            severity="CRITICAL",
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
        )
        logger.info("org_document.deleted", doc_id=doc_id, org_id=org_id)

    # ── Recent Activity ────────────────────────────────────────────────────────

    async def list_document_activities(
        self,
        org_id: str,
        caller_role: str,
        caller_contact_role: ContactRole | None,
        page: int = 1,
        size: int = 50,
        sort_order: str = "desc",
        activity_types: list[OrgDocumentActivityType] | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        search: str | None = None,
        browser: str | None = None,
    ) -> tuple[list[OrgDocumentActivityResponse], int]:
        """Return paginated recent-activity log for all documents in an org.

        Admin: any org. CUSTOMER_B2B: must be a member.
        Each row includes document_reference (joined from org_documents),
        plus client context fields: ip_address, browser, device, os.
        """
        if not is_platform_admin_role(caller_role) and caller_contact_role is None:
            raise ForbiddenError("You are not a member of this organisation.")

        await self._org_repo.get_by_id_or_404(org_id)
        rows, total = await self._activity_repo.list_by_org(
            org_id,
            page=page,
            size=size,
            sort_order=sort_order,
            activity_types=activity_types or [],
            date_from=date_from,
            date_to=date_to,
            search=search,
            browser=browser,
        )
        items = [OrgDocumentActivityResponse(**r) for r in rows]
        return items, total

    async def list_document_activities_by_document(
        self,
        org_id: str,
        doc_id: str,
        caller_role: str,
        caller_contact_role: ContactRole | None,
        activity_types: list[OrgDocumentActivityType] | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[OrgDocumentActivityResponse]:
        """Return all activity rows for a single document (no pagination).

        Admin: any org. CUSTOMER_B2B: must be a member of the org.
        """
        if not is_platform_admin_role(caller_role) and caller_contact_role is None:
            raise ForbiddenError("You are not a member of this organisation.")

        await self._repo.get_active_by_org_and_id(org_id, doc_id)

        rows = await self._activity_repo.fetch_all_for_export(
            org_id,
            document_id=doc_id,
            activity_types=activity_types or [],
            date_from=date_from,
            date_to=date_to,
        )
        return [OrgDocumentActivityResponse(**r) for r in rows]

    async def export_document_activities_csv(
        self,
        org_id: str,
        caller_role: str,
        caller_contact_role: ContactRole | None,
        document_id: str | None = None,
        activity_types: list[OrgDocumentActivityType] | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        search: str | None = None,
    ) -> str:
        """Return a CSV string of document activity rows for export.

        Admin: any org. CUSTOMER_B2B: must be a member of the org.
        When document_id is provided the export is scoped to that document only.
        """
        import csv
        import io

        if not is_platform_admin_role(caller_role) and caller_contact_role is None:
            raise ForbiddenError("You are not a member of this organisation.")

        await self._org_repo.get_by_id_or_404(org_id)

        if document_id:
            await self._repo.get_active_by_org_and_id(org_id, document_id)

        export_from, export_to = _resolve_doc_activity_export_range(date_from, date_to)
        rows = await self._activity_repo.fetch_all_for_export(
            org_id,
            document_id=document_id,
            activity_types=activity_types or [],
            date_from=export_from,
            date_to=export_to,
            search=search,
            limit=DOC_ACTIVITY_EXPORT_MAX_ROWS + 1,
        )
        if len(rows) > DOC_ACTIVITY_EXPORT_MAX_ROWS:
            raise ValidationError(
                f"Export exceeds the maximum of {DOC_ACTIVITY_EXPORT_MAX_ROWS:,} rows. "
                "Narrow your date range or filters and try again."
            )

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "timestamp",
                "document_reference",
                "document_name",
                "activity_type",
                "actor_email",
                "actor_role",
                "details",
                "ip_address",
                "browser",
                "device",
                "os",
            ],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "timestamp": r["created_at"].strftime("%Y-%m-%d %H:%M:%S UTC") if r["created_at"] else "",
                    "document_reference": r["document_reference"] or "",
                    "document_name": r["document_name"] or "",
                    "activity_type": r["activity_type"].value if hasattr(r["activity_type"], "value") else r["activity_type"],
                    "actor_email": r["actor_email"] or "",
                    "actor_role": r["actor_role"] or "",
                    "details": r["details"] or "",
                    "ip_address": r["ip_address"] or "",
                    "browser": r["browser"] or "",
                    "device": r["device"] or "",
                    "os": r["os"] or "",
                }
            )
        return output.getvalue()


class OrgDocumentShareService(BaseService):
    """Business logic for document sharing."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._share_repo = OrgDocumentShareRepository(session)
        self._doc_repo = OrgDocumentRepository(session)
        self._activity_repo = OrgDocumentActivityRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._user_repo = UserRepository(session)
        self._audit = AuditService(session, request=request)
        from app.modules.organizations.repository import ShareAccessTokenRepository

        self._share_token_repo = ShareAccessTokenRepository(session)

    # ── Share a document ──────────────────────────────────────────────────────

    async def share_document(
        self,
        org_id: str,
        doc_id: str,
        data: OrgDocumentShareCreate,
        admin_user_id: str,
    ) -> OrgDocumentShareResponse:
        """Create a share link, optionally generate a password, enqueue emails."""
        await self._org_repo.get_by_id_or_404(org_id)
        doc = await self._doc_repo.get_active_by_org_and_id(org_id, doc_id)

        admin_user = await self._user_repo.get_by_id(admin_user_id)
        admin_name = None
        if admin_user:
            admin_name = f"{admin_user.first_name or ''} {admin_user.last_name or ''}".strip() or admin_user.email

        otp_required = data.password_protected

        recipient_list = [str(r) for r in data.recipients]

        # Generate share token and build in-app URL
        share_token = secrets.token_hex(32)
        from app.core.config import settings as _settings

        share_url = f"{_settings.FRONTEND_BASE_URL.rstrip('/')}/shared-doc/{share_token}"

        share = await self._share_repo.create(
            {
                "organization_id": org_id,
                "document_id": doc_id,
                "share_token": share_token,
                "recipients": recipient_list,
                "shared_by": admin_user_id,
                "shared_by_name": admin_name,
                "document_title": doc.title,
                "document_reference": doc.reference,
                "expiry_date": data.expiry_date,
                "otp_required": otp_required,
                "message": data.message,
                "status": OrgDocumentShareStatus.ACTIVE,
                "access_count": 0,
            }
        )

        for recipient_email in recipient_list:
            await enqueue(
                Job.SEND_DOCUMENT_SHARE_EMAIL,
                share_id=share.id,
                to_email=recipient_email,
                document_title=doc.title,
                document_reference=doc.reference,
                shared_by_name=admin_name or "SW Couriers Admin",
                share_url=share_url,
                expiry_date=data.expiry_date.isoformat() if data.expiry_date else None,
                message=data.message,
                otp_required=otp_required,
                priority=QueuePriority.DEFAULT,
            )

        await self._audit.log(
            action="org_document.shared",
            entity_type="OrgDocumentShare",
            entity_id=share.id,
            user_id=admin_user_id,
            new_value={
                "document_id": doc_id,
                "recipients": share.recipients,
                "expiry_date": data.expiry_date.isoformat() if data.expiry_date else None,
                "password_protected": data.password_protected,
            },
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.DOCUMENT_UPLOADED,
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
        )

        activity_payload = {
            "organization_id": org_id,
            "document_id": doc_id,
            "activity_type": OrgDocumentActivityType.SHARED,
            "actor_email": admin_name,
            "actor_role": "Admin",
            "document_name": doc.title,
            "details": f"Document shared with {len(share.recipients)} recipient(s)",
        }
        activity_payload.update(_extract_request_context(self._request))
        await self._activity_repo.create(activity_payload)

        logger.info("org_document.shared", share_id=share.id, doc_id=doc_id, org_id=org_id, recipients=len(share.recipients))

        return OrgDocumentShareResponse.from_share(share)

    # ── List sharing history ───────────────────────────────────────────────────

    async def list_shares_for_org(
        self,
        org_id: str,
        caller_role: str,
        caller_contact_role: ContactRole | None,
        page: int = 1,
        size: int = 50,
        status_in: list[OrgDocumentShareStatus] | None = None,
        document_type_in: list[OrgDocumentType] | None = None,
    ) -> tuple[list[OrgDocumentShareResponse], int]:
        """Paginated sharing history for the whole org."""
        if not is_platform_admin_role(caller_role) and caller_contact_role is None:
            raise ForbiddenError("You are not a member of this organisation.")
        await self._org_repo.get_by_id_or_404(org_id)
        shares, total = await self._share_repo.list_by_org(org_id, page=page, size=size, status_in=status_in, document_type_in=document_type_in)
        return [OrgDocumentShareResponse.from_share(s) for s in shares], total

    async def list_shares_for_document(
        self,
        org_id: str,
        doc_id: str,
        caller_role: str,
        caller_contact_role: ContactRole | None,
    ) -> list[OrgDocumentShareResponse]:
        """All shares for a specific document."""
        if not is_platform_admin_role(caller_role) and caller_contact_role is None:
            raise ForbiddenError("You are not a member of this organisation.")
        await self._doc_repo.get_active_by_org_and_id(org_id, doc_id)
        shares = await self._share_repo.list_by_document(org_id, doc_id)
        return [OrgDocumentShareResponse.from_share(s) for s in shares]

    # ── Extend expiry ─────────────────────────────────────────────────────────

    async def extend_expiry(
        self,
        org_id: str,
        share_id: str,
        data: OrgDocumentShareExtendExpiry,
        admin_user_id: str,
    ) -> OrgDocumentShareResponse:
        """Update the expiry date on an existing share."""
        share = await self._share_repo.get_by_id_or_404(share_id)
        if share.organization_id != org_id:
            raise NotFoundError(resource="share", id=share_id)
        if share.status == OrgDocumentShareStatus.REVOKED:
            raise ValidationError("Cannot extend expiry on a revoked share.")

        old_expiry = share.expiry_date
        share = await self._share_repo.update_by_id(
            share_id,
            {
                "expiry_date": data.expiry_date,
                "status": OrgDocumentShareStatus.ACTIVE,
                "status_reason": data.reason,
            },
        )

        await self._audit.log(
            action="org_document_share.expiry_extended",
            entity_type="OrgDocumentShare",
            entity_id=share_id,
            user_id=admin_user_id,
            old_value={"expiry_date": old_expiry.isoformat() if old_expiry else None},
            new_value={"expiry_date": data.expiry_date.isoformat(), "reason": data.reason},
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.DOCUMENT_UPLOADED,
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
        )

        admin_user = await self._user_repo.get_by_id(admin_user_id)
        admin_email = admin_user.email if admin_user else None

        activity_payload = {
            "organization_id": org_id,
            "document_id": share.document_id,
            "activity_type": OrgDocumentActivityType.EXTENDED,
            "actor_email": admin_email,
            "actor_role": "Admin",
            "document_name": share.document_title,
            "details": f"Share expiry extended: {data.reason}",
        }
        activity_payload.update(_extract_request_context(self._request))
        await self._activity_repo.create(activity_payload)

        return OrgDocumentShareResponse.from_share(share)

    # ── Revoke ────────────────────────────────────────────────────────────────

    async def revoke_share(
        self,
        org_id: str,
        share_id: str,
        data: OrgDocumentShareRevoke,
        admin_user_id: str,
    ) -> OrgDocumentShareResponse:
        """Revoke a share — prevents future access via the share token."""
        share = await self._share_repo.get_by_id_or_404(share_id)
        if share.organization_id != org_id:
            raise NotFoundError(resource="share", id=share_id)
        if share.status == OrgDocumentShareStatus.REVOKED:
            raise ValidationError("Share is already revoked.")

        share = await self._share_repo.update_by_id(
            share_id,
            {
                "status": OrgDocumentShareStatus.REVOKED,
                "revoked_at": date.today(),
                "revoked_by": admin_user_id,
                "status_reason": data.reason,
            },
        )

        await self._audit.log(
            action="org_document_share.revoked",
            entity_type="OrgDocumentShare",
            entity_id=share_id,
            user_id=admin_user_id,
            new_value={"reason": data.reason},
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.DOCUMENT_DELETED,
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
        )

        admin_user = await self._user_repo.get_by_id(admin_user_id)
        admin_email = admin_user.email if admin_user else None

        activity_payload = {
            "organization_id": org_id,
            "document_id": share.document_id,
            "activity_type": OrgDocumentActivityType.REVOKED,
            "actor_email": admin_email,
            "actor_role": "Admin",
            "document_name": share.document_title,
            "details": f"Share revoked: {data.reason}",
        }
        activity_payload.update(_extract_request_context(self._request))
        await self._activity_repo.create(activity_payload)

        logger.info("org_document_share.revoked", share_id=share_id, org_id=org_id)
        return OrgDocumentShareResponse.from_share(share)

    # ── Public share access (no auth) ─────────────────────────────────────────

    @staticmethod
    def _normalize_share_recipient_email(email: str) -> str:
        return email.strip().lower()

    def recipient_is_allowed(self, share: OrgDocumentShare, email: str) -> bool:
        """True when email matches an address the document was shared with (case-insensitive)."""
        allowed = {self._normalize_share_recipient_email(str(r)) for r in (share.recipients or [])}
        return self._normalize_share_recipient_email(email) in allowed

    async def get_share_for_public_otp(self, share_token: str) -> OrgDocumentShare:
        """Load share row for public OTP routes; 404 when token is unknown."""
        share = await self._share_repo.get_by_token(share_token)
        if not share:
            raise NotFoundError(resource="shared document", id=share_token)
        return share

    def assert_share_active_for_otp(self, share: OrgDocumentShare) -> None:
        """Raise ForbiddenError when the share link is revoked or past expiry."""
        effective_status = self._effective_share_status(share)
        if effective_status == OrgDocumentShareStatus.REVOKED:
            raise ForbiddenError("This share link has been revoked.")
        if effective_status == OrgDocumentShareStatus.EXPIRED:
            raise ForbiddenError("This share link has expired.")

    def _effective_share_status(self, share) -> OrgDocumentShareStatus:
        """Return the real-time status, accounting for date-based expiry."""
        if share.status == OrgDocumentShareStatus.REVOKED:
            return OrgDocumentShareStatus.REVOKED
        if share.expiry_date and share.expiry_date < date.today():
            return OrgDocumentShareStatus.EXPIRED
        return share.status

    async def get_share_info(
        self,
        share_token: str,
    ) -> SharedDocumentInfoResponse:
        """Step 1 — Return share metadata + otp_required flag.

        Used by the frontend to decide whether to show a password modal.
        Returns info even for expired/revoked shares so the frontend can
        show the appropriate error message.
        """
        share = await self._share_repo.get_by_token(share_token)
        if not share:
            raise NotFoundError(resource="shared document", id=share_token)

        effective_status = self._effective_share_status(share)

        return SharedDocumentInfoResponse(
            share_token=share.share_token,
            document_title=share.document_title,
            document_reference=share.document_reference,
            shared_by_name=share.shared_by_name,
            message=share.message,
            otp_required=share.otp_required,
            status=effective_status,
            expiry_date=share.expiry_date,
        )

    async def _validate_otp_token_for_share(self, share_token: str, share_access_token: str | None) -> None:
        """Raise AuthenticationError when an OTP token is required but missing or invalid."""
        if not share_access_token:
            raise AuthenticationError("OTP verification is required. Request a code via POST /{share_token}/otp/send.")
        row = await self._share_token_repo.find_valid(share_access_token, share_token)
        if row is None:
            raise AuthenticationError("Share access token is missing, invalid, or expired. " "Request a new OTP via POST /{share_token}/otp/send.")
        share = await self._share_repo.get_by_token(share_token)
        if share is None or not self.recipient_is_allowed(share, row.recipient_email):
            raise AuthenticationError("Share access token is missing, invalid, or expired. " "Request a new OTP via POST /{share_token}/otp/send.")

    async def access_shared_document(
        self,
        share_token: str,
        share_access_token: str | None = None,
        request: Request | None = None,
    ) -> SharedDocumentAccessResponse:
        """Step 3 — Generate a presigned URL, log the access, increment counter.

        Validates status/expiry and (when otp_required) the share access token before granting access.
        Creates an OrgDocumentActivity audit row with client context.
        """
        share = await self._share_repo.get_by_token(share_token)
        if not share:
            raise NotFoundError(resource="shared document", id=share_token)

        effective_status = self._effective_share_status(share)
        if effective_status == OrgDocumentShareStatus.REVOKED:
            raise ForbiddenError("This share link has been revoked.")
        if effective_status == OrgDocumentShareStatus.EXPIRED:
            raise ForbiddenError("This share link has expired.")

        # OTP token check (if protected)
        if share.otp_required:
            await self._validate_otp_token_for_share(share_token, share_access_token)

        # Fetch the document to get the R2 key
        doc = await self._doc_repo.get_by_id(share.document_id)
        if not doc or not doc.is_active:
            raise NotFoundError(resource="document", id=str(share.document_id))

        # Generate presigned URL
        document_url = generate_document_url(doc.r2_key, expiry_seconds=_DOC_URL_TTL)

        # Increment access count
        await self._share_repo.increment_access_count(share)

        # Extract client context from request headers (uses user-agent parsing fallback)
        ctx = _extract_request_context(request)
        ip = request.client.host if request and request.client else None

        # Create document activity audit log
        await self._activity_repo.create(
            {
                "organization_id": share.organization_id,
                "document_id": share.document_id,
                "activity_type": OrgDocumentActivityType.VIEWED,
                "actor_email": None,  # external user — no email known
                "actor_role": "External (shared link)",
                "document_name": share.document_title,
                "details": f"Accessed via share link (token: ...{share.share_token[-8:]})",
                **ctx,
            }
        )

        logger.info(
            "shared_document.accessed",
            share_id=share.id,
            doc_id=share.document_id,
            ip=ip,
        )

        return SharedDocumentAccessResponse(
            document_title=share.document_title,
            document_reference=share.document_reference,
            document_url=document_url,
            document_url_expires_in_seconds=_DOC_URL_TTL,
        )

    async def download_shared_document(
        self,
        share_token: str,
        share_access_token: str | None = None,
        request: Request | None = None,
    ) -> SharedDocumentAccessResponse:
        """Return a presigned URL and log a DOWNLOADED activity.

        Validates the share link exactly like access_shared_document but
        records DOWNLOADED instead of VIEWED, indicating the user explicitly
        triggered a file download rather than an inline preview.
        """
        share = await self._share_repo.get_by_token(share_token)
        if not share:
            raise NotFoundError(resource="shared document", id=share_token)

        effective_status = self._effective_share_status(share)
        if effective_status == OrgDocumentShareStatus.REVOKED:
            raise ForbiddenError("This share link has been revoked.")
        if effective_status == OrgDocumentShareStatus.EXPIRED:
            raise ForbiddenError("This share link has expired.")

        if share.otp_required:
            await self._validate_otp_token_for_share(share_token, share_access_token)

        doc = await self._doc_repo.get_by_id(share.document_id)
        if not doc or not doc.is_active:
            raise NotFoundError(resource="document", id=str(share.document_id))

        document_url = generate_document_url(doc.r2_key, expiry_seconds=_DOC_URL_TTL)

        await self._share_repo.increment_access_count(share)

        ctx = _extract_request_context(request)
        await self._activity_repo.create(
            {
                "organization_id": share.organization_id,
                "document_id": share.document_id,
                "activity_type": OrgDocumentActivityType.DOWNLOADED,
                "actor_email": None,
                "actor_role": "External (shared link)",
                "document_name": share.document_title,
                "details": f"Full document download via share link (token: ...{share.share_token[-8:]})",
                **ctx,
            }
        )

        logger.info(
            "shared_document.downloaded",
            share_id=share.id,
            doc_id=share.document_id,
        )

        return SharedDocumentAccessResponse(
            document_title=share.document_title,
            document_reference=share.document_reference,
            document_url=document_url,
            document_url_expires_in_seconds=_DOC_URL_TTL,
        )


class OrgDiscountConfigService(BaseService):
    """CRUD service for per-organisation, per-service-tier discount configuration.

    Each discount is stored as one row per (org, service_tier_id, discount_type).
    Writes are Admin-only; reads are accessible to Admin.
    """

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._discount_repo = OrgDiscountConfigRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._audit = AuditService(session, request=request)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_response(self, org_id: str, rows: list) -> OrgDiscountConfigResponse:
        item_responses = [OrgDiscountConfigItemResponse.model_validate(r) for r in rows]
        return OrgDiscountConfigResponse(organization_id=org_id, discounts=item_responses)

    def _build_row_payload(self, item) -> dict:
        """Build the DB payload for a single OrgDiscountConfigItem."""
        payload: dict = {
            "service_tier_id": item.service_tier_id,
            "discount_type": item.discount_type,
            "is_enabled": item.is_enabled,
            "value": None,
            "valid_from": None,
            "valid_until": None,
            "volume_tiers": None,
        }
        if item.discount_type in (DiscountType.PERCENTAGE, DiscountType.FIXED_PER_BOOKING):
            payload["value"] = item.value
            payload["valid_from"] = item.valid_from
            payload["valid_until"] = item.valid_until
        elif item.discount_type == DiscountType.VOLUME_TIERED and item.volume_tiers:
            payload["volume_tiers"] = _serialize_volume_tiers(item.volume_tiers)
        return payload

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_discount_config(self, org_id: str) -> OrgDiscountConfigResponse | None:
        """Return all discount rows for an org, or None if none configured."""
        await self._org_repo.get_by_id_or_404(org_id)
        rows = await self._discount_repo.list_by_org(org_id)
        if not rows:
            return None
        return self._build_response(org_id, rows)

    # ── Create (inline during org creation) ──────────────────────────────────

    async def create_discount_config(
        self,
        org_id: str,
        data: OrgDiscountConfigInput,
        admin_user_id: str,
    ) -> OrgDiscountConfigResponse:
        """Create all discount rows for a new org.  Used during org creation."""
        org = await self._org_repo.get_by_id_or_404(org_id)
        created = []
        for item in data.discounts:
            payload = self._build_row_payload(item)
            payload["organization_id"] = org_id
            row = await self._discount_repo.create(payload)
            created.append(row)

        ctx = _extract_request_context(self._request)
        await self._audit.log(
            action="org_discount_config.created",
            entity_type="org_discount_config",
            entity_id=org_id,
            entity_ref=org.reference,
            user_id=admin_user_id,
            new_value={"discount_count": len(created)},
            category=AuditCategory.BILLING,
            event_type=AuditEventType.ACCOUNT_UPDATED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
            ip_address=ctx.get("ip_address"),
            user_agent=self._request.headers.get("user-agent") if self._request else None,
        )
        logger.info("org_discount_config.created", org_id=org_id, count=len(created), admin_id=admin_user_id)
        return self._build_response(org_id, created)

    # ── Upsert (PUT — replaces all rows for the org) ──────────────────────────

    async def upsert_discount_config(
        self,
        org_id: str,
        data: OrgDiscountConfigUpsert,
        admin_user_id: str,
    ) -> OrgDiscountConfigResponse:
        """Replace all discount rows for an org in one request.

        Strategy: upsert each (org, tier, type) — create if missing, update if exists.
        reason is mandatory for audit trail.
        """
        org = await self._org_repo.get_by_id_or_404(org_id)

        upserted = []
        for item in data.discounts:
            payload = self._build_row_payload(item)
            existing = await self._discount_repo.get_by_org_tier_type(org_id, item.service_tier_id, item.discount_type)
            if existing is None:
                payload["organization_id"] = org_id
                row = await self._discount_repo.create(payload)
            else:
                row = await self._discount_repo.update_by_id(existing.id, payload)
            upserted.append(row)

        ctx = _extract_request_context(self._request)
        await self._audit.log(
            action="org_discount_config.upserted",
            entity_type="org_discount_config",
            entity_id=org_id,
            entity_ref=org.reference,
            user_id=admin_user_id,
            new_value={"discount_count": len(upserted)},
            reason=data.reason,
            category=AuditCategory.BILLING,
            event_type=AuditEventType.ACCOUNT_UPDATED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
            ip_address=ctx.get("ip_address"),
            user_agent=self._request.headers.get("user-agent") if self._request else None,
        )
        logger.info("org_discount_config.upserted", org_id=org_id, count=len(upserted), admin_id=admin_user_id)
        return self._build_response(org_id, upserted)

    # ── Delete ────────────────────────────────────────────────────────────────

    async def delete_discount_config(self, org_id: str, admin_user_id: str) -> None:
        """Hard-delete ALL discount rows for an org.  Admin only."""
        org = await self._org_repo.get_by_id_or_404(org_id)
        rows = await self._discount_repo.list_by_org(org_id)
        if not rows:
            raise NotFoundError(resource="OrgDiscountConfig", id=org_id)
        await self._discount_repo.delete_all_by_org(org_id)
        ctx = _extract_request_context(self._request)
        await self._audit.log(
            action="org_discount_config.deleted",
            entity_type="org_discount_config",
            entity_id=org_id,
            entity_ref=org.reference,
            user_id=admin_user_id,
            old_value={"discount_count": len(rows)},
            category=AuditCategory.BILLING,
            event_type=AuditEventType.ACCOUNT_UPDATED,
            severity="NOTICE",
            organization_id=org_id,
            user_role=UserRole.ADMIN.value,
            ip_address=ctx.get("ip_address"),
            user_agent=self._request.headers.get("user-agent") if self._request else None,
        )
        logger.info("org_discount_config.deleted", org_id=org_id, count=len(rows), admin_id=admin_user_id)


class OrgDraftService(BaseService):
    """Business logic for the organisation draft-save flow.

    Lifecycle:
      POST  /drafts           → create draft (status=DRAFT, all fields optional)
      PATCH /drafts/{id}      → partial update
      GET   /drafts           → list all drafts
      GET   /drafts/{id}      → single draft detail
      DELETE/drafts/{id}      → hard delete (only while status=DRAFT)
      POST  /drafts/{id}/publish → validate completeness, create contacts, DRAFT→ACTIVE
    """

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._org_repo = OrganizationRepository(session)
        self._draft_repo = OrgDraftRepository(session)
        self._contact_repo = OrgContactRepository(session)
        self._user_repo = UserRepository(session)
        self._pricing_tier_repo = ServiceTierRepository(session)
        self._auth_service = AuthService(session, request=request)
        self._perm_service = PermissionService(session, request=request)
        self._audit = AuditService(session, request=request)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_logo_url(self, logo_cf_image_id: str | None) -> str | None:
        if not logo_cf_image_id:
            return None
        try:
            return generate_image_url(logo_cf_image_id)
        except Exception:
            return None

    def _build_draft_response(self, draft: OrgDraft) -> OrgDraftResponse:
        org = draft.organization
        logo_url = self._get_logo_url(org.logo_cf_image_id)
        return OrgDraftResponse(
            draft_number=draft.draft_number,
            draft_created_by_id=draft.created_by_id,
            draft_created_at=draft.created_at,
            draft_updated_at=draft.updated_at,
            draft_contacts=draft.draft_contacts,
            id=org.id,
            reference=org.reference,
            trading_name=org.trading_name,
            legal_entity_name=org.legal_entity_name,
            industry=org.industry,
            company_size=org.company_size,
            date_of_incorporation=org.date_of_incorporation,
            website=org.website,
            description=org.description,
            phone=org.phone,
            companies_house_number=org.companies_house_number,
            eori_number=org.eori_number,
            vat_number=org.vat_number,
            reg_address_line_1=org.reg_address_line_1,
            reg_address_line_2=org.reg_address_line_2,
            reg_city=org.reg_city,
            reg_state=org.reg_state,
            reg_postcode=org.reg_postcode,
            reg_country=org.reg_country,
            trading_address_line_1=org.trading_address_line_1,
            trading_address_line_2=org.trading_address_line_2,
            trading_address_city=org.trading_address_city,
            trading_address_state=org.trading_address_state,
            trading_address_postcode=org.trading_address_postcode,
            trading_address_country=org.trading_address_country,
            pricing_plans=org.pricing_plans,
            contract_reference=org.contract_reference,
            contract_title=org.contract_title,
            contract_expiry_date=org.contract_expiry_date,
            pricing_agreement_start=org.pricing_agreement_start,
            pricing_agreement_end=org.pricing_agreement_end,
            max_package_weight=org.max_package_weight,
            max_package_length=org.max_package_length,
            max_package_width=org.max_package_width,
            max_package_height=org.max_package_height,
            min_charge_per_booking=org.min_charge_per_booking,
            status=org.status,
            notes=org.notes,
            logo_url=logo_url,
            account_manager_user_id=org.account_manager_user_id,
            secondary_account_manager_user_id=org.secondary_account_manager_user_id,
            additional_account_manager_user_id=org.additional_account_manager_user_id,
            created_at=org.created_at,
            updated_at=org.updated_at,
            version=org.version,
        )

    def _build_draft_list_item(self, draft: OrgDraft) -> OrgDraftListItem:
        org = draft.organization
        return OrgDraftListItem(
            draft_number=draft.draft_number,
            draft_created_by_id=draft.created_by_id,
            draft_created_at=draft.created_at,
            draft_updated_at=draft.updated_at,
            id=org.id,
            reference=org.reference,
            trading_name=org.trading_name,
            legal_entity_name=org.legal_entity_name,
            industry=org.industry,
            company_size=org.company_size,
            status=org.status,
            account_manager_user_id=org.account_manager_user_id,
            created_at=org.created_at,
            updated_at=org.updated_at,
        )

    async def _get_draft_by_identifier(self, identifier: str) -> OrgDraft:
        """Get draft by draft_number (ORG-D-001) or organization_id (UUID).

        Tries draft_number first, then organization_id if UUID-like.
        """
        import uuid as uuid_lib

        # Try draft_number first (handles ORG-D-001 format)
        draft = await self._draft_repo.get_by_draft_number(identifier)
        if draft is not None:
            return draft

        # Try organization_id (UUID format) only if identifier looks like a UUID
        try:
            uuid_lib.UUID(identifier)  # Validate UUID format
            draft = await self._draft_repo.get_by_org_id(identifier)
            if draft is not None:
                return draft
        except (ValueError, TypeError):
            # Not a valid UUID, skip org_id lookup
            pass

        # Not found by either method
        raise NotFoundError(
            resource="org_draft",
            id=identifier,
        )

    async def _get_draft_or_404(self, draft_number: str) -> OrgDraft:
        """Legacy method - use _get_draft_by_identifier for both draft_number and org_id."""
        draft = await self._draft_repo.get_by_draft_number(draft_number)
        if draft is None:
            raise NotFoundError(resource="org_draft", id=draft_number)
        return draft

    async def _apply_draft_fields(self, org_id: str, request: OrgDraftCreateRequest) -> None:
        """Apply all non-None fields from request onto the organizations row."""
        update_data: dict = {}
        raw = request.model_dump(
            exclude={"contacts", "registered_address", "trading_address", "pricing_plans"},
            exclude_none=True,
        )
        update_data.update(raw)

        if request.registered_address is not None:
            update_data = _flatten_address(update_data, request.registered_address, None)

        if request.trading_address is not None:
            update_data = _flatten_address(update_data, None, request.trading_address)

        if request.pricing_plans is not None:
            update_data["pricing_plans"] = await _validate_and_enrich_pricing_plans(request.pricing_plans, self._pricing_tier_repo)

        if update_data:
            await self._org_repo.update_by_id(org_id, update_data)

        # Sync contract lines whenever pricing_plans is explicitly provided (even as []).
        # This mirrors create_org_with_contacts so that effective-for-org returns the correct
        # permitted/is_default state while the org is still in DRAFT status.
        if request.pricing_plans is not None:
            from app.modules.organizations.pricing_plans_contract_sync import (  # noqa: PLC0415
                replace_org_contract_from_pricing_plans,
            )

            stored_plans = update_data.get("pricing_plans") or []
            await replace_org_contract_from_pricing_plans(
                self._session,
                organization_id=org_id,
                plans=list(stored_plans),
            )

    # ── Create ────────────────────────────────────────────────────────────────

    async def create_draft(
        self,
        request: OrgDraftCreateRequest,
        admin_user_id: str,
        logo_file: UploadFile | None = None,
    ) -> OrgDraftResponse:
        """Create a new organisation in DRAFT status."""
        reference = await self._org_repo.generate_reference()

        org = await self._org_repo.create(
            {
                "reference": reference,
                "status": OrganizationStatus.DRAFT,
                "onboarded_by_user_id": admin_user_id,
            }
        )

        # Apply any provided fields
        await self._apply_draft_fields(org.id, request)

        # Upload logo if provided
        if logo_file is not None and logo_file.filename:
            content, _ = await read_and_validate(
                logo_file,
                allowed_types={"image/jpeg", "image/png"},
                max_size=10 * 1024 * 1024,
                label="Logo",
            )
            result = await upload_image(
                content,
                filename=logo_file.filename,
                metadata={"kind": "org_logo", "org_id": org.id},
            )
            await self._org_repo.update_by_id(org.id, {"logo_cf_image_id": result.id})

        draft_number = await self._draft_repo.generate_draft_number()
        draft_contacts = [c.model_dump(mode="json") for c in request.contacts] if request.contacts else None
        draft = await self._draft_repo.create(
            {
                "draft_number": draft_number,
                "organization_id": org.id,
                "created_by_id": admin_user_id,
                "draft_contacts": draft_contacts,
            }
        )

        await self._audit.log(
            action="org_draft.created",
            entity_type="organization",
            entity_id=org.id,
            entity_ref=reference,
            user_id=admin_user_id,
            new_value={"draft_number": draft_number},
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_CREATED,
            severity="NOTICE",
            organization_id=org.id,
            user_role=UserRole.ADMIN.value,
        )

        # Re-fetch with organisation loaded for response
        refreshed = await self._draft_repo.get_by_org_id(org.id)
        assert refreshed is not None
        return self._build_draft_response(refreshed)

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_draft(self, draft_identifier: str) -> OrgDraftResponse:
        """Get draft by draft_number (ORG-D-001) or organization_id (UUID).

        Tries draft_number first, then falls back to organization_id.
        """
        draft = await self._get_draft_by_identifier(draft_identifier)
        return self._build_draft_response(draft)

    async def list_drafts(
        self,
        *,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
    ) -> tuple[list[OrgDraftListItem], int]:
        drafts, total = await self._draft_repo.list_drafts(page=page, size=size, search=search)
        return [self._build_draft_list_item(d) for d in drafts], total

    # ── Update ────────────────────────────────────────────────────────────────

    async def update_draft(
        self,
        draft_identifier: str,
        request: OrgDraftCreateRequest,
        admin_user_id: str,
        logo_file: UploadFile | None = None,
    ) -> OrgDraftResponse:
        draft = await self._get_draft_by_identifier(draft_identifier)
        org = draft.organization

        if org.status != OrganizationStatus.DRAFT:
            raise ValidationError("Only DRAFT-status organisations can be updated via draft endpoints.")

        await self._apply_draft_fields(org.id, request)

        # Upload logo if provided
        if logo_file is not None and logo_file.filename:
            content, _ = await read_and_validate(
                logo_file,
                allowed_types={"image/jpeg", "image/png"},
                max_size=10 * 1024 * 1024,
                label="Logo",
            )
            result = await upload_image(
                content,
                filename=logo_file.filename,
                metadata={"kind": "org_logo", "org_id": org.id},
            )
            await self._org_repo.update_by_id(org.id, {"logo_cf_image_id": result.id})

        # Update draft_contacts if provided
        if request.contacts is not None:
            draft_contacts = [c.model_dump(mode="json") for c in request.contacts]
            await self._draft_repo.update_by_id(draft.id, {"draft_contacts": draft_contacts})

        await self._audit.log(
            action="org_draft.updated",
            entity_type="organization",
            entity_id=org.id,
            entity_ref=org.reference,
            user_id=admin_user_id,
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_UPDATED,
            severity="NOTICE",
            organization_id=org.id,
            user_role=UserRole.ADMIN.value,
        )

        refreshed = await self._draft_repo.get_by_org_id(org.id)
        assert refreshed is not None
        return self._build_draft_response(refreshed)

    # ── Delete ────────────────────────────────────────────────────────────────

    async def delete_draft(self, draft_identifier: str, admin_user_id: str) -> None:
        draft = await self._get_draft_by_identifier(draft_identifier)
        org = draft.organization

        if org.status != OrganizationStatus.DRAFT:
            raise ValidationError("Only DRAFT-status organisations can be deleted via draft endpoints.")

        org_id = org.id
        reference = org.reference

        # Delete logo from Cloudflare Images if present
        if org.logo_cf_image_id:
            try:
                await delete_image(org.logo_cf_image_id)
            except Exception:
                pass

        # Hard delete the org (cascade removes org_drafts row)
        await self._org_repo.hard_delete(org_id)

        await self._audit.log(
            action="org_draft.deleted",
            entity_type="organization",
            entity_id=org_id,
            entity_ref=reference,
            user_id=admin_user_id,
            old_value={"draft_number": draft.draft_number},
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_DEACTIVATED,
            severity="WARNING",
            user_role=UserRole.ADMIN.value,
        )

    # ── Publish ───────────────────────────────────────────────────────────────

    async def publish_draft(
        self,
        draft_identifier: str,
        request: OrgDraftPublishRequest,
        inviter: AuthUser,
        logo_file: UploadFile | None = None,
        contract_file: UploadFile | None = None,
    ) -> CreateOrgWithContactsResponse:
        """Validate completeness, create contact user accounts, transition DRAFT → ACTIVE."""
        draft = await self._get_draft_by_identifier(draft_identifier)
        org = draft.organization

        if org.status != OrganizationStatus.DRAFT:
            raise ValidationError("Organisation is not in DRAFT status.")

        # Resolve contacts — request body overrides draft_contacts JSONB
        if request.contacts:
            raw_contacts: list[dict] = [c.model_dump(mode="json") for c in request.contacts]
        else:
            raw_contacts = list(draft.draft_contacts or [])
        if not raw_contacts:
            raise ValidationError("At least one contact is required to publish an organisation draft.")

        contacts = [OrgDraftContactInput.model_validate(c) for c in raw_contacts]

        if not any(c.contact_role == ContactRole.ACCOUNT_OWNER for c in contacts):
            raise ValidationError("At least one contact must have contact_role='ACCOUNT_OWNER'.")

        # Validate required org fields are all present before committing anything
        required = {
            "trading_name": org.trading_name,
            "legal_entity_name": org.legal_entity_name,
            "industry": org.industry,
            "company_size": org.company_size,
            "date_of_incorporation": org.date_of_incorporation,
            "companies_house_number": org.companies_house_number,
            "reg_address_line_1": org.reg_address_line_1,
            "reg_city": org.reg_city,
            "reg_postcode": org.reg_postcode,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValidationError(f"Cannot publish: missing required fields: {', '.join(missing)}")

        # Check contact emails are unique
        for contact in contacts:
            email = contact.email.strip().lower()
            if await self._user_repo.email_exists(email):
                raise ConflictError(f"User with email '{email}' already exists.")

        # Upload logo if provided
        if logo_file is not None and logo_file.filename:
            content, _ = await read_and_validate(
                logo_file,
                allowed_types={"image/jpeg", "image/png"},
                max_size=10 * 1024 * 1024,
                label="Logo",
            )
            result = await upload_image(
                content,
                filename=logo_file.filename,
                metadata={"kind": "org_logo", "org_id": org.id},
            )
            await self._org_repo.update_by_id(org.id, {"logo_cf_image_id": result.id})
            org.logo_cf_image_id = result.id

        # Upload contract if provided
        contract_url: str | None = None
        if contract_file is not None and contract_file.filename:
            org_svc = OrganizationService(self._session, self._request)
            contract_result = await org_svc.upload_contract(
                org.id,
                contract_file,
                inviter.id,
            )
            await self._org_repo.update_by_id(org.id, {"contract_reference": contract_result.contract_reference})
            contract_url = contract_result.contract_url

        raw_plans = list(org.pricing_plans or [])
        enriched_plans = await _validate_and_enrich_pricing_plans(raw_plans, self._pricing_tier_repo)
        await self._org_repo.update_by_id(org.id, {"pricing_plans": enriched_plans})
        from app.modules.organizations.pricing_plans_contract_sync import replace_org_contract_from_pricing_plans

        await replace_org_contract_from_pricing_plans(
            self._session,
            organization_id=org.id,
            plans=list(enriched_plans),
        )

        # Transition org to ACTIVE
        await self._org_repo.update_by_id(org.id, {"status": OrganizationStatus.ACTIVE})

        # Mark draft as published
        await self._draft_repo.update_by_id(draft.id, {"published_by_id": inviter.id})

        dummy_password = hash_password("INVITED_USER_PLACEHOLDER")
        created_contacts: list[ContactCreatedEntry] = []

        for contact in contacts:
            email = contact.email.strip().lower()
            user = await self._user_repo.create(
                {
                    "email": email,
                    "first_name": contact.first_name,
                    "last_name": contact.last_name,
                    "role": UserRole.CUSTOMER_B2B,
                    "status": UserStatus.PENDING_VERIFICATION,
                    "organization_id": org.id,
                    "password_hash": dummy_password,
                }
            )
            org_contact = await self._contact_repo.create(
                {
                    "organization_id": org.id,
                    "contact_number": contact.contact_number,
                    "contact_role": contact.contact_role,
                    "status": ContactStatus.PENDING,
                    "user_id": user.id,
                }
            )
            await _apply_permission_overrides(
                self._perm_service,
                user.id,
                inviter.id,
                contact.permissions,
                contact_role=contact.contact_role,
            )
            ir = await self._auth_service.create_invite(
                inviter,
                user.id,
                expires_days=1,
                organization_id=org.id,
            )
            if not ir.throttled:
                invite_link = _b2b_invite_email_link(ir.raw_token or "")
                await enqueue(
                    Job.SEND_INVITE_EMAIL,
                    invite_id=ir.public_invite_id,
                    to_email=email,
                    first_name=getattr(user, "first_name", None) or email,
                    invite_link=invite_link,
                    expires_days=1,
                    priority=QueuePriority.DEFAULT,
                )
            created_contacts.append(
                ContactCreatedEntry(
                    contact_id=org_contact.id,
                    user_id=user.id,
                    email=email,
                    contact_role=contact.contact_role,
                    invite_token=ir.raw_token or "",
                )
            )

        # Optionally create payment config on publish
        payment_config_response = None
        if request.payment_config is not None:
            payment_config_response = await OrgPaymentConfigService(self._session, self._request).create_payment_config(
                org_id=org.id,
                data=request.payment_config,
                admin_user_id=inviter.id,
            )

        # Optionally create pickup addresses on publish (same path as create org + contacts)
        pickup_address_responses: list[PickupAddressResponse] | None = None
        if request.pickup_addresses:
            pickup_svc = PickupAddressService(self._session, self._request)
            pickup_address_responses = await pickup_svc.create_addresses_for_organization(
                PickupAddressOwner(organization_id=org.id),
                CreatePickupAddressesRequest(request.pickup_addresses),
                actor_user_id=inviter.id,
            )

        inviter_role = inviter.role if isinstance(inviter.role, str) else str(inviter.role)
        await self._audit.log(
            action="org_draft.published",
            entity_type="organization",
            entity_id=org.id,
            entity_ref=org.reference,
            user_id=inviter.id,
            new_value={"draft_number": draft.draft_number, "contacts": len(created_contacts)},
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_CREATED,
            severity="NOTICE",
            organization_id=org.id,
            user_role=inviter_role,
        )

        # Re-fetch org for up-to-date fields
        org = await self._org_repo.get_by_id_or_404(org.id)
        org_response = OrganizationResponse.model_validate(org)
        org_response.logo_url = self._get_logo_url(org.logo_cf_image_id)

        return CreateOrgWithContactsResponse(
            organization=org_response,
            contacts=created_contacts,
            payment_config=payment_config_response,
            pickup_addresses=pickup_address_responses,
            contract_url=contract_url,
            contract_url_expires_in_seconds=3600 if contract_url else None,
            message=f"Organisation published with {len(created_contacts)} contact(s). Invite(s) sent.",
        )
