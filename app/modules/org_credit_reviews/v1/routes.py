from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.common.deps import Allowed, AuthUser
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.response import ok
from app.common.schemas import PaginatedResponse, SuccessResponse
from app.modules.org_credit_reviews.service import OrgCreditReviewService
from app.modules.org_credit_reviews.v1.docs import (
    GET_ORG_CREDIT_REVIEW_DETAIL,
    GET_ORG_CREDIT_REVIEWS_AND_STATUS,
    GET_ORG_CREDIT_REVIEWS_HISTORY,
    PATCH_ORG_CREDIT_REVIEW_CONFIGURATION,
    POST_ORG_CREDIT_REVIEW,
)
from app.modules.org_credit_reviews.v1.schemas import (
    CreditReviewDetailResponse,
    CreditReviewHistoryItem,
    OrgCreditReviewsAndStatusResponse,
    ReviewConfigurationRequest,
    ReviewListParams,
    SubmitReviewRequest,
)
from app.modules.organizations.v1.routes import OrgProfileReadUserDep

router = APIRouter()

OrgCreditReviewServiceDep = Annotated[OrgCreditReviewService, Depends(OrgCreditReviewService.dep)]

CreditAdminWriteDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.ORGANIZATIONS, level=PermissionLevel.WRITE),
]


@router.get(
    "/{org_id}/credit/reviews/summary",
    response_model=SuccessResponse[OrgCreditReviewsAndStatusResponse],
    **GET_ORG_CREDIT_REVIEWS_AND_STATUS,
)
async def get_org_credit_reviews_and_status(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditReviewServiceDep,
) -> dict:
    payload = await svc.get_reviews_and_status_payload(org_id)
    return ok(payload)


@router.get(
    "/{org_id}/credit/reviews-history",
    response_model=SuccessResponse[PaginatedResponse[CreditReviewHistoryItem]],
    **GET_ORG_CREDIT_REVIEWS_HISTORY,
)
async def list_org_credit_reviews_history(
    request: Request,
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditReviewServiceDep,
    params: Annotated[ReviewListParams, Query()],
) -> dict:
    items, total = await svc.list_review_history(org_id, page=params.page, size=params.size)
    response_items = [CreditReviewHistoryItem.model_validate(r) for r in items]
    return ok(PaginatedResponse.create(items=response_items, total=total, page=params.page, size=params.size, request=request))


@router.patch(
    "/{org_id}/credit/reviews/configuration",
    response_model=SuccessResponse,
    **PATCH_ORG_CREDIT_REVIEW_CONFIGURATION,
)
async def patch_org_credit_review_configuration(
    org_id: str,
    data: ReviewConfigurationRequest,
    caller: CreditAdminWriteDep,
    svc: OrgCreditReviewServiceDep,
) -> dict:
    await svc.configure_review(
        org_id,
        caller=caller,
        review_frequency=data.review_frequency,
        next_review_date=data.next_review_date,
        reminder_period=data.reminder_period,
        reviewer_user_id=data.reviewer_user_id,
    )
    return ok(message="Review configuration updated.")


@router.post(
    "/{org_id}/credit/reviews",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    **POST_ORG_CREDIT_REVIEW,
)
async def post_org_credit_review(
    org_id: str,
    data: SubmitReviewRequest,
    caller: CreditAdminWriteDep,
    svc: OrgCreditReviewServiceDep,
) -> dict:
    await svc.submit_review(
        org_id,
        caller=caller,
        risk_level=data.risk_level,
        outcome=data.outcome,
        review_notes=data.review_notes,
        next_review_frequency=data.next_review_frequency,
        recommended_new_limit=data.recommended_new_limit,
        recommended_payment_terms_days=data.recommended_payment_terms_days,
        credit_report_id=data.credit_report_id,
    )
    return ok(message="Credit review submitted.")


@router.get(
    "/{org_id}/credit/reviews/{review_id}",
    response_model=SuccessResponse[CreditReviewDetailResponse],
    **GET_ORG_CREDIT_REVIEW_DETAIL,
)
async def get_org_credit_review_detail(
    org_id: str,
    review_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditReviewServiceDep,
) -> dict:
    data = await svc.get_review_detail(org_id, review_id)
    return ok(CreditReviewDetailResponse.model_validate(data))
