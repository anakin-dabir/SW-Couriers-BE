from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request, status
from pydantic import Json

from app.common.deps import ORG_FILE, Allowed, AuthUser, ValidatedFile, validated_upload
from app.common.enums import UserRole
from app.common.exceptions import ValidationError
from app.common.response import ok
from app.common.schemas import MessageResponse, PaginatedResponse, SuccessResponse
from app.core.swagger.utils import schema_description
from app.modules.org_credit.v1.schemas import CreditCheckResult
from app.modules.org_credit_applications.service import OrgCreditApplicationService, OrgCreditLimitIncreaseRequestService
from app.modules.org_credit_applications.v1.docs import (
    ADD_TRADE_REFERENCE,
    APPROVE_CREDIT_APPLICATION,
    APPROVE_CREDIT_LIMIT_INCREASE_REQUEST,
    ASSIGN_CREDIT_REVIEWER,
    CANCEL_CREDIT_APPLICATION,
    CREATE_CREDIT_APPLICATION,
    CREATE_CREDIT_LIMIT_INCREASE_REQUEST,
    DELETE_CREDIT_APPLICATION,
    DELETE_CREDIT_APPLICATION_DRAFT,
    EDIT_BANK_REFERENCE,
    EDIT_COMPANY_FINANCIAL_INFO,
    EDIT_DECLARATIONS,
    EDIT_REQUESTED_CREDIT_TERMS,
    EDIT_TRADE_REFERENCE,
    GET_CREDIT_APPLICATION_DETAIL,
    GET_CREDIT_LIMIT_INCREASE_REQUEST,
    GET_CURRENT_CREDIT_APPLICATION_DETAIL,
    GET_CREDIT_APPLICATION_DRAFT,
    LIST_CREDIT_APPLICATION_DRAFTS,
    LIST_CREDIT_APPLICATIONS,
    LIST_CREDIT_LIMIT_INCREASE_REQUESTS,
    PATCH_CREDIT_APPLICATION_DRAFT,
    PUBLISH_CREDIT_APPLICATION_DRAFT,
    READY_FOR_DECISION,
    REFRESH_CREDIT_CHECK,
    REJECT_CREDIT_APPLICATION,
    REJECT_CREDIT_LIMIT_INCREASE_REQUEST,
    RUN_CREDIT_CHECK,
    SAVE_CREDIT_APPLICATION_DRAFT,
    VERIFY_TRADE_REFERENCE,
    WITHDRAW_CREDIT_APPLICATION,
)
from app.modules.org_credit_applications.v1.schemas import (
    AddTradeReferenceRequest,
    ApproveCreditApplicationBody,
    ApproveCreditLimitIncreaseRequestBody,
    AssignReviewerBody,
    CancelApplicationBody,
    CreateCreditApplicationRequest,
    CreateCreditLimitIncreaseRequestBody,
    CreditApplicationCreatedResponse,
    CreditApplicationCreatedWithUploadsResponse,
    CreditApplicationCurrentDetailView,
    CreditApplicationDetailView,
    CreditApplicationDraftDetail,
    CreditApplicationDraftListItem,
    CreditApplicationDraftListParams,
    CreditApplicationDraftSaveResponse,
    CreditApplicationListItem,
    CreditApplicationListParams,
    CreditLimitIncreaseRequestListParams,
    CreditLimitIncreaseRequestResponse,
    MessageWithUploadsResponse,
    PatchBankReferenceBody,
    EditCompanyFinancialInfoRequest,
    EditDeclarationsRequest,
    EditRequestedCreditTermsRequest,
    EditTradeReferenceRequest,
    RejectCreditApplicationBody,
    SaveDraftRequest,
    UpdateDraftRequest,
    VerifyTradeReferenceRequest,
)
from app.modules.organizations.v1.routes import OrgProfileReadUserDep, OrgProfileWriteUserDep

router = APIRouter()
credit_limit_requests_router = APIRouter()

CreditAppServiceDep = Annotated[OrgCreditApplicationService, Depends(OrgCreditApplicationService.dep)]


