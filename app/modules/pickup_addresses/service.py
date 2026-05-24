from __future__ import annotations

from typing import Any

import structlog
from fastapi.requests import Request
from geoalchemy2.elements import WKTElement
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import AuthUser
from app.common.exceptions import NotFoundError, ValidationError
from app.common.service import BaseService
from app.integrations.google_maps import forward_geocode
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.organizations.models import Organization
from app.modules.organizations.repository import OrganizationRepository
from app.modules.pickup_addresses.repository import PickupAddressRepository
from app.modules.pickup_addresses.types import (
    PickupAddressOwner,
    owner_scope_for_repo,
    require_organization_id,
    resolve_pickup_address_owner_from_auth,
)
from app.modules.pickup_addresses.v1.schemas import (
    CreatePickupAddressesRequest,
    GeocodeAddressRequest,
    GeocodeResultResponse,
    PickupAddressCreate,
    PickupAddressResponse,
    PickupAddressUpdate,
)

logger = structlog.get_logger()


def _coerce_default_flags(
    items: list[PickupAddressCreate],
    *,
    scope_has_addresses: bool = False,
) -> list[PickupAddressCreate]:
    """Ensure a default only when the scope has no addresses yet (first pickup for org/user)."""
    if not items:
        return []
    if any(x.is_default for x in items):
        return list(items)
    if scope_has_addresses:
        return list(items)
    first = items[0].model_copy(update={"is_default": True})
    return [first, *list(items[1:])]


def _lines_from_org_registered(org: Organization) -> dict[str, str | None]:
    return {
        "line_1": org.reg_address_line_1,
        "line_2": org.reg_address_line_2,
        "city": org.reg_city,
        "state": org.reg_state,
        "postcode": org.reg_postcode,
        "country": (org.reg_country or "United Kingdom").strip() or "United Kingdom",
    }


def _require_non_empty_org_address(base: dict[str, str | None], *, source_name: str) -> dict[str, str | None]:
    missing: list[str] = []
    for key in ("line_1", "city", "postcode", "country"):
        val = base.get(key)
        if val is None or not str(val).strip():
            missing.append(key)
    if missing:
        raise ValidationError(
            f"Pickup address cannot be same as {source_name}: organisation {source_name} address is incomplete "
            f"(missing: {', '.join(missing)})."
        )
    return base


def _lines_from_org_trading(org: Organization) -> dict[str, str | None]:
    return {
        "line_1": org.trading_address_line_1,
        "line_2": org.trading_address_line_2,
        "city": org.trading_address_city,
        "state": org.trading_address_state,
        "postcode": org.trading_address_postcode,
        "country": (org.trading_address_country or "").strip() or None,
    }


def _resolve_to_orm_dict(item: PickupAddressCreate, org: Organization | None) -> dict[str, Any]:
    if item.same_as_registered_address or item.same_as_trading_address:
        if org is None:
            raise ValidationError("Same as registered or trading address requires an organisation")
        if item.same_as_registered_address:
            base = _require_non_empty_org_address(_lines_from_org_registered(org), source_name="registered")
        else:
            base = _require_non_empty_org_address(_lines_from_org_trading(org), source_name="trading")
    else:
        base = {
            "line_1": item.line_1 or "",
            "line_2": item.line_2,
            "city": item.city or "",
            "state": item.state,
            "postcode": item.postcode or "",
            "country": item.country or "",
        }
    return {
        **base,
        "label": item.label,
        "contact_phone": (item.contact_phone or "").strip() or None,
        "latitude": item.latitude,
        "longitude": item.longitude,
        "is_default": item.is_default,
    }


def _insert_payload(
    resolved: dict[str, Any],
    *,
    organization_id: str | None,
    user_id: str | None,
    created_by_user_id: str,
) -> dict[str, Any]:
    return {
        "organization_id": organization_id,
        "user_id": user_id,
        "label": resolved.get("label"),
        "contact_phone": resolved.get("contact_phone"),
        "line_1": resolved["line_1"],
        "line_2": resolved.get("line_2"),
        "city": resolved["city"],
        "state": resolved.get("state"),
        "postcode": resolved["postcode"],
        "country": resolved["country"],
        "latitude": resolved.get("latitude"),
        "longitude": resolved.get("longitude"),
        "is_default": resolved["is_default"],
        "created_by_user_id": created_by_user_id,
    }


def _geocode_query_from_request(body: GeocodeAddressRequest) -> str:
    if body.query and body.query.strip():
        return body.query.strip()
    parts = [body.line_1, body.line_2, body.city, body.state, body.postcode, body.country]
    return ", ".join(p.strip() for p in parts if p and str(p).strip())


def _set_location_dict(data: dict[str, Any]) -> None:
    lat = data.get("latitude")
    lng = data.get("longitude")
    if lat is not None and lng is not None:
        data["location"] = WKTElement(f"POINT({lng} {lat})", srid=4326)
    elif "latitude" in data or "longitude" in data:
        data["location"] = None


