from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import structlog
from fastapi import Request
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import AuthUser
from app.common.enums import UserRole
from app.common.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.common.schemas import UserSchema
from app.common.service import BaseService
from app.integrations.creditsafe.client import (
    CreditsafeNoCompanyFound,
    request_fresh_investigation,
    run_credit_assessment,
)
from app.integrations.creditsafe.report_parser import parse_creditsafe_report
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.org_credit.enums import OrgCreditInvestigationStatus
from app.modules.org_credit.models import OrgCreditInvestigation, OrgCreditReport
from app.modules.org_credit.repository import (
    OrgCreditAccountRepository,
    OrgCreditInvestigationRepository,
    OrgCreditReportRepository,
)
from app.modules.org_credit.service import OrgCreditService
from app.modules.org_credit.v1.schemas import (
    CreditCheckResult,
    CreditInvestigationResponse,
    CreditReportResponse,
)
from app.modules.org_credit_applications.enums import (
    AttachmentType,
    CreditApplicationLifecycleState,
    CreditApplicationStatus,
    OrgCreditLimitIncreaseRequestStatus,
    TradeReferenceVerificationStatus,
)
from app.modules.org_credit_applications.models import (
    OrgCreditApplication,
    OrgCreditApplicationAttachment,
    OrgCreditApplicationTradeReference,
    OrgCreditLimitIncreaseRequest,
)
from app.modules.org_credit_applications.repository import (
    OrgCreditApplicationAttachmentRepository,
    OrgCreditApplicationDraftRepository,
    OrgCreditApplicationRepository,
    OrgCreditApplicationTradeReferenceRepository,
    OrgCreditLimitIncreaseRequestRepository,
)
from app.modules.org_credit_applications.v1.schemas import (
    ApproveCreditLimitIncreaseRequestBody,
    BankReferenceLetterResponse,
    BankReferenceResponse,
    CreateCreditLimitIncreaseRequestBody,
    CreditApplicationCooldownSnippet,
    CreditApplicationCurrentDetailView,
    CreditApplicationDetailView,
    CreditApplicationDraftApplicationView,
    CreditApplicationDraftDetail,
    CreditApplicationDraftListItem,
    CreditApplicationListItem,
    CreditApplicationSubmissionValidator,
    CreditLimitIncreaseRequestResponse,
    DraftCreatorRef,
    FileUploadFailure,
    TradeReferenceInput,
    TradeReferenceResponse,
    UserRef,
)
from app.modules.org_credit_settings.enums import CreditLimitAdjustmentReason
from app.modules.org_credit_settings.service import OrgCreditSettingsService
from app.modules.organizations.repository import OrganizationRepository
from app.modules.user.models import User
from app.storage.upload import delete_from_r2, generate_document_url, upload_to_r2

logger = structlog.get_logger()

_DOC_URL_TTL = 3600
_MAX_TRADE_REFERENCES = 5

_TERMINAL_STATUSES = frozenset({
    CreditApplicationStatus.APPROVED,
    CreditApplicationStatus.REJECTED,
    CreditApplicationStatus.WITHDRAWN,
    CreditApplicationStatus.CANCELLED,
})

_APPLICATION_COLUMNS = frozenset({
    "company_registration_number", "vat_registration_number", "industry",
    "number_of_employees", "date_of_incorporation", "years_trading",
    "annual_turnover", "net_profit",
    "bank_name", "bank_sort_code", "bank_account_number_last4",
    "bank_account_type",
    "requested_credit_limit", "requested_payment_terms_days",
    "expected_monthly_spend", "seasonal_peaks", "justification",
    "director_signatory_name", "director_signatory_position",
    "declaration_date", "consent_credit_check",
    "consent_terms_and_conditions", "consent_data_processing",
})


def _make_bank_reference_r2_key(org_id: str, application_id: str, filename: str) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe = re.sub(r"[^\w.\-]", "_", filename)[:100]
    return f"organizations/{org_id}/application/bank_reference/{application_id}/{ts}_{safe}"


async def _delete_r2_key_safe(key: str) -> None:
    try:
        await delete_from_r2(key)
    except Exception:
        logger.warning("org_credit_application.r2_delete_failed", key=key, exc_info=True)


def _draft_list_actor(user: User | None) -> Literal["ADMIN", "CLIENT"] | None:
    if user is None:
        return None
    if user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
        return "ADMIN"
    if user.role == UserRole.CUSTOMER_B2B:
        return "CLIENT"
    return None


def _trade_ref_to_response(ref: OrgCreditApplicationTradeReference) -> TradeReferenceResponse:
    return TradeReferenceResponse(
        id=ref.id,
        ref_index=ref.ref_index,
        company_name=ref.company_name,
        contact_person=ref.contact_person,
        contact_phone=ref.contact_phone,
        contact_email=ref.contact_email,
        account_number_reference=ref.account_number_reference,
        credit_limit_with_reference=ref.credit_limit_with_reference,
        relationship_duration=ref.relationship_duration,
        verification_status=ref.verification_status,
        verified_at=ref.verified_at,
        verified_by_user_id=ref.verified_by_user_id,
        created_at=ref.created_at,
        updated_at=ref.updated_at,
    )


def _loaded_relationship(app: OrgCreditApplication, name: str) -> User | None:
    return app.__dict__.get(name)


def _user_ref(user: User | None) -> UserRef | None:
    if user is None:
        return None
    return UserRef(id=user.id, first_name=user.first_name, last_name=user.last_name)


def _loaded_trade_references(
    app: OrgCreditApplication,
    refs: list[OrgCreditApplicationTradeReference] | None = None,
) -> list[TradeReferenceResponse]:
    try:
        return [_trade_ref_to_response(r) for r in app.trade_references]
    except Exception:
        return [_trade_ref_to_response(r) for r in refs] if refs else []


def _application_to_list_item(app: OrgCreditApplication) -> CreditApplicationListItem:
    return CreditApplicationListItem(
        id=app.id,
        application_number=app.application_number,
        status=app.status,
        submitted_at=app.submitted_at,
        requested_credit_limit=app.requested_credit_limit,
        assigned_reviewer=_user_ref(_loaded_relationship(app, "reviewer")),
    )


