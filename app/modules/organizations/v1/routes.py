import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError as PydanticValidationError

from app.common.deps import Allowed, AuditCtxDep, AuthUser, CurrentUserDep, DocAccessDep, SessionDep
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.exceptions import ValidationError as AppValidationError
from app.common.response import ok
from app.common.schemas import MessageResponse, PaginatedResponse, SuccessResponse
from app.core.rate_limit import DOC_OTP_VERIFY_RATE_LIMIT, limiter
from app.modules.auth.service import AuthService
from app.modules.auth.v1.schemas import SupportIssuePasswordRequest, SupportIssuePasswordResponse
from app.modules.org_credit_suspension.service import OrgCreditSuspensionService
from app.modules.org_credit_suspension.v1.schemas import (
    OrgCreditConfigResponse,
    OrgCreditConfigUpsert,
    OrgCreditSuspensionFullResponse,
)
from app.modules.org_discounts.v1.schemas import OrgDiscountConfigResponse, OrgDiscountConfigUpsert
from app.modules.organizations.access import assert_org_profile_access, is_platform_admin_role
from app.modules.organizations.doc_access_service import DocAccessServiceDep
from app.modules.organizations.enums import (
    CompanySize,
    ContactRole,
    IndustryType,
    OrganizationStatus,
    OrgDocumentActivityType,
    OrgDocumentCategory,
    OrgDocumentConfidentialityLevel,
    OrgDocumentShareStatus,
    OrgDocumentStatus,
    OrgDocumentType,
    PaymentModel,
)
from app.modules.organizations.repository import OrgContactRepository
from app.modules.organizations.service import (
    CONTRACT_DOCUMENT_TYPES,
    OrganizationService,
    OrgContactService,
    OrgDiscountConfigService,
    OrgDocumentService,
    OrgDocumentShareService,
    OrgDraftService,
    OrgPaymentConfigService,
)
from app.modules.organizations.v1.docs import (
    CREATE_PICKUP_ADDRESS,
    DELETE_ORG_DOCUMENT,
    DELETE_PICKUP_ADDRESS,
    EXTEND_SHARE_EXPIRY,
    GET_ORG_DOCUMENT,
    GET_ORG_PROFILE,
    GET_ORG_STATS,
    GET_PROFILE_COMPLETION,
    ISSUE_CONTACT_SUPPORT_PASSWORD,
    LIST_DOCUMENT_SHARES,
    LIST_ORG_DOCUMENT_ACTIVITIES,
    LIST_ORG_DOCUMENT_SHARES,
    LIST_ORG_DOCUMENTS,
    LIST_PICKUP_ADDRESSES,
    ORG_CARDS_BRAINTREE_TOKEN,
    ORG_CARDS_CREATE,
    ORG_CARDS_DELETE,
    ORG_CARDS_GET,
    ORG_CARDS_LIST,
    ORG_CARDS_MARK_DEFAULT,
    ORG_CARDS_PREPARE_PAYMENT,
    ORG_CARDS_UNMARK_DEFAULT,
    ORG_PROFILE_PAYLOAD_OPENAPI_EXAMPLES,
    REVOKE_DOCUMENT_SHARE,
    SEND_DOC_OTP,
    SHARE_DOCUMENT,
    UPDATE_ORG_DOCUMENT,
    UPDATE_ORG_LOGO,
    UPDATE_ORG_SELF,
    UPDATE_PICKUP_ADDRESS,
    UPLOAD_ORG_DOCUMENT,
    UPLOAD_ORG_DOCUMENT_OPERATIONS,
    VERIFY_DOC_OTP,
)
from app.modules.organizations.v1.schemas import (
    AccountManagerListResponse,
    AccountManagerResponse,
    AssignAccountManagerRequest,
    BookingServiceTiersResponse,
    CreateOrgWithContactsRequest,
    CreateOrgWithContactsResponse,
    DeactivateOrgRequest,
    DocAccessTokenResponse,
    DocOTPSendResponse,
    DocOTPVerifyRequest,
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
    OrgDocumentExpiringSoonCard,
    OrgDocumentListResponse,
    OrgDocumentOperationsRequest,
    OrgDocumentResponse,
    OrgDocumentShareCreate,
    OrgDocumentShareExtendExpiry,
    OrgDocumentShareResponse,
    OrgDocumentShareRevoke,
    OrgDocumentStats,
    OrgDocumentTotalBreakdown,
    OrgDocumentTotalCard,
    OrgDocumentUpdate,
    OrgDraftCreateRequest,
    OrgDraftListItem,
    OrgDraftPublishRequest,
    OrgDraftResponse,
    OrgPaymentConfigResponse,
    OrgPaymentConfigUpdate,
    OrgPaymentDetailsResponse,
    OrgPaymentMethodCreate,
    OrgPaymentMethodUpdate,
    OrgProfileSavePayload,
    OrgStatsResponse,
    PlaceOnHoldRequest,
    ProfileCompletionResponse,
    ProfileSaveSuccessResponse,
    SuspendOrgRequest,
)
from app.modules.payments.service import CreditCardOwner
from app.modules.payments.v1.routes import PaymentServiceDep
from app.modules.payments.v1.schemas import ClientTokenResponse, CreatePaymentMethodRequest, PaymentMethodResponse, PreparePaymentNonceRequest, PreparePaymentNonceResponse
from app.modules.pickup_addresses.service import PickupAddressService
from app.modules.pickup_addresses.types import PickupAddressOwner
from app.modules.pickup_addresses.v1.schemas import CreatePickupAddressesRequest, PickupAddressResponse, PickupAddressUpdate
from app.modules.service_tiers.enums import ServiceTierAudience
from app.modules.suspension_rules.enums import RuleScopeType, SuspensionRuleStatus, SuspensionRuleType
from app.modules.suspension_rules.service import SuspensionRulesService
from app.modules.suspension_rules.v1.schemas import (
    OrgRuleOverrideUpsertRequest,
    SuspensionRuleConditionV2,
    SuspensionRuleSetListResponse,
    SuspensionRuleSetResponse,
)

router = APIRouter()


def _optional_form_str(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped if stripped else None


def _account_manager_form_fields(
    primary: str | None,
    secondary: str | None,
    additional: str | None,
) -> dict[str, str]:
    """Only include account manager keys when a non-empty UUID was submitted."""
    fields: dict[str, str] = {}
    if primary_id := _optional_form_str(primary):
        fields["account_manager_user_id"] = primary_id
    if secondary_id := _optional_form_str(secondary):
        fields["secondary_account_manager_user_id"] = secondary_id
    if additional_id := _optional_form_str(additional):
        fields["additional_account_manager_user_id"] = additional_id
    return fields


AdminUserDep = Annotated[CurrentUserDep, Allowed(UserRole.ADMIN)]
OrgServiceDep = Annotated[OrganizationService, Depends(OrganizationService.dep)]
ContactServiceDep = Annotated[OrgContactService, Depends(OrgContactService.dep)]
PaymentConfigServiceDep = Annotated[OrgPaymentConfigService, Depends(OrgPaymentConfigService.dep)]
CreditSuspensionServiceDep = Annotated[OrgCreditSuspensionService, Depends(OrgCreditSuspensionService.dep)]
DocServiceDep = Annotated[OrgDocumentService, Depends(OrgDocumentService.dep)]
ShareServiceDep = Annotated[OrgDocumentShareService, Depends(OrgDocumentShareService.dep)]
PickupAddressServiceDep = Annotated[PickupAddressService, Depends(PickupAddressService.dep)]
DiscountConfigServiceDep = Annotated[OrgDiscountConfigService, Depends(OrgDiscountConfigService.dep)]
SuspensionRulesServiceDep = Annotated[SuspensionRulesService, Depends(SuspensionRulesService.dep)]
OrgDraftServiceDep = Annotated[OrgDraftService, Depends(OrgDraftService.dep)]

# ADMIN/SUPER_ADMIN or any CUSTOMER_B2B (further org-ownership check happens in service)
OrgMemberDep = Annotated[CurrentUserDep, Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.CUSTOMER_B2B)]
B2bSupportPasswordResetDep = Annotated[
    CurrentUserDep,
    Allowed(
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
        resource=Resource.RESET_B2B_CLIENT_PASSWORDS,
        level=PermissionLevel.WRITE,
    ),
]
AuthServiceDep = Annotated[AuthService, Depends(AuthService.dep)]


async def _caller_contact_role(
    org_id: str,
    caller: OrgMemberDep,
    session: SessionDep,
) -> ContactRole | None:
    """Resolve the caller's ContactRole within this org (None for platform admin callers)."""
    if is_platform_admin_role(caller.role):
        return None
    repo = OrgContactRepository(session)
    return await repo.get_contact_role_for_user(org_id, caller.id)


CallerContactRoleDep = Annotated[ContactRole | None, Depends(_caller_contact_role)]


async def _require_org_profile_read(
    org_id: str,
    caller: OrgMemberDep,
    session: SessionDep,
    caller_contact_role: CallerContactRoleDep,
) -> AuthUser:
    await assert_org_profile_access(session, caller, org_id, caller_contact_role, PermissionLevel.READ)
    return caller


async def _require_org_profile_write(
    org_id: str,
    caller: OrgMemberDep,
    session: SessionDep,
    caller_contact_role: CallerContactRoleDep,
) -> AuthUser:
    await assert_org_profile_access(session, caller, org_id, caller_contact_role, PermissionLevel.WRITE)
    return caller


OrgProfileReadUserDep = Annotated[AuthUser, Depends(_require_org_profile_read)]
OrgProfileWriteUserDep = Annotated[AuthUser, Depends(_require_org_profile_write)]


# ── Draft Save ────────────────────────────────────────────────────────────────
# All /drafts routes must appear before /{org_id} to avoid path conflicts.


