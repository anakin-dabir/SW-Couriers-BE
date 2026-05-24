"""User v1 API schemas: request and response."""

from app.common.schemas import BaseSchema


class SendInviteResponse(BaseSchema):
    """Response after sending an invite to an existing user."""

    invite_id: str
    email: str
