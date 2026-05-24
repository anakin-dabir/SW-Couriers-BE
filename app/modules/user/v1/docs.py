from __future__ import annotations

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

SEND_INVITE = create_doc_entry(
    "Send invite to an existing user",
    {
        201: success_entry(
            "Invite sent",
            data={
                "invite_id": "00000000-0000-0000-0000-000000000000",
                "email": "user@example.com",
            },
            message="Invite sent successfully",
        ),
        401: error_401_entry(
            "Not authenticated",
            "AUTHENTICATION_ERROR",
            "Missing authorization header",
        ),
        403: error_entry(
            "Not allowed (requires matching organization scope, or super admin)",
            code="FORBIDDEN",
            message="You cannot send an invite to this user",
        ),
        404: error_entry(
            "User not found",
            code="NOT_FOUND",
            message="user with id '...' not found",
        ),
        409: error_entry(
            "User not in pending_verification status",
            code="CONFLICT",
            message="User must be in pending_verification status to send an invite",
        ),
    },
)