@router.post(
    "/drafts",
    response_model=SuccessResponse[OrgDraftResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_org_draft(
    admin: AdminUserDep,
    draft_service: OrgDraftServiceDep,
    trading_name: str | None = Form(None, max_length=255),
    legal_entity_name: str | None = Form(None, max_length=255),
    industry: IndustryType | None = Form(None),
    company_size: CompanySize | None = Form(None),
    date_of_incorporation: date | None = Form(None),
    companies_house_number: str | None = Form(None, max_length=100),
    vat_number: str | None = Form(None, max_length=50),
    eori_number: str | None = Form(None, max_length=100),
    website: str | None = Form(None, max_length=500),
    description: str | None = Form(None, max_length=500),
    phone: str | None = Form(None, max_length=50),
    notes: str | None = Form(None),
    reg_address_line_1: str | None = Form(None, max_length=255),
    reg_address_line_2: str | None = Form(None, max_length=255),
    reg_city: str | None = Form(None, max_length=100),
    reg_state: str | None = Form(None, max_length=100),
    reg_postcode: str | None = Form(None, max_length=20),
    reg_country: str = Form("United Kingdom", max_length=100),
    trading_address_line_1: str | None = Form(None, max_length=255),
    trading_address_line_2: str | None = Form(None, max_length=255),
    trading_address_city: str | None = Form(None, max_length=100),
    trading_address_state: str | None = Form(None, max_length=100),
    trading_address_postcode: str | None = Form(None, max_length=20),
    trading_address_country: str | None = Form(None, max_length=100),
    account_manager_user_id: str | None = Form(None),
    secondary_account_manager_user_id: str | None = Form(None),
    additional_account_manager_user_id: str | None = Form(None),
    pricing_plans: str | None = Form(None, description="JSON array of pricing plan objects"),
    pricing_agreement_start: date | None = Form(None),
    pricing_agreement_end: date | None = Form(None),
    max_package_weight: float | None = Form(None),
    max_package_length: float | None = Form(None),
    max_package_width: float | None = Form(None),
    max_package_height: float | None = Form(None),
    min_charge_per_booking: Decimal | None = Form(None),
    contacts: str | None = Form(None, description="JSON array of contact objects (optional at draft stage)"),
    logo_file: UploadFile | None = File(None),
) -> dict:
    """Save a new organisation draft. All fields optional — save progress at any stage. Admin only."""

    def _parse_json(value: str | None, field_name: str):
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            raise AppValidationError(f"{field_name} must be a valid JSON string.")

    parsed = OrgDraftCreateRequest.model_validate({
        "trading_name": trading_name,
        "legal_entity_name": legal_entity_name,
        "industry": industry,
        "company_size": company_size,
        "date_of_incorporation": date_of_incorporation,
        "companies_house_number": companies_house_number,
        "vat_number": vat_number,
        "eori_number": eori_number,
        "website": website,
        "description": description,
        "phone": phone,
        "notes": notes,
        "registered_address": {
            "address_line_1": reg_address_line_1,
            "address_line_2": reg_address_line_2,
            "city": reg_city,
            "state": reg_state,
            "postcode": reg_postcode,
            "country": reg_country,
        } if reg_address_line_1 else None,
        "trading_address": {
            "address_line_1": trading_address_line_1,
            "address_line_2": trading_address_line_2,
            "city": trading_address_city,
            "state": trading_address_state,
            "postcode": trading_address_postcode,
            "country": trading_address_country,
        } if trading_address_line_1 else None,
        "account_manager_user_id": account_manager_user_id,
        "secondary_account_manager_user_id": secondary_account_manager_user_id,
        "additional_account_manager_user_id": additional_account_manager_user_id,
        "pricing_plans": _parse_json(pricing_plans, "pricing_plans"),
        "pricing_agreement_start": pricing_agreement_start,
        "pricing_agreement_end": pricing_agreement_end,
        "max_package_weight": max_package_weight,
        "max_package_length": max_package_length,
        "max_package_width": max_package_width,
        "max_package_height": max_package_height,
        "min_charge_per_booking": min_charge_per_booking,
        "contacts": _parse_json(contacts, "contacts"),
    })
    result = await draft_service.create_draft(parsed, admin_user_id=admin.id, logo_file=logo_file)
    return ok(result)


@router.get(
    "/drafts",
    response_model=SuccessResponse[PaginatedResponse[OrgDraftListItem]],
)
async def list_org_drafts(
    request: Request,
    admin: AdminUserDep,
    draft_service: OrgDraftServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str | None, Query(description="Search by draft number, trading name, or reference")] = None,
) -> dict:
    """List all organisation drafts. Admin only."""
    items, total = await draft_service.list_drafts(page=page, size=size, search=search)
    paginated = PaginatedResponse[OrgDraftListItem].create(items=items, total=total, page=page, size=size, request=request)
    return ok(paginated)


@router.get(
    "/drafts/{draft_identifier}",
    response_model=SuccessResponse[OrgDraftResponse],
)
async def get_org_draft(
    draft_identifier: str,
    admin: AdminUserDep,
    draft_service: OrgDraftServiceDep,
) -> dict:
    """Get a single organisation draft by draft_number (ORG-D-001) or organization_id (UUID). Admin only."""
    result = await draft_service.get_draft(draft_identifier)
    return ok(result)


@router.patch(
    "/drafts/{draft_identifier}",
    response_model=SuccessResponse[OrgDraftResponse],
)
async def update_org_draft(
    draft_identifier: str,
    admin: AdminUserDep,
    draft_service: OrgDraftServiceDep,
    trading_name: str | None = Form(None, max_length=255),
    legal_entity_name: str | None = Form(None, max_length=255),
    industry: IndustryType | None = Form(None),
    company_size: CompanySize | None = Form(None),
    date_of_incorporation: date | None = Form(None),
    companies_house_number: str | None = Form(None, max_length=100),
    vat_number: str | None = Form(None, max_length=50),
    eori_number: str | None = Form(None, max_length=100),
    website: str | None = Form(None, max_length=500),
    description: str | None = Form(None, max_length=500),
    phone: str | None = Form(None, max_length=50),
    notes: str | None = Form(None),
    reg_address_line_1: str | None = Form(None, max_length=255),
    reg_address_line_2: str | None = Form(None, max_length=255),
    reg_city: str | None = Form(None, max_length=100),
    reg_state: str | None = Form(None, max_length=100),
    reg_postcode: str | None = Form(None, max_length=20),
    reg_country: str | None = Form(None, max_length=100),
    trading_address_line_1: str | None = Form(None, max_length=255),
    trading_address_line_2: str | None = Form(None, max_length=255),
    trading_address_city: str | None = Form(None, max_length=100),
    trading_address_state: str | None = Form(None, max_length=100),
    trading_address_postcode: str | None = Form(None, max_length=20),
    trading_address_country: str | None = Form(None, max_length=100),
    account_manager_user_id: str | None = Form(None),
    secondary_account_manager_user_id: str | None = Form(None),
    additional_account_manager_user_id: str | None = Form(None),
    pricing_plans: str | None = Form(None),
    pricing_agreement_start: date | None = Form(None),
    pricing_agreement_end: date | None = Form(None),
    max_package_weight: float | None = Form(None),
    max_package_length: float | None = Form(None),
    max_package_width: float | None = Form(None),
    max_package_height: float | None = Form(None),
    min_charge_per_booking: Decimal | None = Form(None),
    contacts: str | None = Form(None, description="JSON array — replaces all stored draft contacts"),
    logo_file: UploadFile | None = File(None),
) -> dict:
    """Partially update an organisation draft. All fields optional. Admin only."""

    def _parse_json(value: str | None, field_name: str):
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            raise AppValidationError(f"{field_name} must be a valid JSON string.")

    parsed = OrgDraftCreateRequest.model_validate({
        "trading_name": trading_name,
        "legal_entity_name": legal_entity_name,
        "industry": industry,
        "company_size": company_size,
        "date_of_incorporation": date_of_incorporation,
        "companies_house_number": companies_house_number,
        "vat_number": vat_number,
        "eori_number": eori_number,
        "website": website,
        "description": description,
        "phone": phone,
        "notes": notes,
        "registered_address": {
            "address_line_1": reg_address_line_1,
            "address_line_2": reg_address_line_2,
            "city": reg_city,
            "state": reg_state,
            "postcode": reg_postcode,
            "country": reg_country,
        } if reg_address_line_1 else None,
        "trading_address": {
            "address_line_1": trading_address_line_1,
            "address_line_2": trading_address_line_2,
            "city": trading_address_city,
            "state": trading_address_state,
            "postcode": trading_address_postcode,
            "country": trading_address_country,
        } if trading_address_line_1 else None,
        "account_manager_user_id": account_manager_user_id,
        "secondary_account_manager_user_id": secondary_account_manager_user_id,
        "additional_account_manager_user_id": additional_account_manager_user_id,
        "pricing_plans": _parse_json(pricing_plans, "pricing_plans"),
        "pricing_agreement_start": pricing_agreement_start,
        "pricing_agreement_end": pricing_agreement_end,
        "max_package_weight": max_package_weight,
        "max_package_length": max_package_length,
        "max_package_width": max_package_width,
        "max_package_height": max_package_height,
        "min_charge_per_booking": min_charge_per_booking,
        "contacts": _parse_json(contacts, "contacts"),
    })
    result = await draft_service.update_draft(draft_identifier, parsed, admin_user_id=admin.id, logo_file=logo_file)
    return ok(result)


@router.delete(
    "/drafts/{draft_identifier}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_org_draft(
    draft_identifier: str,
    admin: AdminUserDep,
    draft_service: OrgDraftServiceDep,
) -> dict:
    """Hard-delete an organisation draft. Only allowed while status=DRAFT. Admin only.

    Identifier can be draft_number (ORG-D-001) or organization_id (UUID).
    """
    await draft_service.delete_draft(draft_identifier, admin_user_id=admin.id)
    return ok(message="Organisation draft deleted.")


@router.post(
    "/drafts/{draft_identifier}/publish",
    response_model=SuccessResponse[CreateOrgWithContactsResponse],
    status_code=status.HTTP_201_CREATED,
)
async def publish_org_draft(
    draft_identifier: str,
    admin: AdminUserDep,
    draft_service: OrgDraftServiceDep,
    body: str | None = Form(None, description="JSON object matching OrgDraftPublishRequest"),
    logo_file: UploadFile | None = File(None),
    contract_file: UploadFile | None = File(None),
) -> dict:
    """Validate completeness, create contact accounts, and transition DRAFT → ACTIVE. Admin only.

    Identifier can be draft_number (ORG-D-001) or organization_id (UUID).

    Send as multipart/form-data:
    - body: JSON string of OrgDraftPublishRequest (contacts, optional payment_config, pickup_addresses)
    - logo_file: optional logo upload
    - contract_file: optional contract PDF upload
    """
    publish_request: OrgDraftPublishRequest
    if body:
        try:
            publish_request = OrgDraftPublishRequest.model_validate(json.loads(body))
        except json.JSONDecodeError:
            raise AppValidationError("body must be a valid JSON object.")
        except PydanticValidationError as exc:
            raise AppValidationError("body must be a valid JSON object matching OrgDraftPublishRequest.") from exc
    else:
        publish_request = OrgDraftPublishRequest()

    result = await draft_service.publish_draft(
        draft_identifier,
        publish_request,
        inviter=admin,
        logo_file=logo_file,
        contract_file=contract_file,
    )
    return ok(result)


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=SuccessResponse[CreateOrgWithContactsResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_org_with_contacts(  # noqa: PLR0913
    admin: AdminUserDep,
    org_service: OrgServiceDep,
    trading_name: str = Form(..., min_length=2, max_length=255, description="Trading / brand name of the organisation"),
    legal_entity_name: str = Form(..., max_length=255, description="Full registered legal name"),
    industry: IndustryType = Form(..., description="Industry type enum value"),
    company_size: CompanySize = Form(..., description="Company size band enum value"),
    date_of_incorporation: date = Form(..., description="ISO 8601 date — e.g. 2020-01-15"),
    companies_house_number: str = Form(..., max_length=100, description="Companies House registration number"),
    vat_number: str | None = Form(None, max_length=50, description="VAT registration number — e.g. GB123456789"),
    reg_address_line_1: str = Form(..., max_length=255, description="First line of registered address"),
    reg_city: str = Form(..., max_length=100, description="City of registered address"),
    reg_postcode: str = Form(..., max_length=20, description="Postcode of registered address"),
    website: str | None = Form(None, max_length=500, description="Public website URL"),
    description: str | None = Form(None, max_length=500, description="Short description of the organisation"),
    phone: str | None = Form(None, max_length=50, description="Main contact phone number"),
    eori_number: str | None = Form(None, max_length=100, description="EORI number for customs"),
    pricing_agreement_start: date | None = Form(None, description="ISO 8601 date — start of pricing agreement"),
    pricing_agreement_end: date | None = Form(None, description="ISO 8601 date — end of pricing agreement"),
    max_package_weight: float | None = Form(None, description="Maximum package weight in kg"),
    max_package_length: float | None = Form(None, description="Maximum package length in cm"),
    max_package_width: float | None = Form(None, description="Maximum package width in cm"),
    max_package_height: float | None = Form(None, description="Maximum package height in cm"),
    min_charge_per_booking: Decimal | None = Form(None, description="Minimum charge per booking in GBP — e.g. 5.00"),
    notes: str | None = Form(None, description="Internal admin notes"),
    reg_address_line_2: str | None = Form(None, max_length=255, description="Second line of registered address"),
    reg_state: str | None = Form(None, max_length=100, description="County / state of registered address"),
    reg_country: str = Form("United Kingdom", max_length=100, description="Country — defaults to 'United Kingdom'"),
    trading_address_line_1: str | None = Form(None, max_length=255, description="First line of trading address (omit if same as registered)"),
    trading_address_line_2: str | None = Form(None, max_length=255, description="Second line of trading address"),
    trading_address_city: str | None = Form(None, max_length=100, description="City of trading address"),
    trading_address_state: str | None = Form(None, max_length=100, description="County / state of trading address"),
    trading_address_postcode: str | None = Form(None, max_length=20, description="Postcode of trading address"),
    trading_address_country: str | None = Form(None, max_length=100, description="Country of trading address"),
    account_manager_user_id: str | None = Form(None, description="UUID of the primary account manager admin user (optional)"),
    secondary_account_manager_user_id: str | None = Form(None, description="UUID of the secondary account manager admin user (optional)"),
    additional_account_manager_user_id: str | None = Form(None, description="UUID of the additional account manager admin user (optional)"),
    contacts: str = Form(..., description="JSON array of contact objects; at least one must have contact_role='ACCOUNT_OWNER'"),
    pricing_plans: str | None = Form(None, description="JSON array of pricing plan objects (optional)"),
    payment_config: str | None = Form(None, description="JSON object for payment configuration (optional)"),
    credit_config: str | None = Form(None, description="JSON object for credit configuration (optional)"),
    suspension_config: str | None = Form(None, description="JSON object for suspension configuration (optional)"),
    discount_config: str | None = Form(None, description="JSON object for discount configuration (optional)"),
    pickup_addresses: str | None = Form(None, description="JSON array of pickup address objects (optional)"),
    contract_file: UploadFile | None = File(None, description="Signed contract PDF (optional, max 10 MB)"),
    contract_title: str | None = Form(None, max_length=255, description="Title for the uploaded contract PDF."),
    contract_expiry_date: date | None = Form(None, description="Contract expiry date (ISO 8601, e.g. 2027-12-31)."),
    logo_file: UploadFile | None = File(None, description="Company logo — JPEG or PNG, max 10 MB (optional)"),
) -> dict:
    """Create an organisation with contacts via multipart/form-data. Admin only."""
    try:
        contacts_data = json.loads(contacts)
    except json.JSONDecodeError:
        raise AppValidationError("contacts must be a valid JSON array string.")

    def _parse_optional_json(value: str | None, field_name: str) -> dict | list | None:
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            raise AppValidationError(f"{field_name} must be a valid JSON string.")

    parsed = CreateOrgWithContactsRequest.model_validate(
        {
            "organization": {
                "trading_name": trading_name,
                "legal_entity_name": legal_entity_name,
                "industry": industry,
                "company_size": company_size,
                "date_of_incorporation": date_of_incorporation,
                "companies_house_number": companies_house_number,
                "vat_number": vat_number,
                "registered_address": {
                    "address_line_1": reg_address_line_1,
                    "address_line_2": reg_address_line_2,
                    "city": reg_city,
                    "state": reg_state,
                    "postcode": reg_postcode,
                    "country": reg_country,
                },
                "trading_address": {
                    "address_line_1": trading_address_line_1,
                    "address_line_2": trading_address_line_2,
                    "city": trading_address_city,
                    "state": trading_address_state,
                    "postcode": trading_address_postcode,
                    "country": trading_address_country,
                } if trading_address_line_1 else None,
                "website": website,
                "description": description,
                "phone": phone,
                "eori_number": eori_number,
                **_account_manager_form_fields(
                    account_manager_user_id,
                    secondary_account_manager_user_id,
                    additional_account_manager_user_id,
                ),
                "pricing_plans": _parse_optional_json(pricing_plans, "pricing_plans"),
                "pricing_agreement_start": pricing_agreement_start,
                "pricing_agreement_end": pricing_agreement_end,
                "max_package_weight": max_package_weight,
                "max_package_length": max_package_length,
                "max_package_width": max_package_width,
                "max_package_height": max_package_height,
                "min_charge_per_booking": min_charge_per_booking,
                "notes": notes,
            },
            "contacts": contacts_data,
            "payment_config": _parse_optional_json(payment_config, "payment_config"),
            "credit_config": _parse_optional_json(credit_config, "credit_config"),
            "suspension_config": _parse_optional_json(suspension_config, "suspension_config"),
            "discount_config": _parse_optional_json(discount_config, "discount_config"),
            "pickup_addresses": _parse_optional_json(pickup_addresses, "pickup_addresses"),
        }
    )
    result = await org_service.create_org_with_contacts(
        parsed,
        inviter=admin,
        contract_file=contract_file,
        contract_title=contract_title,
        contract_expiry_date=contract_expiry_date,
        logo_file=logo_file,
    )
    return ok(result)


# ── List ──────────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=SuccessResponse[PaginatedResponse[OrganizationListItemResponse]],
)
async def list_organizations(
    request: Request,
    caller: OrgMemberDep,
    org_service: OrgServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str | None, Query(description="Search by client ID, name, industry, account manager, or owner email")] = None,
    status: Annotated[list[OrganizationStatus], Query(description="Filter by one or more statuses: ACTIVE, INACTIVE, ON_HOLD, SUSPENDED (DRAFT excluded — use GET /organizations/drafts)")] = [],
    vat_registered: Annotated[bool | None, Query(description="Filter by VAT registered (true/false)")] = None,
    pricing_type: Annotated[str | None, Query(description="Filter by pricing type: standard or custom")] = None,
    payment_model: Annotated[list[PaymentModel], Query(description="Filter by one or more payment models: CARD, BANK_TRANSFER, CREDIT_ACCOUNT, CASH")] = [],
    onboarded_by_user_id: Annotated[list[str], Query(description="Filter by one or more admin user IDs who onboarded the org")] = [],
    created_from: Annotated[date | None, Query(description="Filter orgs created on or after this date (YYYY-MM-DD)")] = None,
    created_to: Annotated[date | None, Query(description="Filter orgs created on or before this date (YYYY-MM-DD)")] = None,
    sort: Annotated[str, Query(description="Sort order: newest (default) or oldest")] = "newest",
) -> dict:
    """List organizations; ADMIN/SUPER_ADMIN see all with filters, CUSTOMER_B2B sees only their own.

    DRAFT-status orgs are excluded; use GET /organizations/drafts for draft clients.
    """
    items, total = await org_service.list_organizations(
        page=page,
        size=size,
        search=search,
        status=status or None,
        vat_registered=vat_registered,
        pricing_type=pricing_type,
        payment_model=payment_model or None,
        onboarded_by_user_id=onboarded_by_user_id or None,
        created_from=created_from,
        created_to=created_to,
        sort=sort,
        caller=caller,
    )
    paginated = PaginatedResponse[OrganizationListItemResponse].create(items=items, total=total, page=page, size=size, request=request)
    return ok(paginated)