@router.get(
    "/{org_id}/credit/applications",
    response_model=SuccessResponse[PaginatedResponse[CreditApplicationListItem]],
    **LIST_CREDIT_APPLICATIONS,
)
async def list_credit_applications(
    request: Request,
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: CreditAppServiceDep,
    params: Annotated[CreditApplicationListParams, Query()],
) -> dict:
    items, total = await svc.list_for_org(
        org_id,
        page=params.page,
        size=params.size,
        status=params.status,
        search=params.search,
    )
    response_items = svc.applications_to_list_items(items)
    return ok(
        PaginatedResponse.create(
            items=response_items,
            total=total,
            page=params.page,
            size=params.size,
            request=request,
        )
    )


@router.post(
    "/{org_id}/credit/applications",
    response_model=CreditApplicationCreatedWithUploadsResponse,
    status_code=status.HTTP_201_CREATED,
    **CREATE_CREDIT_APPLICATION,
)
async def create_credit_application(
    org_id: str,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
    bank_reference_letter_file: Annotated[
        ValidatedFile | None,
        validated_upload(ORG_FILE, field_name="bank_reference_letter_file", max_files=1, optional=True),
    ],
    application_data: Annotated[
        Json[CreateCreditApplicationRequest],
        Form(
            media_type="application/json",
            description=schema_description(CreateCreditApplicationRequest),
        ),
    ],
) -> dict:
    data = application_data.model_dump(exclude={"trade_references"}, exclude_unset=True)
    app = await svc.create(
        org_id,
        data=data,
        trade_references=application_data.trade_references,
        caller=caller,
    )

    failed_documents = await svc.handle_bank_reference_letter_upload(
        org_id,
        app.id,
        bank_reference_letter_file,
        caller=caller,
    )
    return ok(
        CreditApplicationCreatedResponse(id=app.id, application_number=app.application_number),
        message="Credit application submitted.",
        failed_documents=failed_documents,
    )


@router.post(
    "/{org_id}/credit/applications/drafts",
    response_model=CreditApplicationDraftSaveResponse,
    status_code=status.HTTP_201_CREATED,
    **SAVE_CREDIT_APPLICATION_DRAFT,
)
async def save_credit_application_draft(
    org_id: str,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
    bank_reference_letter_file: Annotated[
        ValidatedFile | None,
        validated_upload(ORG_FILE, field_name="bank_reference_letter_file", max_files=1, optional=True),
    ],
    draft_data: Annotated[
        Json[SaveDraftRequest],
        Form(
            media_type="application/json",
            description=schema_description(SaveDraftRequest),
        ),
    ],
) -> dict:
    data = draft_data.model_dump(exclude={"trade_references"}, exclude_unset=True)
    draft, app_id = await svc.save_draft(
        org_id,
        caller=caller,
        data=data,
        trade_references=draft_data.trade_references,
    )

    failed_documents = await svc.handle_bank_reference_letter_upload(
        org_id,
        app_id,
        bank_reference_letter_file,
        caller=caller,
    )
    return ok(
        draft,
        message="Credit application draft saved.",
        failed_documents=failed_documents,
    )


@router.get(
    "/{org_id}/credit/applications/drafts",
    response_model=SuccessResponse[PaginatedResponse[CreditApplicationDraftListItem]],
    **LIST_CREDIT_APPLICATION_DRAFTS,
)
async def list_credit_application_drafts(
    request: Request,
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: CreditAppServiceDep,
    params: Annotated[CreditApplicationDraftListParams, Query()],
) -> dict:
    items, total = await svc.list_drafts(org_id, page=params.page, size=params.size)
    return ok(
        PaginatedResponse.create(
            items=items,
            total=total,
            page=params.page,
            size=params.size,
            request=request,
        )
    )


