from __future__ import annotations

import re

import structlog
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import SessionDep
from app.common.exceptions import ValidationError
from app.common.service import BaseService
from app.common.types import AuditContext
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.dropdown_configs.enums import DropdownConfigKey, key_display_name
from app.modules.dropdown_configs.models import DropdownValue
from app.modules.dropdown_configs.repository import DropdownValueRepository
from app.modules.dropdown_configs.v1.schemas import (
    DropdownKeyListItem,
    DropdownValuesByKeyResponse,
    DropdownValueReplaceItem,
    DropdownValueReplaceRequest,
    DropdownValueResponse,
)

logger = structlog.get_logger()

_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_CODE_MAX_LEN = 64


class DropdownConfigService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = DropdownValueRepository(session)
        self._audit = AuditService(session)
        self._ip_address = request.client.host if request and request.client else None
        self._user_agent = request.headers.get("user-agent") if request else None

    @classmethod
    def dep(cls, request: Request, session: SessionDep):
        return cls(session, request)

    @staticmethod
    def _normalize_hex(color: str | None) -> str | None:
        if color is None:
            return None
        c = color.strip()
        if not c.startswith("#"):
            raise ValidationError("color_hex must start with #")
        if len(c) not in (7, 9):
            raise ValidationError("color_hex must be #RRGGBB or #RRGGBBAA")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", c):
            raise ValidationError("color_hex contains invalid characters")
        return c.upper()

    @staticmethod
    def _label_to_base_code(label: str) -> str:
        raw = label.strip()
        if not raw:
            raise ValidationError("label is required")
        t = raw.replace("&", " AND ")
        t = re.sub(r"[^A-Za-z0-9]+", "_", t)
        t = t.strip("_").upper()
        t = re.sub(r"_+", "_", t)
        if not t:
            raise ValidationError("label could not be converted to a stable code")
        if not t[0].isalpha():
            t = "X_" + t
        t = re.sub(r"[^A-Z0-9_]", "", t)
        while "__" in t:
            t = t.replace("__", "_")
        t = t[:_CODE_MAX_LEN].rstrip("_")
        if len(t) < 2:
            raise ValidationError("label is too short to derive a valid code")
        if not _CODE_PATTERN.fullmatch(t):
            raise ValidationError("label produces an invalid code")
        return t

    @classmethod
    def _allocate_codes(cls, items: list[DropdownValueReplaceItem]) -> list[str]:
        used: set[str] = set()
        codes: list[str] = []
        for item in items:
            base = cls._label_to_base_code(item.label.strip())
            code = base
            n = 2
            while code in used:
                suffix = f"_{n}"
                room = _CODE_MAX_LEN - len(suffix)
                if room < 2:
                    raise ValidationError("unable to derive unique codes for this label list")
                prefix = base[:room].rstrip("_")
                if len(prefix) < 2:
                    prefix = base[:room]
                if len(prefix) < 2:
                    raise ValidationError("unable to derive unique codes for this label list")
                code = prefix + suffix
                if not _CODE_PATTERN.fullmatch(code):
                    raise ValidationError("unable to derive a unique valid code")
                n += 1
                if n > 10_000:
                    raise ValidationError("unable to derive unique codes")
            used.add(code)
            codes.append(code)
        return codes

    def _to_value_response(self, v: DropdownValue) -> DropdownValueResponse:
        return DropdownValueResponse(
            id=v.id,
            dropdown_key=v.dropdown_key,
            code=v.code,
            label=v.label,
            color_hex=v.color_hex,
            created_at=v.created_at,
            updated_at=v.updated_at,
        )

    async def list_keys(
        self,
        *,
        search: str | None = None,
    ) -> list[DropdownKeyListItem]:
        q = search.strip().lower() if search and search.strip() else None
        items: list[DropdownKeyListItem] = []
        for key in sorted(DropdownConfigKey, key=lambda k: k.value):
            display_name = key_display_name(key)
            if q is not None:
                if q not in key.value.lower() and q not in display_name.lower():
                    continue
            cnt = await self._repo.count_for_key(key)
            items.append(
                DropdownKeyListItem(
                    key=key,
                    display_name=display_name,
                    values_count=cnt,
                )
            )
        return items

    async def list_values(self, key: DropdownConfigKey) -> list[DropdownValueResponse]:
        rows = await self._repo.list_for_key(key)
        return [self._to_value_response(v) for v in rows]

    async def list_all_values_grouped(self) -> list[DropdownValuesByKeyResponse]:
        rows = await self._repo.list_all()
        rows_by_key: dict[DropdownConfigKey, list[DropdownValueResponse]] = {
            key: [] for key in sorted(DropdownConfigKey, key=lambda k: k.value)
        }
        for row in rows:
            rows_by_key[row.dropdown_key].append(self._to_value_response(row))
        return [
            DropdownValuesByKeyResponse(
                key=key,
                display_name=key_display_name(key),
                values=rows_by_key[key],
            )
            for key in rows_by_key
        ]

    async def replace_values_for_key(
        self, key: DropdownConfigKey, body: DropdownValueReplaceRequest, ctx: AuditContext
    ) -> list[DropdownValueResponse]:
        codes = self._allocate_codes(body.values)

        await self._repo.delete_all_for_key(key)

        for code, item in zip(codes, body.values, strict=True):
            color = self._normalize_hex(item.color_hex)
            await self._repo.create(
                {
                    "dropdown_key": key,
                    "code": code,
                    "label": item.label.strip(),
                    "color_hex": color,
                }
            )

        await self._audit.log(
            action="dropdown_config.values_replaced",
            entity_type="dropdown_value",
            entity_id=None,
            entity_ref=key.value,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={
                "dropdown_key": key.value,
                "count": len(body.values),
                "codes": codes,
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            category=AuditCategory.SYSTEM,
            event_type=AuditEventType.SYSTEM_CONFIG_CHANGED,
        )
        logger.info("dropdown_config.values_replaced", dropdown_key=key.value, count=len(body.values))
        return await self.list_values(key)