def _application_to_detail_view(
    app: OrgCreditApplication,
    bank_reference: BankReferenceResponse | None,
    refs: list[OrgCreditApplicationTradeReference] | None = None,
) -> CreditApplicationDetailView:
    return CreditApplicationDetailView(
        id=app.id,
        created_at=app.created_at,
        updated_at=app.updated_at,
        organization_id=app.organization_id,
        application_number=app.application_number,
        state=app.state,
        status=app.status,
        company_registration_number=app.company_registration_number,
        vat_registration_number=app.vat_registration_number,
        industry=app.industry,
        number_of_employees=app.number_of_employees,
        date_of_incorporation=app.date_of_incorporation,
        years_trading=app.years_trading,
        annual_turnover=app.annual_turnover,
        net_profit=app.net_profit,
        trade_references=_loaded_trade_references(app, refs),
        bank_reference=bank_reference,
        requested_credit_limit=app.requested_credit_limit,
        requested_payment_terms_days=app.requested_payment_terms_days,
        expected_monthly_spend=app.expected_monthly_spend,
        seasonal_peaks=app.seasonal_peaks,
        justification=app.justification,
        director_signatory_name=app.director_signatory_name,
        director_signatory_position=app.director_signatory_position,
        declaration_date=app.declaration_date,
        consent_credit_check=app.consent_credit_check,
        consent_terms_and_conditions=app.consent_terms_and_conditions,
        consent_data_processing=app.consent_data_processing,
        submitted_by=_user_ref(_loaded_relationship(app, "submitted_by_user")),
        assigned_reviewer=_user_ref(_loaded_relationship(app, "reviewer")),
        submitted_at=app.submitted_at,
        reviewer_assigned_at=app.reviewer_assigned_at,
        references_verified_at=app.references_verified_at,
        decided_at=app.decided_at,
        approved_at=app.approved_at,
        approved_by=_user_ref(_loaded_relationship(app, "approved_by_user")),
        rejected_at=app.rejected_at,
        rejected_by=_user_ref(_loaded_relationship(app, "rejected_by_user")),
        cancelled_at=app.cancelled_at,
        cancelled_by=_user_ref(_loaded_relationship(app, "cancelled_by_user")),
        withdrawn_at=app.withdrawn_at,
        withdrawn_by=_user_ref(_loaded_relationship(app, "withdrawn_by_user")),
        approved_credit_limit=app.approved_credit_limit,
        approved_payment_terms_days=app.approved_payment_terms_days,
        review_frequency=app.review_frequency,
        approval_notes=app.approval_notes,
        rejection_category=app.rejection_category,
        rejection_reason=app.rejection_reason,
        cancellation_reason=app.cancellation_reason,
        internal_notes=app.internal_notes,
        deleted_at=app.deleted_at,
    )


def _application_to_draft_view(
    app: OrgCreditApplication,
    bank_reference: BankReferenceResponse | None,
    refs: list[OrgCreditApplicationTradeReference] | None = None,
) -> CreditApplicationDraftApplicationView:
    return CreditApplicationDraftApplicationView(
        company_registration_number=app.company_registration_number,
        vat_registration_number=app.vat_registration_number,
        industry=app.industry,
        number_of_employees=app.number_of_employees,
        date_of_incorporation=app.date_of_incorporation,
        years_trading=app.years_trading,
        annual_turnover=app.annual_turnover,
        net_profit=app.net_profit,
        trade_references=_loaded_trade_references(app, refs),
        bank_reference=bank_reference,
        requested_credit_limit=app.requested_credit_limit,
        requested_payment_terms_days=app.requested_payment_terms_days,
        expected_monthly_spend=app.expected_monthly_spend,
        seasonal_peaks=app.seasonal_peaks,
        justification=app.justification,
        director_signatory_name=app.director_signatory_name,
        director_signatory_position=app.director_signatory_position,
        declaration_date=app.declaration_date,
        consent_credit_check=app.consent_credit_check,
        consent_terms_and_conditions=app.consent_terms_and_conditions,
        consent_data_processing=app.consent_data_processing,
    )


def _caller_role_str(caller: AuthUser) -> str:
    return caller.role if isinstance(caller.role, str) else caller.role.value


def _user_to_schema(user: User | None) -> UserSchema | None:
    if user is None:
        return None
    return UserSchema(id=user.id, first_name=user.first_name, last_name=user.last_name)