@router.get(
    "/{org_id}/credit/applications/drafts/{draft_id}",
    response_model=SuccessResponse[CreditApplicationDraftDetail],
    **GET_CREDIT_APPLICATION_DRAFT,
)
async def get_credit_application_draft(
    org_id: str,
    draft_id: str,
    _caller: OrgProfileReadUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    return ok(await svc.get_draft(org_id, draft_id))


@router.patch(
    "/{org_id}/credit/applications/drafts/{draft_id}",
    response_model=CreditApplicationDraftSaveResponse,
    **PATCH_CREDIT_APPLICATION_DRAFT,
)
async def patch_credit_application_draft(
    org_id: str,
    draft_id: str,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
    bank_reference_letter_file: Annotated[
        ValidatedFile | None,
        validated_upload(ORG_FILE, field_name="bank_reference_letter_file", max_files=1, optional=True),
    ],
    draft_data: Annotated[
        Json[UpdateDraftRequest],
        Form(
            media_type="application/json",
            description=schema_description(UpdateDraftRequest),
        ),
    ],
    deleted_bank_reference_letter_id: Annotated[
        str | None,
        Form(description="Single bank reference letter attachment id to delete."),
    ] = None,
) -> dict:
    data = draft_data.model_dump(exclude={"trade_references"}, exclude_unset=True)
    draft, app = await svc.update_draft(
        org_id, draft_id,
        caller=caller,
        data=data,
        trade_references=draft_data.trade_references,
    )

    if deleted_bank_reference_letter_id:
        await svc.delete_bank_reference_letter(
            org_id,
            app.id,
            deleted_bank_reference_letter_id,
            caller=caller,
        )

    failed_documents = await svc.handle_bank_reference_letter_upload(
        org_id,
        app.id,
        bank_reference_letter_file,
        caller=caller,
    )
    return ok(
        draft,
        message="Credit application draft updated.",
        failed_documents=failed_documents,
    )


@router.post(
    "/{org_id}/credit/applications/drafts/{draft_id}/publish",
    response_model=CreditApplicationDraftSaveResponse,
    **PUBLISH_CREDIT_APPLICATION_DRAFT,
)
async def publish_credit_application_draft(
    org_id: str,
    draft_id: str,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
    bank_reference_letter_file: Annotated[
        ValidatedFile | None,
        validated_upload(ORG_FILE, field_name="bank_reference_letter_file", max_files=1, optional=True),
    ],
    application_data: Annotated[
        Json[CreateCreditApplicationRequest],
        Form(
            media_type="application/json",
            description=schema_description(CreateCreditApplicationRequest),
        ),
    ],
    deleted_bank_reference_letter_id: Annotated[
        str | None,
        Form(description="Single bank reference letter attachment id to delete."),
    ] = None,
) -> dict:
    _draft, app = await svc.load_draft_row(org_id, draft_id)
    app_id = app.id

    if deleted_bank_reference_letter_id:
        await svc.delete_bank_reference_letter(
            org_id,
            app_id,
            deleted_bank_reference_letter_id,
            caller=caller,
        )

    failed_documents = await svc.handle_bank_reference_letter_upload(
        org_id,
        app_id,
        bank_reference_letter_file,
        caller=caller,
    )

    data = application_data.model_dump(exclude={"trade_references"}, exclude_unset=True)
    draft, _app = await svc.publish_draft(
        org_id, draft_id,
        caller=caller,
        data=data,
        trade_references=application_data.trade_references,
    )
    return ok(
        draft,
        message="Credit application submitted.",
        failed_documents=failed_documents,
    )


@router.delete(
    "/{org_id}/credit/applications/drafts/{draft_id}",
    response_model=MessageResponse,
    **DELETE_CREDIT_APPLICATION_DRAFT,
)
async def delete_credit_application_draft(
    org_id: str,
    draft_id: str,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    await svc.delete_draft(org_id, draft_id, caller=caller)
    return ok(message="Credit application draft deleted.")


@router.get(
    "/{org_id}/credit/applications/current-application",
    response_model=SuccessResponse[CreditApplicationCurrentDetailView],
    **GET_CURRENT_CREDIT_APPLICATION_DETAIL,
)
async def get_credit_application_current(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    return ok(await svc.get_detail_latest(org_id))


@router.get(
    "/{org_id}/credit/applications/{application_id}",
    response_model=SuccessResponse[CreditApplicationDetailView],
    **GET_CREDIT_APPLICATION_DETAIL,
)
async def get_credit_application(
    org_id: str,
    application_id: str,
    _caller: OrgProfileReadUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    return ok(await svc.get_detail(org_id, application_id))


@router.patch(
    "/{org_id}/credit/applications/{application_id}/company-financial-info",
    response_model=MessageResponse,
    **EDIT_COMPANY_FINANCIAL_INFO,
)
async def edit_company_financial_info(
    org_id: str,
    application_id: str,
    data: EditCompanyFinancialInfoRequest,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    updates = data.model_dump(exclude_unset=True)
    await svc.edit_section(
        org_id, application_id,
        caller=caller, updates=updates,
        section_name="company_financial_info",
    )
    return ok(message="Company financial information updated.")


@router.patch(
    "/{org_id}/credit/applications/{application_id}/bank-reference",
    response_model=MessageWithUploadsResponse,
    **EDIT_BANK_REFERENCE,
)
async def edit_bank_reference(
    org_id: str,
    application_id: str,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
    bank_reference: Annotated[
        Json[PatchBankReferenceBody],
        Form(
            media_type="application/json",
            description=schema_description(PatchBankReferenceBody),
        ),
    ],
    bank_reference_letter_file: Annotated[
        ValidatedFile | None,
        validated_upload(ORG_FILE, field_name="bank_reference_letter_file", max_files=1, optional=True),
    ] = None,
    deleted_bank_reference_letter_id: Annotated[
        str | None,
        Form(description="Single bank reference letter attachment id to delete."),
    ] = None,
) -> dict:
    updates = bank_reference.model_dump(exclude_unset=True)
    updates = {k: v for k, v in updates.items() if v is not None}
    if not updates and not bank_reference_letter_file and not deleted_bank_reference_letter_id:
        raise ValidationError(
            "At least one bank reference field in bank_reference JSON, a bank_reference_letter_file, or deleted_bank_reference_letter_id must be provided.",
        )

    if deleted_bank_reference_letter_id:
        await svc.delete_bank_reference_letter(
            org_id,
            application_id,
            deleted_bank_reference_letter_id,
            caller=caller,
        )

    failed_documents = await svc.handle_bank_reference_letter_upload(
        org_id,
        application_id,
        bank_reference_letter_file,
        caller=caller,
    )

    await svc.edit_section(
        org_id, application_id,
        caller=caller, updates=updates,
        section_name="bank_reference",
    )
    return ok(
        message="Bank reference updated.",
        failed_documents=failed_documents,
    )


@router.patch(
    "/{org_id}/credit/applications/{application_id}/requested-credit-terms",
    response_model=MessageResponse,
    **EDIT_REQUESTED_CREDIT_TERMS,
)
async def edit_requested_credit_terms(
    org_id: str,
    application_id: str,
    data: EditRequestedCreditTermsRequest,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    updates = data.model_dump(exclude_unset=True)
    await svc.edit_section(
        org_id, application_id,
        caller=caller, updates=updates,
        section_name="requested_credit_terms",
    )
    return ok(message="Requested credit terms updated.")


@router.patch(
    "/{org_id}/credit/applications/{application_id}/declarations",
    response_model=MessageResponse,
    **EDIT_DECLARATIONS,
)
async def edit_declarations(
    org_id: str,
    application_id: str,
    data: EditDeclarationsRequest,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    updates = data.model_dump(exclude_unset=True)
    await svc.edit_section(
        org_id, application_id,
        caller=caller, updates=updates,
        section_name="declarations",
    )
    return ok(message="Declarations updated.")


@router.post(
    "/{org_id}/credit/applications/{application_id}/trade-references",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    **ADD_TRADE_REFERENCE,
)
async def add_trade_reference(
    org_id: str,
    application_id: str,
    data: AddTradeReferenceRequest,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    from app.modules.org_credit_applications.v1.schemas import TradeReferenceInput

    ref_input = TradeReferenceInput(**data.model_dump(exclude_unset=True))
    await svc.add_trade_reference(
        org_id, application_id,
        caller=caller, data=ref_input,
    )
    return ok(message="Trade reference added.")


@router.patch(
    "/{org_id}/credit/applications/{application_id}/trade-references/{ref_id}",
    response_model=MessageResponse,
    **EDIT_TRADE_REFERENCE,
)
async def edit_trade_reference(
    org_id: str,
    application_id: str,
    ref_id: str,
    data: EditTradeReferenceRequest,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    updates = data.model_dump(exclude_unset=True)
    await svc.update_trade_reference(
        org_id, application_id, ref_id,
        caller=caller, updates=updates,
    )
    return ok(message="Trade reference updated.")


@router.patch(
    "/{org_id}/credit/applications/{application_id}/trade-references/{ref_id}/verify",
    response_model=MessageResponse,
    **VERIFY_TRADE_REFERENCE,
)
async def verify_trade_reference(
    org_id: str,
    application_id: str,
    ref_id: str,
    data: VerifyTradeReferenceRequest,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    await svc.verify_trade_reference(
        org_id, application_id, ref_id,
        caller=caller,
        status=data.verification_status,
    )
    return ok(message="Trade reference verification updated.")


@router.post(
    "/{org_id}/credit/applications/{application_id}/assign-reviewer",
    response_model=MessageResponse,
    **ASSIGN_CREDIT_REVIEWER,
)
async def assign_reviewer(
    org_id: str,
    application_id: str,
    data: AssignReviewerBody,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    await svc.assign_reviewer(
        org_id, application_id,
        caller=caller,
        reviewer_user_id=data.reviewer_user_id,
    )
    return ok(message="Reviewer assigned.")


@router.post(
    "/{org_id}/credit/applications/{application_id}/credit-check/run",
    response_model=SuccessResponse[CreditCheckResult],
    **RUN_CREDIT_CHECK,
)
async def run_credit_check(
    org_id: str,
    application_id: str,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    result = await svc.run_credit_check(
        org_id, application_id,
        caller=caller,
    )
    return ok(result, message=result.message or "Credit check processed.")


@router.post(
    "/{org_id}/credit/applications/{application_id}/credit-check/refresh",
    response_model=SuccessResponse[CreditCheckResult],
    **REFRESH_CREDIT_CHECK,
)
async def refresh_credit_check(
    org_id: str,
    application_id: str,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    result = await svc.refresh_credit_check(
        org_id, application_id,
        caller=caller,
    )
    return ok(result, message=result.message or "Credit check refreshed.")


@router.post(
    "/{org_id}/credit/applications/{application_id}/ready-for-decision",
    response_model=MessageResponse,
    **READY_FOR_DECISION,
)
async def ready_for_decision(
    org_id: str,
    application_id: str,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    await svc.mark_ready_for_decision(
        org_id, application_id,
        caller=caller,
    )
    return ok(message="Application ready for decision.")


@router.post(
    "/{org_id}/credit/applications/{application_id}/approve",
    response_model=MessageResponse,
    **APPROVE_CREDIT_APPLICATION,
)
async def approve_credit_application(
    org_id: str,
    application_id: str,
    data: ApproveCreditApplicationBody,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    await svc.approve(
        org_id, application_id,
        caller=caller,
        approved_credit_limit=data.approved_credit_limit,
        approved_payment_terms_days=data.approved_payment_terms_days,
        review_frequency=data.review_frequency,
        approval_notes=data.approval_notes,
    )
    return ok(message="Credit application approved.")


@router.post(
    "/{org_id}/credit/applications/{application_id}/reject",
    response_model=MessageResponse,
    **REJECT_CREDIT_APPLICATION,
)
async def reject_credit_application(
    org_id: str,
    application_id: str,
    data: RejectCreditApplicationBody,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    await svc.reject(
        org_id, application_id,
        caller=caller,
        rejection_category=data.rejection_category,
        detailed_reason=data.detailed_reason,
    )
    return ok(message="Credit application rejected.")


@router.post(
    "/{org_id}/credit/applications/{application_id}/cancel",
    response_model=MessageResponse,
    **CANCEL_CREDIT_APPLICATION,
)
async def cancel_credit_application(
    org_id: str,
    application_id: str,
    data: CancelApplicationBody,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    await svc.cancel(
        org_id, application_id,
        caller=caller,
        reason=data.reason,
    )
    return ok(message="Credit application cancelled.")


@router.post(
    "/{org_id}/credit/applications/{application_id}/withdraw",
    response_model=MessageResponse,
    **WITHDRAW_CREDIT_APPLICATION,
)
async def withdraw_credit_application(
    org_id: str,
    application_id: str,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    await svc.withdraw(
        org_id, application_id,
        caller=caller,
    )
    return ok(message="Credit application withdrawn.")


@router.delete(
    "/{org_id}/credit/applications/{application_id}",
    response_model=MessageResponse,
    **DELETE_CREDIT_APPLICATION,
)
async def delete_credit_application(
    org_id: str,
    application_id: str,
    caller: OrgProfileWriteUserDep,
    svc: CreditAppServiceDep,
) -> dict:
    await svc.delete(org_id, application_id, caller=caller)
    return ok(message="Credit application deleted.")


LimitIncreaseServiceDep = Annotated[OrgCreditLimitIncreaseRequestService, Depends(OrgCreditLimitIncreaseRequestService.dep)]

CreditAdminOrgWriteDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN),
]


@credit_limit_requests_router.post(
    "/{org_id}/credit/limit-increase-requests",
    response_model=SuccessResponse[CreditLimitIncreaseRequestResponse],
    status_code=status.HTTP_201_CREATED,
    **CREATE_CREDIT_LIMIT_INCREASE_REQUEST,
)
async def create_credit_limit_increase_request(
    org_id: str,
    caller: OrgProfileWriteUserDep,
    svc: LimitIncreaseServiceDep,
    body: CreateCreditLimitIncreaseRequestBody,
) -> dict:
    data = await svc.create(org_id, caller=caller, data=body)
    return ok(data, message="Credit limit increase request submitted.")


@credit_limit_requests_router.get(
    "/{org_id}/credit/limit-increase-requests",
    response_model=SuccessResponse[PaginatedResponse[CreditLimitIncreaseRequestResponse]],
    **LIST_CREDIT_LIMIT_INCREASE_REQUESTS,
)
async def list_credit_limit_increase_requests(
    request: Request,
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: LimitIncreaseServiceDep,
    params: Annotated[CreditLimitIncreaseRequestListParams, Query()],
) -> dict:
    items, total = await svc.list_for_org(org_id, page=params.page, size=params.size)
    return ok(
        PaginatedResponse.create(
            items=items,
            total=total,
            page=params.page,
            size=params.size,
            request=request,
        )
    )


@credit_limit_requests_router.get(
    "/{org_id}/credit/limit-increase-requests/{request_id}",
    response_model=SuccessResponse[CreditLimitIncreaseRequestResponse],
    **GET_CREDIT_LIMIT_INCREASE_REQUEST,
)
async def get_credit_limit_increase_request(
    org_id: str,
    request_id: str,
    _caller: OrgProfileReadUserDep,
    svc: LimitIncreaseServiceDep,
) -> dict:
    return ok(await svc.get_by_id(org_id, request_id))


@credit_limit_requests_router.post(
    "/{org_id}/credit/limit-increase-requests/{request_id}/approve",
    response_model=SuccessResponse[CreditLimitIncreaseRequestResponse],
    **APPROVE_CREDIT_LIMIT_INCREASE_REQUEST,
)
async def approve_credit_limit_increase_request(
    org_id: str,
    request_id: str,
    caller: CreditAdminOrgWriteDep,
    svc: LimitIncreaseServiceDep,
    body: ApproveCreditLimitIncreaseRequestBody,
) -> dict:
    data = await svc.approve(org_id, request_id, caller=caller, data=body)
    return ok(data, message="Credit limit increase request approved.")


@credit_limit_requests_router.post(
    "/{org_id}/credit/limit-increase-requests/{request_id}/reject",
    response_model=SuccessResponse[CreditLimitIncreaseRequestResponse],
    **REJECT_CREDIT_LIMIT_INCREASE_REQUEST,
)
async def reject_credit_limit_increase_request(
    org_id: str,
    request_id: str,
    caller: CreditAdminOrgWriteDep,
    svc: LimitIncreaseServiceDep,
) -> dict:
    data = await svc.reject(org_id, request_id, caller=caller)
    return ok(data, message="Credit limit increase request rejected.")