# ── Organization Statistics ───────────────────────────────────────────────────


@router.get(
    "/stats",
    response_model=SuccessResponse[OrgStatsResponse],
    **GET_ORG_STATS,
)
async def get_org_stats(
    admin: AdminUserDep,
    org_service: OrgServiceDep,
) -> dict:
    """Get B2B client organization statistics."""
    stats = await org_service.get_org_stats()
    return ok(data=OrgStatsResponse(**stats))


# ── Account managers list ─────────────────────────────────────────────────────
# Must be registered before /{org_id} routes to avoid FastAPI routing
# the literal path segment "account-managers" as an org UUID.


@router.get(
    "/account-managers",
    response_model=SuccessResponse[AccountManagerListResponse],
)
async def list_account_managers(
    request: Request,
    admin: AdminUserDep,
    org_service: OrgServiceDep,
    search: str | None = Query(None, description="Filter by name or email"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
) -> dict:
    """List admin users eligible to be assigned as account managers. Admin only."""
    managers, total = await org_service.list_account_managers(search=search, page=page, size=size)
    paginated = PaginatedResponse[AccountManagerResponse].create(items=managers, total=total, page=page, size=size, request=request)
    return ok(AccountManagerListResponse(**paginated.model_dump()))


# ── Read single ───────────────────────────────────────────────────────────────


@router.get(
    "/{org_id}/booking-service-tiers",
    response_model=SuccessResponse[BookingServiceTiersResponse],
)
async def get_booking_service_tiers(
    org_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    org_service: OrgServiceDep,
    available_for: ServiceTierAudience = Query(
        default=ServiceTierAudience.CUSTOMER_B2B,
        description="Audience filter: CUSTOMER_B2B | CUSTOMER_B2C (BOTH tiers match either).",
    ),
) -> dict:
    """Resolved service tiers for booking: org contract (permitted/default) or global catalog fallback."""
    data = await org_service.get_booking_service_tiers(
        org_id,
        available_for=available_for.value,
        caller=caller,
        caller_contact_role=caller_contact_role,
    )
    return ok(data=data)


@router.get(
    "/{org_id}",
    response_model=SuccessResponse[OrganizationResponse],
)
async def get_organization(
    org_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    org_service: OrgServiceDep,
) -> dict:
    """Get organization details by ID.

    - ADMIN or SUPER_ADMIN: any org (no org_contacts row required).
    - CUSTOMER_B2B: only their own org (membership verified via org_contacts).
    """
    result = await org_service.get_organization(org_id, caller_role=caller.role, caller_contact_role=caller_contact_role)
    return ok(result)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch(
    "/{org_id}",
    response_model=SuccessResponse[OrganizationUpdateResponse],
)
async def update_organization_admin(
    org_id: str,
    data: OrganizationUpdate,
    admin: AdminUserDep,
    org_service: OrgServiceDep,
) -> dict:
    """Update organization details. Admin only. reason is mandatory.

    payment_config is optional — when provided it is updated atomically
    with the org fields and returned in the same response.
    """
    result = await org_service.update_organization(org_id, data, caller=admin, caller_contact_role=None)
    return ok(result)


@router.get(
    "/{org_id}/profile",
    response_model=ProfileSaveSuccessResponse,
    response_model_exclude_none=False,
    **GET_ORG_PROFILE,
)
async def get_organization_profile(
    org_id: str,
    caller: OrgProfileReadUserDep,
    caller_contact_role: CallerContactRoleDep,
    org_service: OrgServiceDep,
    pickup_service: PickupAddressServiceDep,
) -> dict:
    """Full B2B profile for one screen: organisation row + pickup addresses (same shape as PATCH /profile)."""
    organization = await org_service.get_organization(
        org_id, caller_role=caller.role, caller_contact_role=caller_contact_role
    )
    pickup_owner = PickupAddressOwner(organization_id=org_id)
    pickup_addresses = await pickup_service.list_for_organization(pickup_owner)
    return ok({"organization": organization, "pickup_addresses": pickup_addresses})


@router.patch(
    "/{org_id}/profile",
    response_model=ProfileSaveSuccessResponse,
    response_model_exclude_none=False,
    **UPDATE_ORG_SELF,
)
async def update_organization_self(
    org_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    org_service: OrgServiceDep,
    pickup_service: PickupAddressServiceDep,
    payload: Annotated[
        str,
        Form(
            ...,
            description=(
                "JSON string for `OrgProfileSavePayload`: optional B2B profile fields including `registered_address`, "
                "`trading_address` or `trading_same_as_registered_address`, `eori_number`, `vat_number`, "
                "`pickup_addresses`, etc. Response returns flat `reg_*` and `trading_address_*` on `organization`."
            ),
            openapi_examples=ORG_PROFILE_PAYLOAD_OPENAPI_EXAMPLES,
        ),
    ],
    logo: Annotated[UploadFile | None, File(description="Optional organisation logo — JPEG or PNG, max 2 MB")] = None,
) -> dict:
    """Update own organisation profile and optional logo in a single request.

    Multipart form:
    - payload: JSON profile object
    - logo: optional image file
    """
    data = OrgProfileSavePayload.model_validate_json(payload)
    result = await org_service.update_profile_full(
        org_id,
        data,
        caller=caller,
        caller_contact_role=caller_contact_role,
        logo=logo,
    )
    pickup_owner = PickupAddressOwner(organization_id=org_id)
    pickup_addresses = await pickup_service.list_for_organization(pickup_owner)
    return ok(
        {
            "organization": result.organization,
            "pickup_addresses": pickup_addresses,
        }
    )


# ── Logo ──────────────────────────────────────────────────────────────────────


@router.patch(
    "/{org_id}/logo",
    response_model=SuccessResponse[OrganizationResponse],
    **UPDATE_ORG_LOGO,
)
async def update_org_logo(
    org_id: str,
    caller: OrgProfileWriteUserDep,
    org_service: OrgServiceDep,
    logo: Annotated[UploadFile, File(description="Organisation logo — JPEG or PNG, max 2 MB")],
) -> dict:
    """Upload or replace the logo for an organisation.

    Accepts JPEG/PNG up to 2 MB. The image is stored via Cloudflare Images and
    a signed CDN URL is returned in the response. Admin or B2B with ORG_PROFILE WRITE / owner.
    """
    result = await org_service.update_logo(org_id, logo, actor_user_id=caller.id)
    return ok(result)


@router.get(
    "/{org_id}/payment-details",
    response_model=SuccessResponse[OrgPaymentDetailsResponse],
)
async def get_org_payment_details(
    org_id: str,
    caller: OrgProfileReadUserDep,
    org_service: OrgServiceDep,
    start_date: date | None = Query(None, description="Filter from this date (inclusive), format: YYYY-MM-DD"),
    end_date: date | None = Query(None, description="Filter to this date (inclusive), format: YYYY-MM-DD"),
) -> dict:
    """Aggregated financial dashboard for an organization's payment details tab."""
    result = await org_service.get_payment_details(org_id, start_date=start_date, end_date=end_date)
    return ok(result)


# ── B2B profile completion & notification preferences ─────────────────────────


@router.get(
    "/{org_id}/profile-completion",
    response_model=SuccessResponse[ProfileCompletionResponse],
    **GET_PROFILE_COMPLETION,
)
async def get_profile_completion(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    org_service: OrgServiceDep,
) -> dict:
    """Onboarding checklist with weighted completion percentage for the B2B portal."""
    result = await org_service.get_profile_completion(org_id, _caller)
    return ok(result)


# ── Account manager ───────────────────────────────────────────────────────────


@router.get(
    "/{org_id}/account-manager",
    response_model=SuccessResponse[OrgAccountManagerResponse],
)
async def get_org_account_manager(
    org_id: str,
    admin: AdminUserDep,
    org_service: OrgServiceDep,
) -> dict:
    """Return the account manager assigned to this organisation.

    Returns null in the account_manager field when no manager is assigned. Admin only.
    """
    result = await org_service.get_account_manager(org_id)
    return ok(result)


@router.patch(
    "/{org_id}/account-manager",
    response_model=SuccessResponse[OrgAccountManagerResponse],
)
async def assign_org_account_manager(
    org_id: str,
    data: AssignAccountManagerRequest,
    admin: AdminUserDep,
    org_service: OrgServiceDep,
) -> dict:
    """Assign or replace the account manager for an organisation.

    Pass account_manager_user_id=null to unassign the current manager.
    Only admin users can be assigned as account managers. Admin only.
    """
    result = await org_service.assign_account_manager(org_id, data, admin_user_id=admin.id)
    return ok(result)


# ── Status change ─────────────────────────────────────────────────────────────


@router.patch(
    "/{org_id}/status",
    response_model=SuccessResponse[OrganizationResponse],
)
async def change_organization_status(
    org_id: str,
    data: OrganizationStatusChange,
    admin: AdminUserDep,
    org_service: OrgServiceDep,
) -> dict:
    """Deactivate, suspend, or reactivate an organization. Reason is mandatory. Admin only."""
    result = await org_service.change_status(org_id, data, admin_user_id=admin.id)
    return ok(result)


@router.patch(
    "/{org_id}/hold",
    response_model=SuccessResponse[OrganizationResponse],
)
async def place_org_on_hold(
    org_id: str,
    data: PlaceOnHoldRequest,
    admin: AdminUserDep,
    org_service: OrgServiceDep,
) -> dict:
    """Place an organisation on hold.

    New bookings will be blocked. Existing in-progress shipments will continue.
    Admin only.
    """
    result = await org_service.place_on_hold(org_id, reason=data.reason, admin_user_id=admin.id)
    return ok(result)


@router.patch(
    "/{org_id}/suspend",
    response_model=SuccessResponse[OrganizationResponse],
)
async def suspend_organization(
    org_id: str,
    data: SuspendOrgRequest,
    admin: AdminUserDep,
    org_service: OrgServiceDep,
) -> dict:
    """Suspend an organisation. Admin only."""
    result = await org_service.suspend_org(org_id, reason=data.reason, admin_user_id=admin.id)
    return ok(result)


@router.patch(
    "/{org_id}/deactivate",
    response_model=SuccessResponse[OrganizationResponse],
)
async def deactivate_organization_permanently(
    org_id: str,
    data: DeactivateOrgRequest,
    admin: AdminUserDep,
    org_service: OrgServiceDep,
) -> dict:
    """Permanently deactivate an organisation. Requires reason and trading name confirmation. Admin only."""
    result = await org_service.deactivate_permanently(
        org_id,
        reason=data.reason,
        confirm_name=data.confirm_name,
        admin_user_id=admin.id,
    )
    return ok(result)


# ── Delete (soft) ─────────────────────────────────────────────────────────────


@router.delete(
    "/{org_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_organization(
    org_id: str,
    admin: AdminUserDep,
    org_service: OrgServiceDep,
) -> dict:
    """Soft-delete (deactivate) an organization. Admin only."""
    await org_service.delete_organization(org_id, admin_user_id=admin.id)
    return ok(message="Organization deactivated.")


# ── Org Contacts ──────────────────────────────────────────────────────────────


@router.get(
    "/{org_id}/contacts",
    response_model=SuccessResponse[OrgContactListResponse],
)
async def list_org_contacts(
    org_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    contact_service: ContactServiceDep,
) -> dict:
    """List contacts for an organisation split into owner and team members."""
    contacts = await contact_service.list_contacts(
        org_id,
        caller_role=caller.role,
        caller_contact_role=caller_contact_role,
    )
    return ok(contacts)


@router.get(
    "/{org_id}/contacts/{contact_id}",
    response_model=SuccessResponse[OrgContactDetailResponse],
)
async def get_org_contact(
    org_id: str,
    contact_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    contact_service: ContactServiceDep,
) -> dict:
    """Get a single contact scoped to the organisation."""
    contact = await contact_service.get_contact(
        org_id,
        contact_id,
        caller_role=caller.role,
        caller_contact_role=caller_contact_role,
    )
    return ok(contact)


@router.post(
    "/{org_id}/contacts",
    response_model=SuccessResponse[OrgContactDetailResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_org_contact(
    org_id: str,
    body: OrgContactCreate,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    contact_service: ContactServiceDep,
) -> dict:
    """Add a new contact; creates a CUSTOMER_B2B user and sends an invite email. Admin or ACCOUNT_OWNER."""
    contact = await contact_service.add_contact(
        org_id,
        body,
        caller=caller,
        caller_contact_role=caller_contact_role,
    )
    return ok(contact)


@router.patch(
    "/{org_id}/contacts/{contact_id}",
    response_model=SuccessResponse[OrgContactDetailResponse],
)
async def update_org_contact(
    org_id: str,
    contact_id: str,
    body: OrgContactUpdate,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    contact_service: ContactServiceDep,
) -> dict:
    """Update contact number, role, and/or permission overrides. Admin or ACCOUNT_OWNER."""
    contact = await contact_service.update_contact(
        org_id,
        contact_id,
        body,
        caller=caller,
        caller_contact_role=caller_contact_role,
    )
    return ok(contact)


@router.delete(
    "/{org_id}/contacts/{contact_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def remove_org_contact(
    org_id: str,
    contact_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    contact_service: ContactServiceDep,
) -> dict:
    """Soft-delete a contact. Cannot remove the last active contact. Admin or ACCOUNT_OWNER."""
    await contact_service.remove_contact(
        org_id,
        contact_id,
        caller=caller,
        caller_contact_role=caller_contact_role,
    )
    return ok(message="Contact removed.")


@router.post(
    "/{org_id}/contacts/{contact_id}/set-primary",
    response_model=SuccessResponse[OrgContactDetailResponse],
)
async def set_primary_contact(
    org_id: str,
    contact_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    contact_service: ContactServiceDep,
) -> dict:
    """Set a contact as primary; clears is_primary on all others atomically. Admin or ACCOUNT_OWNER."""
    contact = await contact_service.set_primary(
        org_id,
        contact_id,
        caller=caller,
        caller_contact_role=caller_contact_role,
    )
    return ok(contact)


@router.post(
    "/{org_id}/contacts/{contact_id}/support-issue-password",
    response_model=SuccessResponse[SupportIssuePasswordResponse],
    **ISSUE_CONTACT_SUPPORT_PASSWORD,
)
async def support_issue_org_contact_password(
    org_id: str,
    contact_id: str,
    admin: B2bSupportPasswordResetDep,
    body: SupportIssuePasswordRequest,
    contact_service: ContactServiceDep,
    auth_service: AuthServiceDep,
) -> dict:
    target_user = await contact_service.resolve_contact_user_for_support_password(org_id, contact_id)
    uid, email = await auth_service.support_issue_temporary_password(
        actor=admin,
        target_user_id=target_user.id,
        new_password=body.new_password,
        flow="org_contact",
        organization_id=org_id,
    )
    return ok(
        data=SupportIssuePasswordResponse(user_id=uid, email=email),
        message="Password reset. The user was signed out of all sessions.",
    )


# ── Payment Configuration ──────────────────────────────────────────────────────
# Payment config is created atomically via POST /organizations (CreateOrgWithContactsRequest.payment_config).
# The endpoints below handle read / update / delete after initial org creation.


@router.get(
    "/{org_id}/payment-config",
    response_model=SuccessResponse[OrgPaymentConfigResponse],
)
async def get_payment_config(
    org_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    payment_config_service: PaymentConfigServiceDep,
) -> dict:
    """Get payment configuration for an organisation.

    If no org-specific config row exists yet, a fallback row is created from
    current global delivery/return attempt settings and returned.
    """
    result = await payment_config_service.get_payment_config(org_id, caller_role=caller.role, caller_contact_role=caller_contact_role)
    return ok(result)


@router.patch(
    "/{org_id}/payment-config",
    response_model=SuccessResponse[OrgPaymentConfigResponse],
)
async def update_payment_config(
    org_id: str,
    body: OrgPaymentConfigUpdate,
    admin: AdminUserDep,
    payment_config_service: PaymentConfigServiceDep,
) -> dict:
    """Update shared payment configuration for an organisation. Admin only.

    delivery/return max_* fields are optional and derived from fee array
    lengths when arrays are provided.
    """
    result = await payment_config_service.update_payment_config(org_id, body, admin_user_id=admin.id)
    return ok(result)


@router.delete(
    "/{org_id}/payment-config",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_payment_config(
    org_id: str,
    admin: AdminUserDep,
    payment_config_service: PaymentConfigServiceDep,
) -> dict:
    """Hard-delete the shared payment configuration. Admin only.

    Subsequent GET recreates fallback defaults from global settings.
    """
    await payment_config_service.delete_payment_config(org_id, admin_user_id=admin.id)
    return ok(message="Payment configuration deleted.")


# ── Payment Methods ─────────────────────────────────────────────────────────────
# Individual payment method add / update / remove.
# An org can have multiple payment models (CARD, BANK_TRANSFER, CREDIT_ACCOUNT, CASH).


@router.post(
    "/{org_id}/payment-config/methods",
    response_model=SuccessResponse[OrgPaymentConfigResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_payment_method(
    org_id: str,
    body: OrgPaymentMethodCreate,
    admin: AdminUserDep,
    payment_config_service: PaymentConfigServiceDep,
) -> dict:
    """Add a new payment method; raises 409 if model already exists. Admin only."""
    result = await payment_config_service.add_payment_method(org_id, body, admin_user_id=admin.id)
    return ok(result)


@router.patch(
    "/{org_id}/payment-config/methods/{payment_model}",
    response_model=SuccessResponse[OrgPaymentConfigResponse],
)
async def update_payment_method(
    org_id: str,
    payment_model: PaymentModel,
    body: OrgPaymentMethodUpdate,
    admin: AdminUserDep,
    payment_config_service: PaymentConfigServiceDep,
) -> dict:
    """Update a specific payment method by model name. Admin only."""
    result = await payment_config_service.update_payment_method(org_id, payment_model, body, admin_user_id=admin.id)
    return ok(result)


@router.delete(
    "/{org_id}/payment-config/methods/{payment_model}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def remove_payment_method(
    org_id: str,
    payment_model: PaymentModel,
    admin: AdminUserDep,
    payment_config_service: PaymentConfigServiceDep,
) -> dict:
    """Remove a payment method; cannot remove the last or default method. Admin only."""
    await payment_config_service.remove_payment_method(org_id, payment_model, admin_user_id=admin.id)
    return ok(message=f"Payment method {payment_model} removed.")


# ── Discount Configuration ─────────────────────────────────────────────────────
# Discount config is optionally created atomically via POST /organizations.
# The endpoints below handle read / upsert / delete after initial org creation.


@router.get(
    "/{org_id}/discount-config",
    response_model=SuccessResponse[OrgDiscountConfigResponse | None],
)
async def get_discount_config(
    org_id: str,
    admin: AdminUserDep,
    discount_service: DiscountConfigServiceDep,
) -> dict:
    """Get the discount configuration for an organisation; returns null if none set. Admin only."""
    result = await discount_service.get_discount_config(org_id)
    return ok(result)


@router.put(
    "/{org_id}/discount-config",
    response_model=SuccessResponse[OrgDiscountConfigResponse],
)
async def upsert_discount_config(
    org_id: str,
    body: OrgDiscountConfigUpsert,
    admin: AdminUserDep,
    discount_service: DiscountConfigServiceDep,
) -> dict:
    """Upsert the discount configuration for an organisation. Admin only."""
    result = await discount_service.upsert_discount_config(org_id, body, admin_user_id=admin.id)
    return ok(result)


@router.delete(
    "/{org_id}/discount-config",
    response_model=SuccessResponse[MessageResponse],
    status_code=status.HTTP_200_OK,
)
async def delete_discount_config(
    org_id: str,
    admin: AdminUserDep,
    discount_service: DiscountConfigServiceDep,
) -> dict:
    """Hard-delete the discount configuration for an organisation. Admin only."""
    await discount_service.delete_discount_config(org_id, admin_user_id=admin.id)
    return ok(MessageResponse(message="Discount configuration deleted."))


# ── Credit & Suspension Configuration ─────────────────────────────────────────
# Credit config and suspension config are optionally created atomically via
# POST /organizations (CreateOrgWithContactsRequest.credit_config / .suspension_config).
# The endpoints below handle read / update after initial org creation.


@router.get(
    "/{org_id}/credit-suspension",
    response_model=SuccessResponse[OrgCreditSuspensionFullResponse],
)
async def get_credit_suspension_config(
    org_id: str,
    admin: AdminUserDep,
    credit_suspension_service: CreditSuspensionServiceDep,
) -> dict:
    """Get the full credit and suspension config for an organisation. Admin only."""
    result = await credit_suspension_service.get_full_config(org_id)
    return ok(result)


@router.put(
    "/{org_id}/credit-suspension/credit",
    response_model=SuccessResponse[OrgCreditConfigResponse],
)
async def upsert_credit_config(
    org_id: str,
    body: OrgCreditConfigUpsert,
    admin: AdminUserDep,
    credit_suspension_service: CreditSuspensionServiceDep,
) -> dict:
    """Upsert the credit configuration for an organisation. Admin only."""
    result = await credit_suspension_service.upsert_credit_config(org_id, body, admin_user_id=admin.id)
    return ok(result)


# ── Account Suspension Rules (org-scoped) ─────────────────────────────────────
# These endpoints expose the effective suspension rules for a single organisation.
# Each rule type (CREDIT_CARD, BANK_TRANSFER, CREDIT_LIMIT, CASH) can be either:
#   - Default   → no org override; the global rule applies
#   - Overridden → org has its own rule that replaces the global one
# Editing a rule here creates/updates an ORG-scoped row and never touches other orgs.


def _rule_set_to_response(
    item,
    *,
    is_override: bool = False,
    source_scope_type: RuleScopeType | None = None,
    source_rule_set_id: str | None = None,
    global_rule_set_id: str | None = None,
) -> SuspensionRuleSetResponse:
    return SuspensionRuleSetResponse(
        id=item.id,
        name=item.name,
        condition_summary=item.condition_summary,
        scope_type=RuleScopeType(item.scope_type),
        scope_org_id=item.scope_org_id,
        rule_type=SuspensionRuleType(item.rule_type),
        status=SuspensionRuleStatus(item.status),
        notes=item.notes,
        auto_suspension_enabled=item.auto_suspension_enabled,
        pause_new_bookings=item.pause_new_bookings,
        restrict_portal_login=item.restrict_portal_login,
        notify_finance_team=item.notify_finance_team,
        notify_account_manager=item.notify_account_manager,
        conditions=[
            SuspensionRuleConditionV2(
                position=cond.position,
                connector=cond.connector,
                condition_type=cond.condition_type,
                threshold_value=float(cond.threshold_value),
                unit=cond.unit,
            )
            for cond in sorted(item.conditions, key=lambda c: c.position)
        ],
        created_at=item.created_at,
        updated_at=item.updated_at,
        version=item.version,
        is_override=is_override,
        source_scope_type=source_scope_type,
        source_rule_set_id=source_rule_set_id,
        global_rule_set_id=global_rule_set_id,
        can_restore_default=bool(is_override and global_rule_set_id),
    )


@router.get(
    "/{org_id}/suspension-rules",
    response_model=SuccessResponse[SuspensionRuleSetListResponse],
)
async def get_org_suspension_rules(
    org_id: str,
    admin: AdminUserDep,
    suspension_service: SuspensionRulesServiceDep,
    rule_type: SuspensionRuleType | None = Query(default=None),
) -> dict:
    """Return effective suspension rules for this organisation, including global-vs-override source info."""
    rows = await suspension_service.get_effective_rule_sets_with_source_for_org(org_id, rule_type=rule_type)
    responses = [
        _rule_set_to_response(
            row["rule_set"],
            is_override=bool(row.get("is_override")),
            source_scope_type=RuleScopeType(row["source_scope_type"]) if row.get("source_scope_type") else None,
            source_rule_set_id=row.get("source_rule_set_id"),
            global_rule_set_id=row.get("global_rule_set_id"),
        )
        for row in rows
    ]
    return ok(data=SuspensionRuleSetListResponse(items=responses, total=len(responses)))


@router.put(
    "/{org_id}/suspension-rules/{rule_type}",
    response_model=SuccessResponse[SuspensionRuleSetResponse],
)
async def upsert_org_suspension_rule(
    org_id: str,
    rule_type: SuspensionRuleType,
    body: OrgRuleOverrideUpsertRequest,
    admin: AdminUserDep,
    suspension_service: SuspensionRulesServiceDep,
) -> dict:
    """Create or update the org-specific suspension rule override for one rule type. Admin only."""
    payload = {}
    for key in (
        "name",
        "condition_summary",
        "status",
        "notes",
        "auto_suspension_enabled",
        "pause_new_bookings",
        "restrict_portal_login",
        "notify_finance_team",
        "notify_account_manager",
    ):
        value = getattr(body, key)
        if value is not None:
            payload[key] = value.value if hasattr(value, "value") else value
    conditions = None
    if body.conditions is not None:
        conditions = [
            {
                "position": cond.position,
                "connector": cond.connector.value if cond.connector else None,
                "condition_type": cond.condition_type.value,
                "threshold_value": cond.threshold_value,
                "unit": cond.unit,
            }
            for cond in body.conditions
        ]
    item = await suspension_service.upsert_org_rule_override(
        organization_id=org_id,
        rule_type=rule_type,
        payload=payload,
        conditions=conditions,
        expected_version=body.version,
        audit_user_id=admin.id,
        audit_user_role=admin.role,
    )
    effect = await suspension_service.get_effective_rule_sets_with_source_for_org(org_id, rule_type=rule_type)
    g_id = effect[0].get("global_rule_set_id") if effect else None
    return ok(
        data=_rule_set_to_response(
            item,
            is_override=True,
            source_scope_type=RuleScopeType.ORG,
            source_rule_set_id=item.id,
            global_rule_set_id=g_id,
        )
    )


@router.delete(
    "/{org_id}/suspension-rules/{rule_type}",
    response_model=SuccessResponse[MessageResponse],
)
async def delete_org_suspension_rule_override(
    org_id: str,
    rule_type: SuspensionRuleType,
    admin: AdminUserDep,
    suspension_service: SuspensionRulesServiceDep,
) -> dict:
    """Remove the org-specific suspension rule override; falls back to global default. Admin only."""
    await suspension_service.delete_org_rule_override(
        organization_id=org_id,
        rule_type=rule_type,
        audit_user_id=admin.id,
        audit_user_role=admin.role,
    )
    return ok(data=MessageResponse(message="Override removed. Organisation will now use the global default rule."))


# ── Contract ──────────────────────────────────────────────────────────────────


@router.post(
    "/{org_id}/contract",
    response_model=SuccessResponse[OrgDocumentResponse],
    status_code=status.HTTP_201_CREATED,
)
async def upload_org_contract(
    org_id: str,
    admin: AdminUserDep,
    doc_service: DocServiceDep,
    org_service: OrgServiceDep,
    document_file: UploadFile = File(
        ...,
        description=(
            "Contract document. "
            "Accepted formats: .pdf, .png, .jpeg, .docx, .heic — max 25 MB."
        ),
    ),
    title: str = Form(..., min_length=1, max_length=255, description="Contract title"),
    document_type: OrgDocumentType = Form(
        ...,
        description="Contract type: MSA | SLA | NDA | DPA | PRICING",
    ),
    expiry_date: date = Form(..., description="Contract expiry date (ISO 8601, e.g. 2027-12-31)"),
) -> dict:
    """Upload a contract document (MSA/SLA/NDA/DPA/PRICING) for an organisation. Admin only."""
    if document_type not in CONTRACT_DOCUMENT_TYPES:
        raise AppValidationError(
            f"document_type must be one of: {', '.join(t.value for t in CONTRACT_DOCUMENT_TYPES)}"
        )
    result = await doc_service.upload_document(
        org_id=org_id,
        file=document_file,
        title=title,
        document_type=document_type,
        expiry_date=expiry_date,
        admin_user_id=admin.id,
        category=OrgDocumentCategory.CONTRACTS,
    )
    # Mirror contract metadata onto the organizations row so GET /organizations/{id}
    # returns contract_reference, contract_title, contract_expiry_date, and contract_url.
    await org_service.update_contract_metadata(
        org_id=org_id,
        r2_key=result.r2_key,
        title=title,
        expiry_date=expiry_date,
        admin_user_id=admin.id,
    )
    return ok(result)


@router.get(
    "/{org_id}/contract",
    response_model=SuccessResponse[PaginatedResponse[OrgDocumentResponse]],
)
async def list_org_contracts(
    request: Request,
    org_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    doc_service: DocServiceDep,
    _doc_access: DocAccessDep,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    size: int = Query(50, ge=1, le=200, description="Items per page"),
    search: str | None = Query(None, max_length=200, description="Search by contract ID, title, or uploader email"),
    document_type: list[OrgDocumentType] = Query(default=[], description="Filter by one or more types: MSA | SLA | NDA | DPA | PRICING"),
    status: list[OrgDocumentStatus] = Query(default=[], description="Filter by one or more statuses: ACTIVE | EXPIRED | EXPIRING_SOON"),
    date_from: datetime | None = Query(None, description="Filter contracts uploaded on or after this datetime (ISO 8601)"),
    date_to: datetime | None = Query(None, description="Filter contracts uploaded on or before this datetime (ISO 8601)"),
) -> dict:
    """List all contract documents for an organisation with pagination and filtering."""
    items, total = await doc_service.list_contracts(
        org_id=org_id,
        caller_role=caller.role,
        caller_contact_role=caller_contact_role,
        page=page,
        size=size,
        search=search,
        document_type=document_type or None,
        status=status or None,
        date_from=date_from,
        date_to=date_to,
    )
    paginated = PaginatedResponse[OrgDocumentResponse].create(items=items, total=total, page=page, size=size, request=request)
    return ok(paginated)


# ── Document Access OTP ────────────────────────────────────────────────────────


@router.post(
    "/documents/otp/send",
    response_model=SuccessResponse[DocOTPSendResponse],
    **SEND_DOC_OTP,
)
async def send_doc_otp(
    user: CurrentUserDep,
    session: SessionDep,
    service: DocAccessServiceDep,
) -> dict:
    """Send a 6-digit OTP to the user's email; valid 10 minutes. Rate limited to 3/10 min."""
    from sqlalchemy import select as sa_select

    from app.modules.user.models import User

    stmt = sa_select(User.email, User.first_name, User.last_name).where(User.id == user.id)
    result = await session.execute(stmt)
    row = result.one_or_none()
    if row is None:
        from app.common.exceptions import NotFoundError
        raise NotFoundError("User account not found.")

    user_email, first_name, last_name = row
    user_name = f"{first_name or ''} {last_name or ''}".strip() or user_email

    await service.send_otp(user_id=user.id, user_email=user_email, user_name=user_name)
    return ok(DocOTPSendResponse())


@router.post(
    "/documents/otp/verify",
    response_model=SuccessResponse[DocAccessTokenResponse],
    **VERIFY_DOC_OTP,
)
@limiter.limit(DOC_OTP_VERIFY_RATE_LIMIT)
async def verify_doc_otp(
    request: Request,
    response: Response,
    body: DocOTPVerifyRequest,
    user: CurrentUserDep,
    service: DocAccessServiceDep,
) -> dict:
    """Verify the OTP and return a 1-hour doc_access_token for document endpoints."""
    result = await service.verify_otp(user_id=user.id, otp_code=body.otp)
    return ok(
        DocAccessTokenResponse(
            doc_access_token=result["doc_access_token"],
            expires_in=result["expires_in"],
            expires_at=result["expires_at"],
            message="OTP verified. Use the doc_access_token in the X-Doc-Access-Token header on document endpoints. Valid for 1 hour.",
        )
    )


# ── Contract Documents ─────────────────────────────────────────────────────────


@router.post(
    "/{org_id}/documents",
    response_model=SuccessResponse[OrgDocumentResponse],
    status_code=status.HTTP_201_CREATED,
    **UPLOAD_ORG_DOCUMENT,
)
async def upload_org_document(
    org_id: str,
    admin: AdminUserDep,
    doc_service: DocServiceDep,
    _doc_access: DocAccessDep,
    document_file: UploadFile = File(
        ...,
        description=(
            "Contract/agreement document. "
            "Accepted formats: .pdf, .png, .jpeg, .docx, .heic — max 25 MB."
        ),
    ),
    title: str = Form(..., min_length=1, max_length=255, description="Document title"),
    document_type: OrgDocumentType = Form(..., description="MSA | SLA | PRICING | NDA | DPA"),
    expiry_date: date = Form(..., description="Document expiry date (ISO 8601 — e.g. 2027-12-31)"),
) -> dict:
    """Upload a contract or agreement document (MSA/SLA/PRICING/NDA/DPA). Admin only."""
    result = await doc_service.upload_document(
        org_id=org_id,
        file=document_file,
        title=title,
        document_type=document_type,
        expiry_date=expiry_date,
        admin_user_id=admin.id,
    )
    return ok(result)


@router.post(
    "/{org_id}/documents/operations",
    response_model=SuccessResponse[OrgDocumentResponse],
    status_code=status.HTTP_201_CREATED,
    **UPLOAD_ORG_DOCUMENT_OPERATIONS,
)
async def upload_org_document_operations(
    org_id: str,
    admin: AdminUserDep,
    doc_service: DocServiceDep,
    _doc_access: DocAccessDep,
    document_file: UploadFile = File(
        ...,
        description=(
            "Document file. "
            "Accepted formats: PNG, JPG, PDF, DOCX — max 25 MB."
        ),
    ),
    title: str = Form(..., min_length=1, max_length=200, description="Document title (max 200 chars)"),
    document_type: OrgDocumentType = Form(..., description="Document type enum value"),
    category: OrgDocumentCategory = Form(..., description="CONTRACTS | INTERNAL | CLIENT_UPLOADS"),
    issuing_authority: str | None = Form(None, max_length=255, description="e.g. Swift Retail Limited"),
    issue_date: date | None = Form(None, description="ISO 8601 date — e.g. 2024-03-01"),
    expiry_date: date | None = Form(None, description="ISO 8601 date — e.g. 2026-11-26"),
    description: str | None = Form(None, max_length=1000, description="Brief description (max 1000 chars)"),
    confidentiality_level: OrgDocumentConfidentialityLevel | None = Form(
        None,
        description="PUBLIC | INTERNAL | CONFIDENTIAL | STRICTLY_CONFIDENTIAL",
    ),
    tags: str | None = Form(
        None,
        description=(
            '**JSON array** of tag strings, max 10 items. Example: `["compliance", "2026", "reviewed"]`'
        ),
    ),
    notify_client: bool = Form(False, description="When true, the client is notified that this document was added"),
) -> dict:
    """Upload a document with full classification (category, confidentiality, tags). Admin only."""
    import json as _json

    parsed_tags: list[str] | None = None
    if tags is not None:
        try:
            parsed_tags = _json.loads(tags)
            if not isinstance(parsed_tags, list) or len(parsed_tags) > 10:
                raise AppValidationError("tags must be a JSON array with at most 10 items.")
        except (_json.JSONDecodeError, TypeError) as exc:
            raise AppValidationError("tags must be a valid JSON array of strings.") from exc

    data = OrgDocumentOperationsRequest(
        title=title,
        document_type=document_type,
        category=category,
        issuing_authority=issuing_authority,
        issue_date=issue_date,
        expiry_date=expiry_date,
        description=description,
        confidentiality_level=confidentiality_level,
        tags=parsed_tags,
        notify_client=notify_client,
    )
    result = await doc_service.upload_document_operations(
        org_id=org_id,
        file=document_file,
        data=data,
        admin_user_id=admin.id,
    )
    return ok(result)


@router.get(
    "/{org_id}/documents/activities",
    response_model=SuccessResponse[PaginatedResponse[OrgDocumentActivityResponse]],
    **LIST_ORG_DOCUMENT_ACTIVITIES,
)
async def list_org_document_activities(
    request: Request,
    org_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    doc_service: DocServiceDep,
    _doc_access: DocAccessDep,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    size: int = Query(50, ge=1, le=200, description="Items per page"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$", description="Sort by created_at: 'desc' (newest first) or 'asc' (oldest first)"),
    activity_types: list[OrgDocumentActivityType] = Query(default=[], description="Filter by one or more activity types (multi-select): UPLOADED, DOWNLOADED, VIEWED, SHARED, EXPIRED, DELETED"),
    date_from: datetime | None = Query(None, description="Filter activities on or after this datetime (ISO 8601, e.g. 2026-02-19T00:00:00)"),
    date_to: datetime | None = Query(None, description="Filter activities on or before this datetime (ISO 8601, e.g. 2026-02-25T23:59:59)"),
    search: str | None = Query(None, max_length=200, description="Search by doc reference, IP address, actor email, document name, or details"),
    browser: str | None = Query(None, max_length=100, description="Filter by browser (partial match): Chrome, Mozilla Firefox, Microsoft Edge, Safari, Opera, Internet Explorer"),
) -> dict:
    """List the activity log for all documents in an organisation with pagination and filtering."""
    items, total = await doc_service.list_document_activities(
        org_id=org_id,
        caller_role=caller.role,
        caller_contact_role=caller_contact_role,
        page=page,
        size=size,
        sort_order=sort_order,
        activity_types=activity_types or None,
        date_from=date_from,
        date_to=date_to,
        search=search,
        browser=browser,
    )
    paginated = PaginatedResponse[OrgDocumentActivityResponse].create(items=items, total=total, page=page, size=size, request=request)
    return ok(paginated)


@router.get(
    "/{org_id}/documents/activities/export",
    response_class=StreamingResponse,
)
async def export_org_document_activities(
    org_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    doc_service: DocServiceDep,
    _doc_access: DocAccessDep,
    activity_types: list[OrgDocumentActivityType] = Query(default=[]),
    date_from: datetime | None = Query(None, description="ISO 8601, e.g. 2026-01-01T00:00:00"),
    date_to: datetime | None = Query(None, description="ISO 8601, e.g. 2026-12-31T23:59:59"),
    search: str | None = Query(None, max_length=200),
) -> StreamingResponse:
    """Export all document activity logs for an organisation as a CSV file."""
    csv_content = await doc_service.export_document_activities_csv(
        org_id=org_id,
        caller_role=caller.role,
        caller_contact_role=caller_contact_role,
        activity_types=activity_types or None,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )
    filename = f"document_activities_{org_id[:8]}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{org_id}/documents/{doc_id}/activities",
    response_model=SuccessResponse[list[OrgDocumentActivityResponse]],
)
async def list_document_activities(
    org_id: str,
    doc_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    doc_service: DocServiceDep,
    _doc_access: DocAccessDep,
    activity_types: list[OrgDocumentActivityType] = Query(default=[]),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
) -> dict:
    """List all activity logs for a single document without pagination."""
    items = await doc_service.list_document_activities_by_document(
        org_id=org_id,
        doc_id=doc_id,
        caller_role=caller.role,
        caller_contact_role=caller_contact_role,
        activity_types=activity_types or None,
        date_from=date_from,
        date_to=date_to,
    )
    return ok(items)


@router.get(
    "/{org_id}/documents/{doc_id}/activities/export",
    response_class=StreamingResponse,
)
async def export_document_activities(
    org_id: str,
    doc_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    doc_service: DocServiceDep,
    _doc_access: DocAccessDep,
    activity_types: list[OrgDocumentActivityType] = Query(default=[]),
    date_from: datetime | None = Query(None, description="ISO 8601, e.g. 2026-01-01T00:00:00"),
    date_to: datetime | None = Query(None, description="ISO 8601, e.g. 2026-12-31T23:59:59"),
) -> StreamingResponse:
    """Export activity logs for a single document as a CSV file."""
    csv_content = await doc_service.export_document_activities_csv(
        org_id=org_id,
        caller_role=caller.role,
        caller_contact_role=caller_contact_role,
        document_id=doc_id,
        activity_types=activity_types or None,
        date_from=date_from,
        date_to=date_to,
    )
    filename = f"doc_{doc_id[:8]}_activities_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{org_id}/documents",
    response_model=SuccessResponse[OrgDocumentListResponse],
    **LIST_ORG_DOCUMENTS,
)
async def list_org_documents(
    request: Request,
    org_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    doc_service: DocServiceDep,
    _doc_access: DocAccessDep,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    size: int = Query(50, ge=1, le=200, description="Items per page"),
    search: str | None = Query(None, max_length=200, description="Search by doc ID, name, actor email, or uploader email"),
    category: list[OrgDocumentCategory] = Query(default=[], description="Filter by one or more categories (multi-select): CONTRACTS, INTERNAL, CLIENT_UPLOADS"),
    document_type: list[OrgDocumentType] = Query(default=[], description="Filter by one or more document types (multi-select): MSA, SLA, NDA, DPA, PRICING, …"),
) -> dict:
    """List all active contract/agreement documents for an organisation."""
    items, total, stats_raw = await doc_service.list_documents(
        org_id=org_id,
        caller_role=caller.role,
        caller_contact_role=caller_contact_role,
        page=page,
        size=size,
        search=search,
        category_in=category or None,
        document_type_in=document_type or None,
    )

    pages = (total + size - 1) // size if size > 0 else 0
    current_url = str(request.url)
    next_url: str | None = str(request.url.include_query_params(page=page + 1, size=size)) if page < pages else None

    stats = OrgDocumentStats(
        expiring_soon=OrgDocumentExpiringSoonCard(
            count=stats_raw["expiring_soon_count"],
            next_title=stats_raw["expiring_soon_next_title"],
            next_expiry_date=stats_raw["expiring_soon_next_expiry"],
        ),
        total=OrgDocumentTotalCard(
            count=stats_raw["total_count"],
            breakdown=OrgDocumentTotalBreakdown(
                contracts=stats_raw["contracts_count"],
                client=stats_raw["client_count"],
                internal=stats_raw["internal_count"],
                system=stats_raw["system_count"],
            ),
        ),
    )

    return ok(
        OrgDocumentListResponse(
            stats=stats,
            items=items,
            total=total,
            page=page,
            size=size,
            pages=pages,
            current_url=current_url,
            next_url=next_url,
        )
    )


@router.get(
    "/{org_id}/documents/{doc_id}",
    response_model=SuccessResponse[OrgDocumentResponse],
    **GET_ORG_DOCUMENT,
)
async def get_org_document(
    request: Request,
    org_id: str,
    doc_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    doc_service: DocServiceDep,
    _doc_access: DocAccessDep,
) -> dict:
    """Get a single document with a fresh presigned download URL (valid 1 hour)."""
    actor_role = "Admin" if is_platform_admin_role(caller.role) else "Client"
    result = await doc_service.get_document(
        org_id=org_id,
        doc_id=doc_id,
        caller_role=caller.role,
        caller_contact_role=caller_contact_role,
        caller_user_id=caller.id,
        actor_role_label=actor_role,
        request=request,
    )
    return ok(result)


@router.patch(
    "/{org_id}/documents/{doc_id}",
    response_model=SuccessResponse[OrgDocumentResponse],
    **UPDATE_ORG_DOCUMENT,
)
async def update_org_document(
    org_id: str,
    doc_id: str,
    body: OrgDocumentUpdate,
    admin: AdminUserDep,
    doc_service: DocServiceDep,
    _doc_access: DocAccessDep,
) -> dict:
    """Update document metadata (title, type, expiry date). Admin only."""
    result = await doc_service.update_document(
        org_id=org_id,
        doc_id=doc_id,
        data=body,
        admin_user_id=admin.id,
    )
    return ok(result)


@router.delete(
    "/{org_id}/documents/{doc_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    **DELETE_ORG_DOCUMENT,
)
async def delete_org_document(
    org_id: str,
    doc_id: str,
    admin: AdminUserDep,
    doc_service: DocServiceDep,
    _doc_access: DocAccessDep,
) -> dict:
    """Soft-delete a document and remove its file from R2. Admin only."""
    await doc_service.delete_document(
        org_id=org_id,
        doc_id=doc_id,
        admin_user_id=admin.id,
    )
    return ok(message="Document deleted.")


# ── Document Sharing ───────────────────────────────────────────────────────────


@router.get(
    "/{org_id}/documents/shares",
    response_model=SuccessResponse[PaginatedResponse[OrgDocumentShareResponse]],
    **LIST_ORG_DOCUMENT_SHARES,
)
async def list_org_document_shares(
    request: Request,
    org_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    share_service: ShareServiceDep,
    _doc_access: DocAccessDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
    status: list[OrgDocumentShareStatus] = Query(default=[], description="Filter by one or more share statuses"),
    document_type: list[OrgDocumentType] = Query(default=[], description="Filter by one or more document types"),
) -> dict:
    """Paginated sharing history across all documents in an organisation."""
    items, total = await share_service.list_shares_for_org(
        org_id=org_id,
        caller_role=caller.role,
        caller_contact_role=caller_contact_role,
        page=page,
        size=size,
        status_in=status,
        document_type_in=document_type,
    )
    paginated = PaginatedResponse[OrgDocumentShareResponse].create(items=items, total=total, page=page, size=size, request=request)
    return ok(paginated)


@router.post(
    "/{org_id}/documents/{doc_id}/shares",
    response_model=SuccessResponse[OrgDocumentShareResponse],
    status_code=status.HTTP_201_CREATED,
    **SHARE_DOCUMENT,
)
async def share_document(
    org_id: str,
    doc_id: str,
    body: OrgDocumentShareCreate,
    admin: AdminUserDep,
    share_service: ShareServiceDep,
    _doc_access: DocAccessDep,
) -> dict:
    """Share a document securely via email with one or more recipients. **Admin only."""
    result = await share_service.share_document(
        org_id=org_id,
        doc_id=doc_id,
        data=body,
        admin_user_id=admin.id,
    )
    return ok(result)


@router.get(
    "/{org_id}/documents/{doc_id}/shares",
    response_model=SuccessResponse[list[OrgDocumentShareResponse]],
    **LIST_DOCUMENT_SHARES,
)
async def list_document_shares(
    org_id: str,
    doc_id: str,
    caller: OrgMemberDep,
    caller_contact_role: CallerContactRoleDep,
    share_service: ShareServiceDep,
    _doc_access: DocAccessDep,
) -> dict:
    """List all share records for a **specific document**, newest first."""
    result = await share_service.list_shares_for_document(
        org_id=org_id,
        doc_id=doc_id,
        caller_role=caller.role,
        caller_contact_role=caller_contact_role,
    )
    return ok(result)


@router.patch(
    "/{org_id}/documents/shares/{share_id}/expiry",
    response_model=SuccessResponse[OrgDocumentShareResponse],
    **EXTEND_SHARE_EXPIRY,
)
async def extend_share_expiry(
    org_id: str,
    share_id: str,
    body: OrgDocumentShareExtendExpiry,
    admin: AdminUserDep,
    share_service: ShareServiceDep,
    _doc_access: DocAccessDep,
) -> dict:
    """Extend or change the expiry date of an **ACTIVE** share link. **Admin only."""
    result = await share_service.extend_expiry(
        org_id=org_id,
        share_id=share_id,
        data=body,
        admin_user_id=admin.id,
    )
    return ok(result)


@router.patch(
    "/{org_id}/documents/shares/{share_id}/revoke",
    response_model=SuccessResponse[OrgDocumentShareResponse],
    **REVOKE_DOCUMENT_SHARE,
)
async def revoke_document_share(
    org_id: str,
    share_id: str,
    body: OrgDocumentShareRevoke,
    admin: AdminUserDep,
    share_service: ShareServiceDep,
    _doc_access: DocAccessDep,
) -> dict:
    """Permanently revoke a share link — access is **immediately invalidated**. **Admin only.**"""
    result = await share_service.revoke_share(
        org_id=org_id,
        share_id=share_id,
        data=body,
        admin_user_id=admin.id,
    )
    return ok(result)


# ── Pickup Addresses ──────────────────────────────────────────────────────────


@router.get(
    "/{org_id}/pickup-addresses",
    response_model=SuccessResponse[list[PickupAddressResponse]],
    **LIST_PICKUP_ADDRESSES,
)
async def list_pickup_addresses(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: PickupAddressServiceDep,
) -> dict:
    """List all pickup addresses for an organisation."""
    owner = PickupAddressOwner(organization_id=org_id)
    result = await svc.list_for_organization(owner)
    return ok(result)


@router.post(
    "/{org_id}/pickup-addresses",
    response_model=SuccessResponse[list[PickupAddressResponse]],
    status_code=status.HTTP_201_CREATED,
    **CREATE_PICKUP_ADDRESS,
)
async def create_pickup_address(
    org_id: str,
    caller: OrgProfileWriteUserDep,
    svc: PickupAddressServiceDep,
    body: CreatePickupAddressesRequest,
) -> dict:
    """Add one or more pickup addresses to an organisation."""
    owner = PickupAddressOwner(organization_id=org_id)
    result = await svc.create_addresses_for_organization(
        owner,
        body,
        caller.id,
        auto_promote_first_default=False,
    )
    return ok(result)


@router.patch(
    "/{org_id}/pickup-addresses/{address_id}",
    response_model=SuccessResponse[PickupAddressResponse],
    **UPDATE_PICKUP_ADDRESS,
)
async def update_pickup_address(
    org_id: str,
    address_id: str,
    caller: OrgProfileWriteUserDep,
    svc: PickupAddressServiceDep,
    body: PickupAddressUpdate,
) -> dict:
    """Update a pickup address."""
    owner = PickupAddressOwner(organization_id=org_id)
    result = await svc.update_for_organization(owner, address_id, body, caller.id)
    return ok(result)


@router.delete(
    "/{org_id}/pickup-addresses/{address_id}",
    response_model=SuccessResponse[MessageResponse],
    **DELETE_PICKUP_ADDRESS,
)
async def delete_pickup_address(
    org_id: str,
    address_id: str,
    caller: OrgProfileWriteUserDep,
    svc: PickupAddressServiceDep,
) -> dict:
    """Delete a pickup address. If the deleted address was the default, the oldest remaining address is automatically promoted to default."""
    owner = PickupAddressOwner(organization_id=org_id)
    await svc.delete_for_organization(owner, address_id, caller.id)
    return ok(MessageResponse(message="Pickup address deleted."))



@router.get(
    "/{org_id}/payment-methods/cards/braintree-client-token",
    response_model=SuccessResponse[ClientTokenResponse],
    **ORG_CARDS_BRAINTREE_TOKEN,
)
async def get_org_braintree_client_token(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    session: SessionDep,
    svc: PaymentServiceDep,
) -> dict:
    owner = CreditCardOwner(organization_id=org_id)
    token = await svc.generate_client_token(owner)
    return ok(ClientTokenResponse(client_token=token))


@router.get(
    "/{org_id}/payment-methods/cards",
    response_model=SuccessResponse[list[PaymentMethodResponse]],
    **ORG_CARDS_LIST,
)
async def list_org_payment_cards(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: PaymentServiceDep,
) -> dict:
    owner = CreditCardOwner(organization_id=org_id)
    cards = await svc.list_payment_methods(owner)
    return ok(cards)


@router.post(
    "/{org_id}/payment-methods/cards",
    response_model=SuccessResponse[PaymentMethodResponse],
    status_code=status.HTTP_201_CREATED,
    **ORG_CARDS_CREATE,
)
async def create_org_payment_card(
    org_id: str,
    _caller: OrgProfileWriteUserDep,
    session: SessionDep,
    svc: PaymentServiceDep,
    ctx: AuditCtxDep,
    data: CreatePaymentMethodRequest,
) -> dict:
    owner = CreditCardOwner(organization_id=org_id)
    result = await svc.create_payment_method(
        owner=owner,
        nonce=data.nonce,
        ctx=ctx,
        cardholder_name=data.cardholder_name,
        set_as_default=data.set_as_default,
    )
    return ok(result, message="Card saved successfully")


@router.post(
    "/{org_id}/payment-methods/cards/prepare-payment",
    response_model=SuccessResponse[PreparePaymentNonceResponse],
    **ORG_CARDS_PREPARE_PAYMENT,
)
async def prepare_org_checkout_nonce(
    org_id: str,
    _caller: OrgProfileWriteUserDep,
    session: SessionDep,
    svc: PaymentServiceDep,
    ctx: AuditCtxDep,
    data: PreparePaymentNonceRequest,
) -> dict:
    owner = CreditCardOwner(organization_id=org_id)
    payload = await svc.prepare_checkout_nonce(owner, data.card_id, ctx)
    return ok(payload)


@router.get(
    "/{org_id}/payment-methods/cards/{card_id}",
    response_model=SuccessResponse[PaymentMethodResponse],
    **ORG_CARDS_GET,
)
async def get_org_payment_card(
    org_id: str,
    card_id: str,
    _caller: OrgProfileReadUserDep,
    svc: PaymentServiceDep,
) -> dict:
    owner = CreditCardOwner(organization_id=org_id)
    card = await svc.get_payment_method(owner, card_id)
    return ok(card)


@router.patch(
    "/{org_id}/payment-methods/cards/{card_id}/mark-default",
    response_model=SuccessResponse[PaymentMethodResponse],
    **ORG_CARDS_MARK_DEFAULT,
)
async def mark_org_payment_card_as_default(
    org_id: str,
    card_id: str,
    _caller: OrgProfileWriteUserDep,
    svc: PaymentServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    owner = CreditCardOwner(organization_id=org_id)
    card = await svc.mark_as_default(owner, card_id, ctx)
    return ok(card, message="Card marked as default")


@router.patch(
    "/{org_id}/payment-methods/cards/{card_id}/unmark-default",
    response_model=SuccessResponse[PaymentMethodResponse],
    **ORG_CARDS_UNMARK_DEFAULT,
)
async def unmark_org_payment_card_as_default(
    org_id: str,
    card_id: str,
    _caller: OrgProfileWriteUserDep,
    svc: PaymentServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    owner = CreditCardOwner(organization_id=org_id)
    card = await svc.unmark_as_default(owner, card_id, ctx)
    return ok(card, message="Card unmarked as default")


@router.delete(
    "/{org_id}/payment-methods/cards/{card_id}",
    response_model=SuccessResponse[MessageResponse],
    **ORG_CARDS_DELETE,
)
async def delete_org_payment_card(
    org_id: str,
    card_id: str,
    _caller: OrgProfileWriteUserDep,
    svc: PaymentServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    owner = CreditCardOwner(organization_id=org_id)
    await svc.delete_payment_method(owner, card_id, ctx)
    return ok(MessageResponse(message="Card removed"))

