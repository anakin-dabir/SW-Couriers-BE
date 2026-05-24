from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.common.schemas import BaseSchema
from app.modules.dropdown_configs.enums import DropdownConfigKey


class DropdownKeyListItem(BaseSchema):
    key: DropdownConfigKey
    display_name: str
    values_count: int


class DropdownValueResponse(BaseSchema):
    id: str
    created_at: datetime
    updated_at: datetime
    dropdown_key: DropdownConfigKey
    code: str
    label: str
    color_hex: str | None


class DropdownValuesByKeyResponse(BaseSchema):
    key: DropdownConfigKey
    display_name: str
    values: list[DropdownValueResponse]


class DropdownValueReplaceItem(BaseSchema):
    label: str = Field(min_length=1, max_length=200)
    color_hex: str | None = None


class DropdownValueReplaceRequest(BaseSchema):
    values: list[DropdownValueReplaceItem] = Field(
        default_factory=list,
        description="Each entry is label + optional color_hex; stable codes are derived server-side from labels.",
    )