class OrgCreditApplicationService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = OrgCreditApplicationRepository(session)
        self._draft_repo = OrgCreditApplicationDraftRepository(session)
        self._trade_ref_repo = OrgCreditApplicationTradeReferenceRepository(session)
        self._credit_report_repo = OrgCreditReportRepository(session)
        self._investigation_repo = OrgCreditInvestigationRepository(session)
        self._attachment_repo = OrgCreditApplicationAttachmentRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._org_credit_settings = OrgCreditSettingsService(session, request)
        self._limit_increase_svc = OrgCreditLimitIncreaseRequestService(session, request)
        self._audit = AuditService(session)
        self._ip = request.client.host if request and request.client else None
        self._ua = request.headers.get("user-agent") if request else None

    def _ensure_org_mutable_for_member(self, app: OrgCreditApplication, _caller: AuthUser) -> None:
        if app.state != CreditApplicationLifecycleState.DRAFT:
            raise ForbiddenError("Organisation users can only edit applications in draft status.")

    def _ensure_not_terminal(self, app: OrgCreditApplication) -> None:
        if app.status in _TERMINAL_STATUSES:
            raise ValidationError("This application is closed and cannot be changed.")

    async def _sync_trade_references(
        self,
        application_id: str,
        inputs: list[TradeReferenceInput] | None,
    ) -> list[OrgCreditApplicationTradeReference]:
        if inputs is None:
            return await self._trade_ref_repo.list_by_application(application_id)
        await self._trade_ref_repo.delete_all_for_application(application_id)
        created = []
        for i, inp in enumerate(inputs):
            data = inp.model_dump(exclude_unset=True)
            data["application_id"] = application_id
            data["ref_index"] = i
            ref = await self._trade_ref_repo.create(data)
            created.append(ref)
        return created

    def _extract_application_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in data.items() if k in _APPLICATION_COLUMNS}

    def application_to_list_item(self, app: OrgCreditApplication) -> CreditApplicationListItem:
        return _application_to_list_item(app)

    def applications_to_list_items(
        self,
        apps: list[OrgCreditApplication],
    ) -> list[CreditApplicationListItem]:
        return [_application_to_list_item(a) for a in apps]

    async def application_to_detail_view(self, app: OrgCreditApplication) -> CreditApplicationDetailView:
        attachment = await self._attachment_repo.get_bank_reference(app.id)
        bank_reference = self._to_bank_reference_response(app, attachment)
        return _application_to_detail_view(app, bank_reference)

    async def application_to_draft_view(self, app: OrgCreditApplication) -> CreditApplicationDraftApplicationView:
        attachment = await self._attachment_repo.get_bank_reference(app.id)
        bank_reference = self._to_bank_reference_response(app, attachment)
        return _application_to_draft_view(app, bank_reference)

    async def create(
        self,
        organization_id: str,
        *,
        data: dict[str, Any],
        trade_references: list[TradeReferenceInput] | None,
        caller: AuthUser,
    ) -> OrgCreditApplication:
        await self._org_repo.get_by_id_or_404(organization_id)

        app_fields = self._extract_application_fields(data)
        app_fields["organization_id"] = organization_id
        app_fields["state"] = CreditApplicationLifecycleState.ACTIVE
        app_fields["submitted_by_user_id"] = caller.id
        app_fields["status"] = CreditApplicationStatus.SUBMITTED
        app_fields["submitted_at"] = datetime.now(UTC)
        app_fields["application_number"] = await self._repo.generate_application_number()
        app = await self._repo.create(app_fields)
        refs = await self._sync_trade_references(app.id, trade_references)

        self._validate_for_submission(app, refs)

        await self._audit.log(
            action="org_credit_application.created",
            entity_type="org_credit_application",
            entity_id=app.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={"organization_id": organization_id, "application_number": app.application_number},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Credit application {app.application_number} submitted",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_SUBMITTED,
            severity="NOTICE",
        )
        logger.info("org_credit_application.created", application_id=app.id, organization_id=organization_id)

        return app

    def _validate_for_submission(
        self,
        app: OrgCreditApplication,
        refs: list[OrgCreditApplicationTradeReference],
    ) -> None:
        payload: dict[str, Any] = {
            "company_registration_number": app.company_registration_number or "",
            "years_trading": app.years_trading if app.years_trading is not None else -1,
            "annual_turnover": app.annual_turnover or Decimal(0),
            "bank_name": app.bank_name or "",
            "bank_sort_code": app.bank_sort_code or "",
            "bank_account_number_last4": app.bank_account_number_last4 or "",
            "requested_credit_limit": app.requested_credit_limit or Decimal(0),
            "requested_payment_terms_days": app.requested_payment_terms_days,
            "expected_monthly_spend": app.expected_monthly_spend or Decimal(0),
            "director_signatory_name": app.director_signatory_name or "",
            "director_signatory_position": app.director_signatory_position or "",
            "consent_credit_check": app.consent_credit_check,
            "consent_terms_and_conditions": app.consent_terms_and_conditions,
            "consent_data_processing": app.consent_data_processing,
            "trade_references": [
                {
                    "company_name": r.company_name,
                    "contact_person": r.contact_person,
                    "contact_phone": r.contact_phone,
                    "contact_email": r.contact_email,
                    "relationship_duration": r.relationship_duration,
                }
                for r in refs
            ],
        }
        try:
            CreditApplicationSubmissionValidator.model_validate(payload)
        except PydanticValidationError as exc:
            messages = [f"{'.'.join(str(l) for l in e['loc'])}: {e['msg']}" for e in exc.errors()]
            raise ValidationError(f"Cannot submit application: {'; '.join(messages)}") from exc

    async def save_draft(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        data: dict[str, Any],
        trade_references: list[TradeReferenceInput] | None = None,
    ) -> tuple[Any, str]:
        await self._org_repo.get_by_id_or_404(organization_id)

        app_fields = self._extract_application_fields(data)
        app_fields["organization_id"] = organization_id
        app_fields["status"] = CreditApplicationStatus.SUBMITTED
        app_fields["state"] = CreditApplicationLifecycleState.DRAFT
        app = await self._repo.create(app_fields)

        if trade_references:
            await self._sync_trade_references(app.id, trade_references)

        draft = await self._draft_repo.create({
            "application_id": app.id,
            "created_by_id": caller.id,
        })

        await self._audit.log(
            action="org_credit_application.draft_saved",
            entity_type="org_credit_application_draft",
            entity_id=draft.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={"draft_number": draft.draft_number, "application_id": app.id},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Saved credit application draft {draft.draft_number}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_SUBMITTED,
            severity="INFO",
        )
        logger.info("org_credit_application.draft_saved", draft_id=draft.id, application_id=app.id)

        return draft, app.id

    async def update_draft(
        self,
        organization_id: str,
        draft_id: str,
        *,
        caller: AuthUser,
        data: dict[str, Any],
        trade_references: list[TradeReferenceInput] | None = None,
    ) -> tuple[Any, OrgCreditApplication]:
        await self._org_repo.get_by_id_or_404(organization_id)
        draft = await self._draft_repo.get_by_id_with_application_or_404(draft_id, organization_id)
        app = draft.application

        if app.state != CreditApplicationLifecycleState.DRAFT:
            raise ValidationError("Cannot update a draft that is no longer in draft status.")
        self._ensure_org_mutable_for_member(app, caller)
        if draft.published_by_id is not None:
            raise ValidationError("This draft has already been published.")

        app_fields = self._extract_application_fields(data)
        if app_fields:
            app = await self._repo.update_by_id(
                app.id, app_fields,
                organization_id=organization_id,
            )

        if trade_references is not None:
            await self._sync_trade_references(app.id, trade_references)

        if app_fields:
            await self._audit.log(
                action="org_credit_application.draft_updated",
                entity_type="org_credit_application_draft",
                entity_id=draft_id,
                user_id=caller.id,
                user_role=_caller_role_str(caller),
                new_value=app_fields,
                ip_address=self._ip,
                user_agent=self._ua,
                reason=f"Updated {len(app_fields)} field(s) on credit application draft",
                organization_id=organization_id,
                category=AuditCategory.CREDIT,
                event_type=AuditEventType.CREDIT_APPLICATION_SUBMITTED,
                severity="INFO",
            )

        app = await self._repo.get_active_with_refs_or_404(app.id, organization_id)
        return draft, app

    async def load_draft_row(
        self,
        organization_id: str,
        draft_id: str,
    ) -> tuple[Any, OrgCreditApplication]:
        await self._org_repo.get_by_id_or_404(organization_id)
        draft = await self._draft_repo.get_by_id_with_application_or_404(draft_id, organization_id)
        return draft, draft.application

    async def get_draft(
        self,
        organization_id: str,
        draft_id: str,
    ) -> CreditApplicationDraftDetail:
        draft, app = await self.load_draft_row(organization_id, draft_id)
        application = await self.application_to_draft_view(app)
        return CreditApplicationDraftDetail(
            id=draft.id,
            draft_number=draft.draft_number,
            created_at=draft.created_at,
            application=application,
        )

    async def list_drafts(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[CreditApplicationDraftListItem], int]:
        await self._org_repo.get_by_id_or_404(organization_id)
        rows, total = await self._draft_repo.list_open_for_org(organization_id, page=page, size=size)
        items = []
        for d in rows:
            u = d.created_by_user
            items.append(
                CreditApplicationDraftListItem(
                    id=d.id,
                    draft_number=d.draft_number,
                    created_at=d.created_at,
                    actor=_draft_list_actor(u),
                    created_by=DraftCreatorRef(id=u.id, email=u.email) if u else None,
                )
            )
        return items, total

    async def delete_draft(
        self,
        organization_id: str,
        draft_id: str,
        *,
        caller: AuthUser,
    ) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        draft = await self._draft_repo.get_by_id_with_application_or_404(draft_id, organization_id)
        app = draft.application
        if app.state != CreditApplicationLifecycleState.DRAFT:
            raise ValidationError("Cannot delete a draft that is no longer in draft status.")
        self._ensure_org_mutable_for_member(app, caller)

        app_id = app.id
        draft_number = draft.draft_number

        attachments = await self._attachment_repo.list_by_application(app_id)
        for attachment in attachments:
            await _delete_r2_key_safe(attachment.r2_key)

        await self._repo.hard_delete(app_id, organization_id=organization_id)

        await self._audit.log(
            action="org_credit_application.draft_deleted",
            entity_type="org_credit_application_draft",
            entity_id=draft_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value={"application_id": app_id, "draft_number": draft_number},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Deleted credit application draft {draft_number}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_REJECTED,
            severity="NOTICE",
        )
        logger.info("org_credit_application.draft_deleted", draft_id=draft_id, application_id=app_id)

    async def publish_draft(
        self,
        organization_id: str,
        draft_id: str,
        *,
        caller: AuthUser,
        data: dict[str, Any],
        trade_references: list[TradeReferenceInput] | None,
    ) -> tuple[Any, OrgCreditApplication]:
        await self._org_repo.get_by_id_or_404(organization_id)
        draft = await self._draft_repo.get_by_id_with_application_or_404(draft_id, organization_id)
        app = draft.application

        if app.state != CreditApplicationLifecycleState.DRAFT:
            raise ValidationError("Only draft applications can be published.")
        self._ensure_org_mutable_for_member(app, caller)
        if draft.published_by_id is not None:
            raise ValidationError("This draft has already been published.")

        app_fields = self._extract_application_fields(data)
        if app_fields:
            app = await self._repo.update_by_id(
                app.id, app_fields,
                organization_id=organization_id,
            )

        refs = await self._sync_trade_references(app.id, trade_references)
        self._validate_for_submission(app, refs)

        app_number = await self._repo.generate_application_number()
        app = await self._repo.update_by_id(
            app.id,
            {
                "application_number": app_number,
                "status": CreditApplicationStatus.SUBMITTED,
                "state": CreditApplicationLifecycleState.ACTIVE,
                "submitted_at": datetime.now(UTC),
                "submitted_by_user_id": caller.id,
            },
            organization_id=organization_id,
        )
        await self._draft_repo.update_by_id(draft.id, {"published_by_id": caller.id})

        await self._audit.log(
            action="org_credit_application.draft_published",
            entity_type="org_credit_application",
            entity_id=app.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={"draft_id": draft_id, "application_number": app_number},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Published draft as credit application {app_number}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_SUBMITTED,
            severity="NOTICE",
        )
        logger.info("org_credit_application.draft_published", application_id=app.id, draft_id=draft_id)

        app = await self._repo.get_active_with_refs_or_404(app.id, organization_id)
        return draft, app

    async def list_for_org(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
        status: CreditApplicationStatus | None = None,
        search: str | None = None,
    ) -> tuple[list[OrgCreditApplication], int]:
        await self._org_repo.get_by_id_or_404(organization_id)
        return await self._repo.list_for_org(organization_id, page=page, size=size, status=status, search=search)

    def _to_bank_reference_response(
        self,
        app: OrgCreditApplication,
        attachment: OrgCreditApplicationAttachment | None,
    ) -> BankReferenceResponse | None:
        has_bank_fields = any([
            app.bank_name,
            app.bank_sort_code,
            app.bank_account_number_last4,
            app.bank_account_type,
        ])
        if attachment is None and not has_bank_fields:
            return None
        reference_letter = None
        if attachment is not None:
            reference_letter = BankReferenceLetterResponse(
                id=attachment.id,
                url=generate_document_url(attachment.r2_key, expiry_seconds=_DOC_URL_TTL),
                filename=attachment.filename,
            )
        return BankReferenceResponse(
            bank_name=app.bank_name,
            bank_sort_code=app.bank_sort_code,
            bank_account_number_last4=app.bank_account_number_last4,
            bank_account_type=app.bank_account_type,
            reference_letter=reference_letter,
        )

    def _to_credit_report_response(self, report: OrgCreditReport | None) -> CreditReportResponse | None:
        if report is None:
            return None
        return CreditReportResponse.from_report(report)

    def _to_investigation_response(
        self,
        investigation: OrgCreditInvestigation | None,
    ) -> CreditInvestigationResponse | None:
        if investigation is None:
            return None
        return CreditInvestigationResponse(
            id=investigation.id,
            status=investigation.status,
            provider_reference=investigation.provider_reference,
            connect_id=investigation.connect_id,
            reg_no=investigation.reg_no,
            company_name=investigation.company_name,
            country=investigation.country,
            requested_at=investigation.requested_at,
            completed_at=investigation.completed_at,
            failure_reason=investigation.failure_reason,
        )

    async def _cooldown_snippet_if_rejected(
        self,
        organization_id: str,
        app: OrgCreditApplication,
    ) -> CreditApplicationCooldownSnippet | None:
        if app.status != CreditApplicationStatus.REJECTED:
            return None
        payload = await self._org_credit_settings.get_active_cooldown_public_payload(organization_id)
        return CreditApplicationCooldownSnippet(
            active=payload["active"],
            summary=payload["summary"],
        )

    async def get_detail(
        self,
        organization_id: str,
        application_id: str,
    ) -> CreditApplicationDetailView:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_with_refs_or_404(application_id, organization_id)
        attachment = await self._attachment_repo.get_bank_reference(application_id)
        bank_reference = self._to_bank_reference_response(app, attachment)
        report = await self._credit_report_repo.get_by_org_id(organization_id)
        view = _application_to_detail_view(app, bank_reference)
        return view.model_copy(update={
            "credit_report": self._to_credit_report_response(report),
        })

    async def get_detail_latest(self, organization_id: str) -> CreditApplicationCurrentDetailView:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_latest_active_with_refs_or_404(organization_id)
        attachment = await self._attachment_repo.get_bank_reference(app.id)
        bank_reference = self._to_bank_reference_response(app, attachment)
        report = await self._credit_report_repo.get_by_org_id(organization_id)
        view = _application_to_detail_view(app, bank_reference)
        cooldown = await self._cooldown_snippet_if_rejected(organization_id, app)
        pending_credit_limit_increase_request = None
        if app.status == CreditApplicationStatus.APPROVED:
            pending_credit_limit_increase_request = (
                await self._limit_increase_svc.get_pending_for_current_application_detail(organization_id)
            )
        return CreditApplicationCurrentDetailView.model_validate({
            **view.model_dump(),
            "credit_report": self._to_credit_report_response(report),
            "cooldown": cooldown,
            "pending_credit_limit_increase_request": pending_credit_limit_increase_request,
        })

    async def edit_section(
        self,
        organization_id: str,
        application_id: str,
        *,
        caller: AuthUser,
        updates: dict[str, Any],
        section_name: str = "application",
    ) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_or_404(application_id, organization_id)
        self._ensure_not_terminal(app)

        app_fields = self._extract_application_fields(updates)
        if not app_fields:
            return

        await self._repo.update_by_id(
            application_id, app_fields,
            organization_id=organization_id,
        )

        await self._audit.log(
            action=f"org_credit_application.{section_name}_edited",
            entity_type="org_credit_application",
            entity_id=application_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value=app_fields,
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Edited '{section_name}' section ({len(app_fields)} field(s))",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_SUBMITTED,
            severity="INFO",
        )

    async def add_trade_reference(
        self,
        organization_id: str,
        application_id: str,
        *,
        caller: AuthUser,
        data: TradeReferenceInput,
    ) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_or_404(application_id, organization_id)
        self._ensure_not_terminal(app)

        count = await self._trade_ref_repo.count_by_application(application_id)
        if count >= _MAX_TRADE_REFERENCES:
            raise ValidationError(f"Maximum {_MAX_TRADE_REFERENCES} trade references allowed.")

        next_idx = await self._trade_ref_repo.next_ref_index(application_id)
        ref_data = data.model_dump(exclude_unset=True)
        ref_data["application_id"] = application_id
        ref_data["ref_index"] = next_idx
        await self._trade_ref_repo.create(ref_data)

        await self._audit.log(
            action="org_credit_application.trade_reference_added",
            entity_type="org_credit_application",
            entity_id=application_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={"ref_index": next_idx},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Added trade reference #{next_idx}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_SUBMITTED,
            severity="INFO",
        )

    async def update_trade_reference(
        self,
        organization_id: str,
        application_id: str,
        ref_id: str,
        *,
        caller: AuthUser,
        updates: dict[str, Any],
    ) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_or_404(application_id, organization_id)
        self._ensure_not_terminal(app)

        ref = await self._trade_ref_repo.get_by_id_and_application_or_404(ref_id, application_id)
        ref_updates = {k: v for k, v in updates.items() if hasattr(ref, k) and k != "id"}
        if ref_updates:
            await self._trade_ref_repo.update_by_id(ref_id, ref_updates)

    async def verify_trade_reference(
        self,
        organization_id: str,
        application_id: str,
        ref_id: str,
        *,
        caller: AuthUser,
        status: TradeReferenceVerificationStatus,
    ) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_or_404(application_id, organization_id)
        self._ensure_not_terminal(app)

        if app.status not in (
            CreditApplicationStatus.REVIEWER_ASSIGNED,
            CreditApplicationStatus.REFERENCES_VERIFIED,
            CreditApplicationStatus.CREDIT_CHECK_FAILED,
            CreditApplicationStatus.CREDIT_CHECK_COMPLETED,
            CreditApplicationStatus.READY_FOR_DECISION,
        ):
            raise ValidationError("Trade references can only be verified during review.")

        ref = await self._trade_ref_repo.get_by_id_and_application_or_404(ref_id, application_id)
        ref_updates: dict[str, Any] = {"verification_status": status}
        if status in (TradeReferenceVerificationStatus.VERIFIED, TradeReferenceVerificationStatus.DECLINED, TradeReferenceVerificationStatus.UNABLE_TO_VERIFY):
            ref_updates["verified_at"] = datetime.now(UTC)
            ref_updates["verified_by_user_id"] = caller.id
        await self._trade_ref_repo.update_by_id(ref_id, ref_updates)

        app_updates: dict[str, Any] = {}
        unverified = await self._trade_ref_repo.count_unverified(application_id)
        if unverified == 0:
            app_updates["references_verified_at"] = datetime.now(UTC)
            app_updates["status"] = CreditApplicationStatus.REFERENCES_VERIFIED

        if app_updates:
            await self._repo.update_by_id(
                application_id, app_updates,
                organization_id=organization_id,
            )

        await self._audit.log(
            action="org_credit_application.trade_reference_verified",
            entity_type="org_credit_application",
            entity_id=application_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={"ref_id": ref_id, "status": status.value},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Trade reference verification set to {status.value}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_SUBMITTED,
            severity="INFO",
        )

    async def assign_reviewer(
        self,
        organization_id: str,
        application_id: str,
        *,
        caller: AuthUser,
        reviewer_user_id: str,
    ) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_or_404(application_id, organization_id)
        self._ensure_not_terminal(app)

        if app.status not in (
            CreditApplicationStatus.SUBMITTED,
            CreditApplicationStatus.REVIEWER_ASSIGNED,
            CreditApplicationStatus.REFERENCES_VERIFIED,
            CreditApplicationStatus.CREDIT_CHECK_COMPLETED,
            CreditApplicationStatus.CREDIT_CHECK_FAILED,
            CreditApplicationStatus.READY_FOR_DECISION,
        ):
            raise ValidationError("Reviewer can only be assigned while the application is in progress.")

        await self._repo.update_by_id(
            application_id,
            {
                "assigned_reviewer_user_id": reviewer_user_id,
                "status": CreditApplicationStatus.REVIEWER_ASSIGNED,
                "reviewer_assigned_at": datetime.now(UTC),
            },
            organization_id=organization_id,
        )

        await self._audit.log(
            action="org_credit_application.assign_reviewer",
            entity_type="org_credit_application",
            entity_id=application_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={"reviewer_user_id": reviewer_user_id},
            ip_address=self._ip,
            user_agent=self._ua,
            reason="Assigned reviewer to credit application",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_ASSIGNED,
            severity="NOTICE",
        )

    async def run_credit_check(
        self,
        organization_id: str,
        application_id: str,
        *,
        caller: AuthUser,
    ) -> CreditCheckResult:
        org = await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_or_404(application_id, organization_id)
        self._ensure_not_terminal(app)

        if app.status not in (
            CreditApplicationStatus.REVIEWER_ASSIGNED,
            CreditApplicationStatus.REFERENCES_VERIFIED,
            CreditApplicationStatus.CREDIT_CHECK_FAILED,
        ):
            raise ValidationError("Credit check can only run while the application is under review.")

        reg_no = app.company_registration_number
        company_name = getattr(org, "trading_name", None)

        try:
            connect_id, report = await run_credit_assessment(reg_no=reg_no)
        except CreditsafeNoCompanyFound:
            investigation = await self._open_fresh_investigation(
                organization_id=organization_id,
                application_id=application_id,
                reg_no=reg_no,
                company_name=company_name,
                caller=caller,
            )
            await self._repo.update_by_id(
                application_id,
                {
                    "status": CreditApplicationStatus.CREDIT_CHECK_INVESTIGATION_PROGRESS,
                    "credit_check_run_at": datetime.now(UTC),
                },
                organization_id=organization_id,
            )
            await self._audit.log(
                action="org_credit_application.credit_check_investigation_opened",
                entity_type="org_credit_application",
                entity_id=application_id,
                user_id=caller.id,
                user_role=_caller_role_str(caller),
                new_value={
                    "status": CreditApplicationStatus.CREDIT_CHECK_INVESTIGATION_PROGRESS.value,
                    "investigation_id": investigation.id,
                },
                ip_address=self._ip,
                user_agent=self._ua,
                reason=f"Opened fresh Creditsafe investigation for {reg_no or 'company'}",
                organization_id=organization_id,
                category=AuditCategory.CREDIT,
                event_type=AuditEventType.CREDIT_SCORE_RECALCULATED,
                severity="NOTICE",
            )
            return CreditCheckResult(
                outcome="INVESTIGATION_PROGRESS",
                investigation=self._to_investigation_response(investigation),
                message="No credit report available yet. A fresh investigation has been ordered and typically takes 2-3 business days.",
            )
        except Exception:
            logger.exception("credit_check.failed", application_id=application_id)
            await self._repo.update_by_id(
                application_id,
                {
                    "status": CreditApplicationStatus.CREDIT_CHECK_FAILED,
                    "credit_check_run_at": datetime.now(UTC),
                },
                organization_id=organization_id,
            )
            await self._audit.log(
                action="org_credit_application.credit_check_failed",
                entity_type="org_credit_application",
                entity_id=application_id,
                user_id=caller.id,
                user_role=_caller_role_str(caller),
                new_value={"status": CreditApplicationStatus.CREDIT_CHECK_FAILED.value},
                ip_address=self._ip,
                user_agent=self._ua,
                reason="Creditsafe credit check failed",
                organization_id=organization_id,
                category=AuditCategory.CREDIT,
                event_type=AuditEventType.CREDIT_SCORE_RECALCULATED,
                severity="WARNING",
            )
            return CreditCheckResult(
                outcome="FAILED",
                message="Credit check failed. Please try again later.",
            )

        report_data = parse_creditsafe_report(connect_id, report, caller.id)
        stored_report = await self._credit_report_repo.upsert_for_org(organization_id, report_data)

        await self._repo.update_by_id(
            application_id,
            {
                "status": CreditApplicationStatus.CREDIT_CHECK_COMPLETED,
                "credit_check_run_at": datetime.now(UTC),
            },
            organization_id=organization_id,
        )
        await self._audit.log(
            action="org_credit_application.credit_check_completed",
            entity_type="org_credit_application",
            entity_id=application_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={"status": CreditApplicationStatus.CREDIT_CHECK_COMPLETED.value},
            ip_address=self._ip,
            user_agent=self._ua,
            reason="Creditsafe credit check completed",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_SCORE_RECALCULATED,
            severity="NOTICE",
        )
        return CreditCheckResult(
            outcome="COMPLETED",
            report=self._to_credit_report_response(stored_report),
            message="Credit check completed successfully.",
        )

    async def _open_fresh_investigation(
        self,
        *,
        organization_id: str,
        application_id: str,
        reg_no: str | None,
        company_name: str | None,
        caller: AuthUser,
    ) -> OrgCreditInvestigation:
        existing = await self._investigation_repo.get_active_for_application(
            organization_id, application_id,
        )
        if existing is not None:
            return existing

        payload = {
            "reg_no": reg_no,
            "company_name": company_name,
            "country": "GB",
        }
        fresh = await request_fresh_investigation(
            reg_no=reg_no,
            company_name=company_name,
        )
        provider_reference = fresh.get("reference") or fresh.get("id") or fresh.get("investigationId")
        return await self._investigation_repo.create({
            "organization_id": organization_id,
            "application_id": application_id,
            "status": OrgCreditInvestigationStatus.IN_PROGRESS,
            "reg_no": reg_no,
            "company_name": company_name,
            "country": "GB",
            "provider_reference": provider_reference,
            "requested_by_user_id": caller.id,
            "requested_at": datetime.now(UTC),
            "raw_request": payload,
            "raw_response": fresh,
        })

    async def refresh_credit_check(
        self,
        organization_id: str,
        application_id: str,
        *,
        caller: AuthUser,
    ) -> CreditCheckResult:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_or_404(application_id, organization_id)
        self._ensure_not_terminal(app)

        if app.status != CreditApplicationStatus.CREDIT_CHECK_INVESTIGATION_PROGRESS:
            raise ValidationError(
                "Credit check refresh is only available while an investigation is in progress.",
            )

        investigation = await self._investigation_repo.get_active_for_application(
            organization_id, application_id,
        )
        if investigation is None:
            raise NotFoundError(resource="org_credit_investigation", id=application_id)

        reg_no = investigation.reg_no or app.company_registration_number

        try:
            connect_id, report = await run_credit_assessment(reg_no=reg_no)
        except CreditsafeNoCompanyFound:
            return CreditCheckResult(
                outcome="INVESTIGATION_PROGRESS",
                investigation=self._to_investigation_response(investigation),
                message="Fresh investigation is still in progress with Creditsafe. Please try again later.",
            )
        except Exception as exc:
            logger.exception(
                "credit_check.refresh_failed",
                application_id=application_id,
                investigation_id=investigation.id,
            )
            await self._investigation_repo.update_by_id(
                investigation.id,
                {
                    "status": OrgCreditInvestigationStatus.FAILED,
                    "completed_at": datetime.now(UTC),
                    "failure_reason": str(exc)[:1000],
                },
            )
            await self._repo.update_by_id(
                application_id,
                {
                    "status": CreditApplicationStatus.CREDIT_CHECK_FAILED,
                    "credit_check_run_at": datetime.now(UTC),
                },
                organization_id=organization_id,
            )
            await self._audit.log(
                action="org_credit_application.credit_check_refresh_failed",
                entity_type="org_credit_application",
                entity_id=application_id,
                user_id=caller.id,
                user_role=_caller_role_str(caller),
                new_value={
                    "status": CreditApplicationStatus.CREDIT_CHECK_FAILED.value,
                    "investigation_id": investigation.id,
                },
                ip_address=self._ip,
                user_agent=self._ua,
                reason="Creditsafe investigation refresh failed",
                organization_id=organization_id,
                category=AuditCategory.CREDIT,
                event_type=AuditEventType.CREDIT_SCORE_RECALCULATED,
                severity="WARNING",
            )
            return CreditCheckResult(
                outcome="FAILED",
                message="Credit check refresh failed. Please try again later.",
            )

        report_data = parse_creditsafe_report(connect_id, report, caller.id)
        stored_report = await self._credit_report_repo.upsert_for_org(organization_id, report_data)

        await self._investigation_repo.update_by_id(
            investigation.id,
            {
                "status": OrgCreditInvestigationStatus.COMPLETED,
                "completed_at": datetime.now(UTC),
                "connect_id": connect_id,
            },
        )
        await self._repo.update_by_id(
            application_id,
            {
                "status": CreditApplicationStatus.CREDIT_CHECK_COMPLETED,
                "credit_check_run_at": datetime.now(UTC),
            },
            organization_id=organization_id,
        )
        await self._audit.log(
            action="org_credit_application.credit_check_investigation_completed",
            entity_type="org_credit_application",
            entity_id=application_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={
                "status": CreditApplicationStatus.CREDIT_CHECK_COMPLETED.value,
                "investigation_id": investigation.id,
            },
            ip_address=self._ip,
            user_agent=self._ua,
            reason="Creditsafe investigation completed and report retrieved",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_SCORE_RECALCULATED,
            severity="NOTICE",
        )
        return CreditCheckResult(
            outcome="COMPLETED",
            report=self._to_credit_report_response(stored_report),
            message="Credit report retrieved after fresh investigation completed.",
        )

    async def mark_ready_for_decision(
        self,
        organization_id: str,
        application_id: str,
        *,
        caller: AuthUser,
    ) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_or_404(application_id, organization_id)
        self._ensure_not_terminal(app)

        if app.status not in (
            CreditApplicationStatus.REVIEWER_ASSIGNED,
            CreditApplicationStatus.REFERENCES_VERIFIED,
            CreditApplicationStatus.CREDIT_CHECK_COMPLETED,
            CreditApplicationStatus.CREDIT_CHECK_FAILED,
        ):
            raise ValidationError("Application is not ready for this transition.")
        if app.status != CreditApplicationStatus.CREDIT_CHECK_COMPLETED:
            raise ValidationError("Credit assessment must be completed first.")

        unverified = await self._trade_ref_repo.count_unverified(application_id)
        if unverified > 0:
            raise ValidationError("All trade references must be verified first.")

        await self._repo.update_by_id(
            application_id,
            {"status": CreditApplicationStatus.READY_FOR_DECISION},
            organization_id=organization_id,
        )

    async def approve(
        self,
        organization_id: str,
        application_id: str,
        *,
        caller: AuthUser,
        approved_credit_limit: Decimal,
        approved_payment_terms_days: int,
        review_frequency: Any | None,
        approval_notes: str | None,
    ) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_or_404(application_id, organization_id)
        self._ensure_not_terminal(app)

        if app.status not in (
            CreditApplicationStatus.READY_FOR_DECISION,
            CreditApplicationStatus.CREDIT_CHECK_COMPLETED,
        ):
            raise ValidationError(
                "Application is not in a state that allows approval. "
                f"Current status is {app.status.value}. "
                "Complete the credit assessment first (POST …/credit-check/run) until status is "
                "CREDIT_CHECK_COMPLETED or READY_FOR_DECISION, then approve.",
            )

        now = datetime.now(UTC)
        data: dict[str, Any] = {
            "status": CreditApplicationStatus.APPROVED,
            "approved_credit_limit": approved_credit_limit,
            "approved_payment_terms_days": approved_payment_terms_days,
            "decided_at": now,
            "approved_at": now,
            "approved_by_user_id": caller.id,
        }
        if review_frequency is not None:
            data["review_frequency"] = review_frequency
        if approval_notes is not None:
            data["approval_notes"] = approval_notes

        await self._repo.update_by_id(
            application_id, data,
            organization_id=organization_id,
        )

        credit_service = OrgCreditService(self._session, self._request)
        await credit_service.provision_account_on_approval(
            organization_id,
            caller=caller,
            application_id=application_id,
            approved_credit_limit=approved_credit_limit,
            approved_payment_terms_days=approved_payment_terms_days,
            review_frequency_value=(
                review_frequency.value
                if review_frequency is not None and hasattr(review_frequency, "value")
                else (str(review_frequency) if review_frequency is not None else None)
            ),
        )

        await self._audit.log(
            action="org_credit_application.approved",
            entity_type="org_credit_application",
            entity_id=application_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={
                "status": CreditApplicationStatus.APPROVED.value,
                "approved_credit_limit": str(approved_credit_limit),
                "approved_payment_terms_days": approved_payment_terms_days,
            },
            ip_address=self._ip,
            user_agent=self._ua,
            reason=approval_notes or f"Credit application approved with limit {approved_credit_limit}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_APPROVED,
            severity="NOTICE",
        )
        logger.info("org_credit_application.approved", application_id=application_id)

    async def reject(
        self,
        organization_id: str,
        application_id: str,
        *,
        caller: AuthUser,
        rejection_category: Any,
        detailed_reason: str,
    ) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_or_404(application_id, organization_id)
        self._ensure_not_terminal(app)

        if app.status not in (
            CreditApplicationStatus.REVIEWER_ASSIGNED,
            CreditApplicationStatus.REFERENCES_VERIFIED,
            CreditApplicationStatus.CREDIT_CHECK_COMPLETED,
            CreditApplicationStatus.CREDIT_CHECK_FAILED,
            CreditApplicationStatus.READY_FOR_DECISION,
        ):
            raise ValidationError("Application is not in a state that allows rejection.")

        now = datetime.now(UTC)
        await self._repo.update_by_id(
            application_id,
            {
                "status": CreditApplicationStatus.REJECTED,
                "rejection_category": rejection_category,
                "rejection_reason": detailed_reason,
                "decided_at": now,
                "rejected_at": now,
                "rejected_by_user_id": caller.id,
            },
            organization_id=organization_id,
        )

        await self._audit.log(
            action="org_credit_application.rejected",
            entity_type="org_credit_application",
            entity_id=application_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={
                "status": CreditApplicationStatus.REJECTED.value,
                "rejection_category": (
                    rejection_category.value if hasattr(rejection_category, "value") else str(rejection_category)
                ),
            },
            ip_address=self._ip,
            user_agent=self._ua,
            reason=detailed_reason or "Credit application rejected",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_REJECTED,
            severity="WARNING",
        )
        logger.info("org_credit_application.rejected", application_id=application_id)

        try:
            await self._org_credit_settings.start_cooldown_window(organization_id)
        except ValidationError as exc:
            logger.warning(
                "org_credit_application.reject.cooldown_window_not_started",
                organization_id=organization_id,
                application_id=application_id,
                error=str(exc),
            )

    async def cancel(
        self,
        organization_id: str,
        application_id: str,
        *,
        caller: AuthUser,
        reason: str,
    ) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_or_404(application_id, organization_id)
        self._ensure_not_terminal(app)

        now = datetime.now(UTC)
        await self._repo.update_by_id(
            application_id,
            {
                "status": CreditApplicationStatus.CANCELLED,
                "cancellation_reason": reason,
                "decided_at": now,
                "cancelled_at": now,
                "cancelled_by_user_id": caller.id,
            },
            organization_id=organization_id,
        )
        await self._audit.log(
            action="org_credit_application.cancelled",
            entity_type="org_credit_application",
            entity_id=application_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={"status": CreditApplicationStatus.CANCELLED.value},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=reason or "Credit application cancelled",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_REJECTED,
            severity="NOTICE",
        )

    async def withdraw(
        self,
        organization_id: str,
        application_id: str,
        *,
        caller: AuthUser,
    ) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_or_404(application_id, organization_id)
        self._ensure_not_terminal(app)

        if app.status != CreditApplicationStatus.SUBMITTED:
            raise ValidationError("Withdraw is only allowed for draft or submitted applications.")

        now = datetime.now(UTC)
        await self._repo.update_by_id(
            application_id,
            {
                "status": CreditApplicationStatus.WITHDRAWN,
                "state": CreditApplicationLifecycleState.ACTIVE,
                "withdrawn_at": now,
                "withdrawn_by_user_id": caller.id,
            },
            organization_id=organization_id,
        )

        await self._audit.log(
            action="org_credit_application.withdrawn",
            entity_type="org_credit_application",
            entity_id=application_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={"status": CreditApplicationStatus.WITHDRAWN.value},
            ip_address=self._ip,
            user_agent=self._ua,
            reason="Credit application withdrawn by client",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_REJECTED,
            severity="NOTICE",
        )

    async def delete(self, organization_id: str, application_id: str, *, caller: AuthUser) -> None:
        await self._org_repo.get_by_id_or_404(organization_id)
        app = await self._repo.get_active_or_404(application_id, organization_id)
        org_user_may_delete = app.state == CreditApplicationLifecycleState.DRAFT or app.status == CreditApplicationStatus.WITHDRAWN
        staff_may_delete = caller.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)
        if not org_user_may_delete and not staff_may_delete:
            raise ForbiddenError("Organisation users can only delete draft or withdrawn applications.")
        await self._repo.soft_delete(application_id, organization_id=organization_id)

        await self._audit.log(
            action="org_credit_application.deleted",
            entity_type="org_credit_application",
            entity_id=application_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value={"organization_id": organization_id, "status": app.status.value},
            ip_address=self._ip,
            user_agent=self._ua,
            reason="Credit application deleted",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_APPLICATION_REJECTED,
            severity="WARNING",
        )
        logger.info("org_credit_application.deleted", application_id=application_id)

    async def handle_bank_reference_letter_upload(
        self,
        organization_id: str,
        application_id: str,
        bank_reference_letter_file: tuple[bytes, str, str] | None,
        *,
        caller: AuthUser,
    ) -> list[FileUploadFailure]:
        if bank_reference_letter_file is None:
            return []

        content, filename, mime_type = bank_reference_letter_file
        try:
            key = _make_bank_reference_r2_key(organization_id, application_id, filename)
            await upload_to_r2(key, content, mime_type)
            await self._attachment_repo.create({
                "application_id": application_id,
                "organization_id": organization_id,
                "attachment_type": AttachmentType.BANK_REFERENCE,
                "r2_key": key,
                "filename": filename,
                "uploaded_by": caller.id,
            })
            logger.info("org_credit_application.bank_reference_uploaded", application_id=application_id)
            return []
        except Exception:
            logger.warning("org_credit_application.bank_reference_upload_failed", application_id=application_id, exc_info=True)
            return [
                FileUploadFailure(
                    index=0,
                    filename=filename,
                    reason="Bank reference letter upload failed, please retry.",
                )
            ]

    async def delete_bank_reference_letter(
        self,
        organization_id: str,
        application_id: str,
        attachment_id: str,
        *,
        caller: AuthUser,
    ) -> None:
        app = await self._repo.get_active_or_404(application_id, organization_id)
        self._ensure_org_mutable_for_member(app, caller)
        self._ensure_not_terminal(app)

        attachment = await self._attachment_repo.get_by_id(attachment_id, organization_id=organization_id)
        if (
            attachment is None
            or attachment.application_id != application_id
            or attachment.attachment_type != AttachmentType.BANK_REFERENCE
        ):
            raise NotFoundError(resource="org_credit_application_attachments", id=attachment_id)

        await _delete_r2_key_safe(attachment.r2_key)
        await self._attachment_repo.hard_delete(attachment_id, organization_id=organization_id)