def _request_ip_ua(request: Request | None) -> tuple[str | None, str | None]:
    if not request:
        return None, None
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


class PickupAddressService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = PickupAddressRepository(session)
        self._audit = AuditService(session)
        self._org_repo = OrganizationRepository(session)

    async def geocode_address(self, auth: AuthUser, body: GeocodeAddressRequest) -> GeocodeResultResponse:
        resolve_pickup_address_owner_from_auth(auth)
        data = await forward_geocode(_geocode_query_from_request(body))
        return GeocodeResultResponse.model_validate(data)

    async def list_addresses(self, auth: AuthUser) -> list[PickupAddressResponse]:
        owner = resolve_pickup_address_owner_from_auth(auth)
        rows = await self._repo.list_for_scope(
            organization_id=owner.organization_id,
            user_id=owner.user_id,
        )
        return [PickupAddressResponse.model_validate(r) for r in rows]

    async def get_address(self, auth: AuthUser, address_id: str) -> PickupAddressResponse:
        owner = resolve_pickup_address_owner_from_auth(auth)
        row = await self._repo.get_for_scope_or_none(
            address_id,
            organization_id=owner.organization_id,
            user_id=owner.user_id,
        )
        if row is None:
            raise NotFoundError(resource="pickup_address", id=address_id)
        return PickupAddressResponse.model_validate(row)

    async def create_addresses(self, auth: AuthUser, request: CreatePickupAddressesRequest) -> list[PickupAddressResponse]:
        owner = resolve_pickup_address_owner_from_auth(auth)
        org: Organization | None = None
        if owner.organization_id:
            org = await self._org_repo.get_by_id_or_404(owner.organization_id)
        else:
            for item in request.root:
                if item.same_as_registered_address or item.same_as_trading_address:
                    raise ValidationError("Same as registered or trading address is only available for organisation accounts")
        existing = await self._repo.list_for_scope(
            organization_id=owner.organization_id,
            user_id=owner.user_id,
        )
        items = _coerce_default_flags(list(request.root), scope_has_addresses=len(existing) > 0)
        results: list[PickupAddressResponse] = []
        for item in items:
            resolved = _resolve_to_orm_dict(item, org)
            if item.is_default:
                await self._repo.clear_default_for_scope(organization_id=owner.organization_id, user_id=owner.user_id)
            payload = _insert_payload(
                resolved,
                organization_id=owner.organization_id,
                user_id=owner.user_id,
                created_by_user_id=auth.id,
            )
            _set_location_dict(payload)
            row = await self._repo.create(payload)
            await self._session.flush()
            await self._session.refresh(row)
            results.append(PickupAddressResponse.model_validate(row))
        return results

    async def update_address(self, auth: AuthUser, address_id: str, data: PickupAddressUpdate) -> PickupAddressResponse:
        owner = resolve_pickup_address_owner_from_auth(auth)
        org_id, uid = owner.organization_id, owner.user_id
        scope = owner_scope_for_repo(owner)

        row = await self._repo.get_for_scope_or_none(address_id, organization_id=org_id, user_id=uid)
        if row is None:
            raise NotFoundError(resource="pickup_address", id=address_id)

        if data.is_default is True:
            await self._repo.clear_default_for_scope(organization_id=org_id, user_id=uid)

        raw = data.model_dump(exclude_unset=True)
        update_data = {k: v for k, v in raw.items() if v is not None}
        if "postcode" in update_data:
            update_data["postcode"] = str(update_data["postcode"]).strip().upper()

        merged_lat = update_data.get("latitude", row.latitude)
        merged_lng = update_data.get("longitude", row.longitude)
        update_data["latitude"] = merged_lat
        update_data["longitude"] = merged_lng
        _set_location_dict(update_data)

        updated = await self._repo.update_by_id(
            address_id,
            update_data,
            expected_version=None,
            **scope,
        )
        return PickupAddressResponse.model_validate(updated)

    async def delete_address(self, auth: AuthUser, address_id: str) -> None:
        owner = resolve_pickup_address_owner_from_auth(auth)
        org_id, uid = owner.organization_id, owner.user_id
        scope = owner_scope_for_repo(owner)
        row = await self._repo.get_for_scope_or_none(address_id, organization_id=org_id, user_id=uid)
        if row is None:
            raise NotFoundError(resource="pickup_address", id=address_id)
        await self._repo.hard_delete(address_id, **scope)

    async def list_for_organization(self, owner: PickupAddressOwner) -> list[PickupAddressResponse]:
        org_id = require_organization_id(owner)
        await self._org_repo.get_by_id_or_404(org_id)
        rows = await self._repo.list_for_scope(organization_id=org_id, user_id=None)
        return [PickupAddressResponse.model_validate(r) for r in rows]

    async def create_addresses_for_organization(
        self,
        owner: PickupAddressOwner,
        request: CreatePickupAddressesRequest,
        actor_user_id: str,
        *,
        auto_promote_first_default: bool = True,
    ) -> list[PickupAddressResponse]:
        org_id = require_organization_id(owner)
        org = await self._org_repo.get_by_id_or_404(org_id)
        existing = await self._repo.list_for_scope(organization_id=org_id, user_id=None)
        if auto_promote_first_default:
            items = _coerce_default_flags(list(request.root), scope_has_addresses=len(existing) > 0)
        else:
            items = list(request.root)
        results: list[PickupAddressResponse] = []
        for item in items:
            resolved = _resolve_to_orm_dict(item, org)
            if item.is_default:
                await self._repo.clear_default_for_scope(organization_id=org_id, user_id=None)
            payload = _insert_payload(
                resolved,
                organization_id=org_id,
                user_id=None,
                created_by_user_id=actor_user_id,
            )
            _set_location_dict(payload)
            row = await self._repo.create(payload)
            await self._session.flush()
            await self._session.refresh(row)
            ip, ua = _request_ip_ua(self._request)
            await self._audit.log(
                action="org_pickup_address.created",
                entity_type="pickup_address",
                entity_id=row.id,
                user_id=actor_user_id,
                new_value={"org_id": org_id, "is_default": item.is_default},
                ip_address=ip,
                user_agent=ua,
                organization_id=org_id,
                severity="NOTICE",
                category=AuditCategory.ACCOUNT,
                event_type=AuditEventType.ACCOUNT_UPDATED,
            )
            logger.info("org_pickup_address.created", address_id=row.id, org_id=org_id)
            results.append(PickupAddressResponse.model_validate(row))
        return results

    async def update_for_organization(
        self,
        owner: PickupAddressOwner,
        address_id: str,
        data: PickupAddressUpdate,
        actor_user_id: str,
    ) -> PickupAddressResponse:
        org_id = require_organization_id(owner)
        await self._org_repo.get_by_id_or_404(org_id)
        row = await self._repo.get_for_scope_or_none(address_id, organization_id=org_id, user_id=None)
        if row is None:
            raise NotFoundError(resource="pickup_address", id=address_id)

        if data.is_default is True:
            await self._repo.clear_default_for_scope(organization_id=org_id, user_id=None)

        raw = data.model_dump(exclude_unset=True)
        update_data = {k: v for k, v in raw.items() if v is not None}
        if "postcode" in update_data:
            update_data["postcode"] = str(update_data["postcode"]).strip().upper()

        merged_lat = update_data.get("latitude", row.latitude)
        merged_lng = update_data.get("longitude", row.longitude)
        update_data["latitude"] = merged_lat
        update_data["longitude"] = merged_lng
        _set_location_dict(update_data)

        updated = await self._repo.update_by_id(
            address_id,
            update_data,
            expected_version=None,
            organization_id=org_id,
        )
        ip, ua = _request_ip_ua(self._request)
        audit_value = {k: v for k, v in update_data.items() if k != "location"}
        await self._audit.log(
            action="org_pickup_address.updated",
            entity_type="pickup_address",
            entity_id=address_id,
            user_id=actor_user_id,
            new_value=audit_value,
            ip_address=ip,
            user_agent=ua,
            organization_id=org_id,
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.ACCOUNT_CONFIG_UPDATED,
        )
        logger.info("org_pickup_address.updated", address_id=address_id, org_id=org_id)
        return PickupAddressResponse.model_validate(updated)

    async def delete_for_organization(
        self,
        owner: PickupAddressOwner,
        address_id: str,
        actor_user_id: str,
    ) -> None:
        org_id = require_organization_id(owner)
        await self._org_repo.get_by_id_or_404(org_id)
        row = await self._repo.get_for_scope_or_none(address_id, organization_id=org_id, user_id=None)
        if row is None:
            raise NotFoundError(resource="pickup_address", id=address_id)

        was_default = row.is_default
        await self._repo.hard_delete(address_id, organization_id=org_id)

        if was_default:
            remaining = await self._repo.list_for_scope(organization_id=org_id, user_id=None)
            if remaining:
                await self._repo.update_by_id(remaining[0].id, {"is_default": True}, organization_id=org_id)

        ip, ua = _request_ip_ua(self._request)
        await self._audit.log(
            action="org_pickup_address.deleted",
            entity_type="pickup_address",
            entity_id=address_id,
            user_id=actor_user_id,
            new_value={"org_id": org_id},
            ip_address=ip,
            user_agent=ua,
            organization_id=org_id,
            severity="CRITICAL",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.ACCOUNT_CONFIG_UPDATED,
        )
        logger.info("org_pickup_address.deleted", address_id=address_id, org_id=org_id)

    async def assert_usable_for_order(
        self,
        pickup_address_id: str,
        *,
        organization_id: str,
    ) -> None:
        """Verify a pickup address exists and belongs to the order's organisation."""
        row = await self._repo.get_by_id(pickup_address_id)
        if row is None:
            raise NotFoundError(resource="pickup_address", id=pickup_address_id)
        if row.organization_id != organization_id:
            raise ValidationError("Pickup address does not belong to your organisation")
