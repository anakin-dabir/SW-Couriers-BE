from pydantic import Field

from app.common.schemas import BaseSchema, BaseResponseSchema
from app.modules.client_inactivity.constants import (
    DEFAULT_INACTIVE_AFTER_DAYS,
    MAX_INACTIVE_AFTER_DAYS,
    MIN_INACTIVE_AFTER_DAYS,
)


class ClientInactivityConfigResponse(BaseResponseSchema):
    enabled: bool = Field(description="When true, B2B client users are marked INACTIVE after the threshold.")
    inactive_after_days: int = Field(
        DEFAULT_INACTIVE_AFTER_DAYS,
        ge=MIN_INACTIVE_AFTER_DAYS,
        le=MAX_INACTIVE_AFTER_DAYS,
        description="Days without login before a B2B client user is marked inactive.",
    )


class ClientInactivityConfigPatch(BaseSchema):
    enabled: bool | None = None
    inactive_after_days: int | None = Field(
        default=None,
        ge=MIN_INACTIVE_AFTER_DAYS,
        le=MAX_INACTIVE_AFTER_DAYS,
    )
    version: int | None = Field(
        None,
        ge=1,
        description="Optional optimistic lock version. If supplied and stale, update is rejected.",
    )