class OrgCreditLimitIncreaseRequestService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = OrgCreditLimitIncreaseRequestRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._account_repo = OrgCreditAccountRepository(session)
        self._settings = OrgCreditSettingsService(session, request)
        self._audit = AuditService(session)
        self._ip = request.client.host if request and request.client else None
        self._ua = request.headers.get("user-agent") if request else None

    def to_response(self, row: OrgCreditLimitIncreaseRequest) -> CreditLimitIncreaseRequestResponse:
        req_u = row.requested_by_user
        rev_u = row.reviewed_by_user
        if req_u is None:
            raise ValidationError("Credit limit increase request is missing requested user data.")
        requested_by = _user_to_schema(req_u)
        if requested_by is None:
            raise ValidationError("Credit limit increase request is missing requested user data.")
        return CreditLimitIncreaseRequestResponse(
            id=row.id,
            previous_limit=row.previous_limit,
            requested_limit=row.requested_limit,
            approved_limit=row.approved_limit,
            reason=row.reason,
            status=row.status,
            requested_by=requested_by,
            reviewed_by=_user_to_schema(rev_u),
            reviewed_at=row.reviewed_at,
            created_at=row.created_at,
        )

    async def get_pending_for_current_application_detail(
        self,
        organization_id: str,
    ) -> CreditLimitIncreaseRequestResponse | None:
        row = await self._repo.get_latest_pending_for_org(organization_id)
        if row is None:
            return None
        return self.to_response(row)

    async def create(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        data: CreateCreditLimitIncreaseRequestBody,
    ) -> CreditLimitIncreaseRequestResponse:
        await self._org_repo.get_by_id_or_404(organization_id)
        pending = await self._repo.count_pending_for_org(organization_id)
        if pending > 0:
            raise ValidationError("A credit limit increase request is already pending for this organisation.")
        acct = await self._account_repo.get_by_org_id(organization_id)
        if acct is None:
            raise ValidationError(
                "No credit account exists for this organisation. A credit account is required before requesting a limit increase.",
            )
        created = await self._repo.create({
            "organization_id": organization_id,
            "previous_limit": acct.credit_limit,
            "requested_limit": data.requested_credit_limit,
            "reason": data.reason,
            "status": OrgCreditLimitIncreaseRequestStatus.PENDING,
            "requested_by_user_id": caller.id,
        })
        row = await self._repo.get_by_id_and_org_with_users(created.id, organization_id)
        if row is None:
            raise NotFoundError(resource="org_credit_limit_increase_request", id=created.id)
        await self._audit.log(
            action="org_credit_limit_increase_request.created",
            entity_type="org_credit_limit_increase_request",
            entity_id=row.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={
                "requested_limit": str(data.requested_credit_limit),
                "previous_limit": str(acct.credit_limit) if acct.credit_limit is not None else None,
            },
            ip_address=self._ip,
            user_agent=self._ua,
            reason=data.reason or f"Requested credit limit increase to {data.requested_credit_limit}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_LIMIT_INCREASE_REQUESTED,
            severity="NOTICE",
        )
        logger.info(
            "org_credit_limit_increase_request.created",
            organization_id=organization_id,
            request_id=row.id,
        )
        return self.to_response(row)

    async def list_for_org(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[CreditLimitIncreaseRequestResponse], int]:
        await self._org_repo.get_by_id_or_404(organization_id)
        rows, total = await self._repo.list_for_org(organization_id, page=page, size=size)
        return [self.to_response(r) for r in rows], total

    async def get_by_id(self, organization_id: str, request_id: str) -> CreditLimitIncreaseRequestResponse:
        await self._org_repo.get_by_id_or_404(organization_id)
        row = await self._repo.get_by_id_and_org_with_users(request_id, organization_id)
        if row is None:
            raise NotFoundError(resource="org_credit_limit_increase_request", id=request_id)
        return self.to_response(row)

    async def approve(
        self,
        organization_id: str,
        request_id: str,
        *,
        caller: AuthUser,
        data: ApproveCreditLimitIncreaseRequestBody,
    ) -> CreditLimitIncreaseRequestResponse:
        role = caller.role if isinstance(caller.role, str) else caller.role.value
        if role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
            raise ForbiddenError("Only administrators can approve credit limit increase requests.")
        await self._org_repo.get_by_id_or_404(organization_id)
        row = await self._repo.get_by_id_and_org_with_users(request_id, organization_id)
        if row is None:
            raise NotFoundError(resource="org_credit_limit_increase_request", id=request_id)
        if row.status != OrgCreditLimitIncreaseRequestStatus.PENDING:
            raise ValidationError("Only a pending request can be approved.")
        today = datetime.now(UTC).date()
        await self._settings.patch_credit_limit(
            organization_id,
            caller=caller,
            credit_limit=data.approved_credit_limit,
            reason_category=CreditLimitAdjustmentReason.CLIENT_REQUEST.value,
            effective_date=today,
            justification=row.reason,
        )
        now = datetime.now(UTC)
        await self._repo.update_by_id(
            request_id,
            {
                "status": OrgCreditLimitIncreaseRequestStatus.APPROVED,
                "approved_limit": data.approved_credit_limit,
                "reviewed_by_user_id": caller.id,
                "reviewed_at": now,
            },
            organization_id=organization_id,
        )
        updated = await self._repo.get_by_id_and_org_with_users(request_id, organization_id)
        if updated is None:
            raise NotFoundError(resource="org_credit_limit_increase_request", id=request_id)
        await self._audit.log(
            action="org_credit_limit_increase_request.approved",
            entity_type="org_credit_limit_increase_request",
            entity_id=request_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={"approved_limit": str(data.approved_credit_limit)},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Approved credit limit increase to {data.approved_credit_limit}",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_LIMIT_INCREASE_APPROVED,
            severity="NOTICE",
        )
        logger.info(
            "org_credit_limit_increase_request.approved",
            organization_id=organization_id,
            request_id=request_id,
        )
        return self.to_response(updated)

    async def reject(
        self,
        organization_id: str,
        request_id: str,
        *,
        caller: AuthUser,
    ) -> CreditLimitIncreaseRequestResponse:
        role = caller.role if isinstance(caller.role, str) else caller.role.value
        if role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
            raise ForbiddenError("Only administrators can reject credit limit increase requests.")
        await self._org_repo.get_by_id_or_404(organization_id)
        row = await self._repo.get_by_id_and_org_with_users(request_id, organization_id)
        if row is None:
            raise NotFoundError(resource="org_credit_limit_increase_request", id=request_id)
        if row.status != OrgCreditLimitIncreaseRequestStatus.PENDING:
            raise ValidationError("Only a pending request can be rejected.")
        now = datetime.now(UTC)
        await self._repo.update_by_id(
            request_id,
            {
                "status": OrgCreditLimitIncreaseRequestStatus.REJECTED,
                "reviewed_by_user_id": caller.id,
                "reviewed_at": now,
            },
            organization_id=organization_id,
        )
        updated = await self._repo.get_by_id_and_org_with_users(request_id, organization_id)
        if updated is None:
            raise NotFoundError(resource="org_credit_limit_increase_request", id=request_id)
        await self._audit.log(
            action="org_credit_limit_increase_request.rejected",
            entity_type="org_credit_limit_increase_request",
            entity_id=request_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            old_value={"status": OrgCreditLimitIncreaseRequestStatus.PENDING.value},
            new_value={"status": OrgCreditLimitIncreaseRequestStatus.REJECTED.value},
            ip_address=self._ip,
            user_agent=self._ua,
            reason="Credit limit increase request rejected",
            organization_id=organization_id,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_LIMIT_INCREASE_REJECTED,
            severity="NOTICE",
        )
        logger.info(
            "org_credit_limit_increase_request.rejected",
            organization_id=organization_id,
            request_id=request_id,
        )
        return self.to_response(updated)
