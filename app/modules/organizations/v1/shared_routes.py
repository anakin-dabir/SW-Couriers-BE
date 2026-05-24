"""Public (unauthenticated) endpoints for accessing shared documents.

These endpoints allow external recipients to:
1. Check share status and OTP requirement
2. Request an OTP (for OTP-protected shares)
3. Verify the OTP and obtain a short-lived share access token
4. Access or download the document (presenting the token for protected shares)
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.common.exceptions import AuthenticationError, ForbiddenError
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.core.rate_limit import SHARED_DOC_OTP_RATE_LIMIT, SHARED_DOC_PASSWORD_RATE_LIMIT, limiter
from app.modules.organizations.doc_access_service import ShareOtpServiceDep
from app.modules.organizations.service import OrgDocumentShareService
from app.modules.organizations.v1.schemas import (
    ShareAccessTokenResponse,
    ShareOtpSendRequest,
    ShareOtpSendResponse,
    ShareOtpVerifyRequest,
    SharedDocumentAccessRequest,
    SharedDocumentAccessResponse,
    SharedDocumentInfoResponse,
)

router = APIRouter()

ShareServiceDep = Annotated[OrgDocumentShareService, Depends(OrgDocumentShareService.dep)]


@router.get(
    "/{share_token}",
    response_model=SuccessResponse[SharedDocumentInfoResponse],
)
async def get_shared_document_info(
    share_token: str,
    share_service: ShareServiceDep,
) -> dict:
    """Check shared document status and whether OTP verification is required.

    This is the first step in the shared document access flow.  The frontend
    calls this endpoint to determine whether to display an OTP prompt or
    redirect directly to the document preview.

    Returns share metadata even for expired / revoked links so the UI
    can show an appropriate message.
    """
    result = await share_service.get_share_info(share_token)
    return ok(result)


@router.post(
    "/{share_token}/otp/send",
    response_model=SuccessResponse[ShareOtpSendResponse],
    status_code=status.HTTP_200_OK,
)
@limiter.limit(SHARED_DOC_OTP_RATE_LIMIT)
async def send_share_otp(
    request: Request,
    response: Response,
    share_token: str,
    body: ShareOtpSendRequest,
    share_service: ShareServiceDep,
    otp_service: ShareOtpServiceDep,
) -> dict:
    """Send a 6-digit OTP to the recipient's email address.

    Call this when `otp_required` is true in the share info response.
    The email must match an address the document was shared with.
    Rate-limited to prevent abuse.

    - 404 — unknown share token
    - 403 — share is revoked or expired
    - 400 — too many OTP requests (rate limit exceeded)

    Non-invited emails receive the same 200 response without sending an OTP.
    """
    share = await share_service.get_share_for_public_otp(share_token)
    share_service.assert_share_active_for_otp(share)

    if not share_service.recipient_is_allowed(share, str(body.email)):
        return ok(ShareOtpSendResponse())

    await otp_service.send_otp(
        recipient_email=str(body.email),
        share_token=share_token,
        document_title=share.document_title,
    )
    return ok(ShareOtpSendResponse())


@router.post(
    "/{share_token}/otp/verify",
    response_model=SuccessResponse[ShareAccessTokenResponse],
)
@limiter.limit(SHARED_DOC_OTP_RATE_LIMIT)
async def verify_share_otp(
    request: Request,
    response: Response,
    share_token: str,
    body: ShareOtpVerifyRequest,
    share_service: ShareServiceDep,
    otp_service: ShareOtpServiceDep,
) -> dict:
    """Verify the OTP and obtain a 1-hour share access token.

    The email must match an address the document was shared with (same as OTP send).
    Pass the returned `share_access_token` as the `X-Share-Access-Token` header
    (or in the request body) when calling the /access or /download endpoints.
    Rate-limited; repeated invalid attempts trigger a temporary lockout (Redis).

    - 401 — invalid or expired OTP, or email not on the share recipient list
    - 403 — share is revoked or expired
    - 429 — too many verify attempts (SlowAPI or lockout after repeated failures)
    """
    share = await share_service.get_share_for_public_otp(share_token)
    share_service.assert_share_active_for_otp(share)

    if not share_service.recipient_is_allowed(share, str(body.email)):
        raise AuthenticationError("Invalid or expired OTP. Please request a new one.")

    result = await otp_service.verify_otp(
        recipient_email=str(body.email),
        share_token=share_token,
        otp_code=body.otp,
    )
    return ok(ShareAccessTokenResponse(**result))


@router.post(
    "/{share_token}/access",
    response_model=SuccessResponse[SharedDocumentAccessResponse],
)
@limiter.limit(SHARED_DOC_PASSWORD_RATE_LIMIT)
async def access_shared_document(
    request: Request,
    response: Response,
    share_token: str,
    body: SharedDocumentAccessRequest,
    share_service: ShareServiceDep,
) -> dict:
    """Access the shared document — returns a presigned download URL.

    For OTP-protected shares, the `share_access_token` obtained after OTP
    verification must be included in the request body.  Each call increments
    the access counter and creates an audit log entry.

    - 404 — unknown share token or document no longer available
    - 401 — OTP token required / invalid
    - 403 — share is revoked or expired
    """
    result = await share_service.access_shared_document(
        share_token=share_token,
        share_access_token=body.share_access_token,
        request=request,
    )
    return ok(result)


@router.post(
    "/{share_token}/download",
    response_model=SuccessResponse[SharedDocumentAccessResponse],
)
@limiter.limit(SHARED_DOC_PASSWORD_RATE_LIMIT)
async def download_shared_document(
    request: Request,
    response: Response,
    share_token: str,
    body: SharedDocumentAccessRequest,
    share_service: ShareServiceDep,
) -> dict:
    """Download the shared document — returns a presigned URL and logs DOWNLOADED.

    Call this when the user explicitly clicks "Download" (as opposed to
    inline preview which uses /access).  Logs a DOWNLOADED activity entry
    instead of VIEWED so the two actions appear distinctly in the activity log.

    - 404 — unknown share token or document no longer available
    - 401 — OTP token required / invalid
    - 403 — share is revoked or expired
    """
    result = await share_service.download_shared_document(
        share_token=share_token,
        share_access_token=body.share_access_token,
        request=request,
    )
    return ok(result)
