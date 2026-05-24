"""Business logic for admin service tier configuration."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ConflictError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.organizations.models import Organization, OrgServiceTierContractLine
from app.modules.service_tiers.constants import SUPERFAST_TIER_NAME, is_superfast_global_tier, is_superfast_tier_name
from app.modules.service_tiers.effective_merge import merge_effective_service_tiers
from app.modules.service_tiers.enums import ServiceTierAudience, ServiceTierScopeType, ServiceTierStatus
from app.modules.service_tiers.models import ServiceTier
from app.modules.service_tiers.repository import ServiceTierRepository

_MONEY_QUANT = Decimal("0.01")

_TIER_UPDATE_ALLOWED: frozenset[str] = frozenset(
    {
        "tier_name",
        "description",
        "duration_days",
        "error_margin_kg",
        "price_per_kg",
        "price_per_package",
        "base_price",
        "available_for",
        "color",
        "icon",
        "status",
    }
)


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


class ServiceTierService(BaseService):
    """Service layer for managing service tiers."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._tier_repo = ServiceTierRepository(session)
        self._audit = AuditService(session)
        self._ip_address = request.client.host if request and request.client else None
        self._user_agent = request.headers.get("user-agent") if request else None

    async def _log_audit(
        self,
        action: str,
        *,
        entity_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        severity: str = "INFO",
        category: AuditCategory = AuditCategory.BILLING,
        event_type: AuditEventType | str = AuditEventType.BILLING_CONFIG_CHANGED,
    ) -> None:
        await self._audit.log(
            action=action,
            entity_type="service_tier",
            entity_id=entity_id,
            user_id=user_id,
            user_role=user_role,
            old_value=old_value,
            new_value=new_value,
            ip_address=self._ip_address,
            user_agent=self._user_agent,
            severity=severity,
            category=category,
            event_type=event_type,
        )

    async def _validate_scope(self, *, scope_type: str | None, scope_org_id: str | None) -> None:
        if scope_type not in {ServiceTierScopeType.GLOBAL.value, ServiceTierScopeType.ORG.value}:
            raise ValidationError("scope_type must be GLOBAL or ORG")
        if scope_type == ServiceTierScopeType.GLOBAL.value:
            if scope_org_id is not None:
                raise ValidationError("scope_org_id must be null for GLOBAL tiers")
            return
        if not scope_org_id:
            raise ValidationError("scope_org_id is required when scope_type=ORG")
        org = await self._session.get(Organization, scope_org_id)
        if org is None:
            raise ValidationError("scope_org_id references a non-existent organization")

    async def _ensure_unique_name_audience(
        self,
        *,
        scope_type: str,
        scope_org_id: str | None,
        tier_name: str,
        available_for: str,
        exclude_tier_id: str | None = None,
    ) -> None:
        if scope_type == ServiceTierScopeType.GLOBAL.value:
            exists = await self._tier_repo.exists_global_name_audience(
                tier_name=tier_name,
                available_for=available_for,
                exclude_id=exclude_tier_id,
            )
            if exists:
                raise ValidationError(f"A global service tier already exists for name={tier_name!r} and available_for={available_for!r}.")
            return
        assert scope_org_id is not None
        exists = await self._tier_repo.exists_org_name_audience(
            organization_id=scope_org_id,
            tier_name=tier_name,
            available_for=available_for,
            exclude_id=exclude_tier_id,
        )
        if exists:
            raise ValidationError(f"An organisation service tier already exists for this org, name={tier_name!r}, " f"available_for={available_for!r}.")

    async def _assert_superfast_mutation_allowed(
        self,
        tier: ServiceTier,
        *,
        operation: str,
        data: dict[str, object] | None = None,
    ) -> None:
        if operation == "delete":
            if is_superfast_global_tier(tier):
                raise ValidationError("Superfast is a system tier and cannot be deleted.")
            return
        if not is_superfast_tier_name(tier.tier_name):
            return
        if operation == "update" and data:
            if "tier_name" in data and str(data["tier_name"]) != SUPERFAST_TIER_NAME:
                raise ValidationError("Superfast tier name cannot be changed.")
            if is_superfast_global_tier(tier) and "available_for" in data:
                af = data["available_for"]
                af_val = af.value if hasattr(af, "value") else str(af)
                if af_val != tier.available_for:
                    raise ValidationError("Superfast audience cannot be changed.")
            if "status" in data:
                st = data["status"]
                st_val = st.value if hasattr(st, "value") else str(st)
                if st_val != ServiceTierStatus.ACTIVE.value:
                    raise ValidationError("Superfast cannot be deactivated.")

    # ── CRUD ───────────────────────────────────────────────────────

    async def list_tiers(
        self,
        *,
        scope_type: ServiceTierScopeType | None = None,
        scope_org_id: str | None = None,
        available_for: list[ServiceTierAudience] | None = None,
        status: list[ServiceTierStatus] | None = None,
        search: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        min_days: int | None = None,
        max_days: int | None = None,
    ) -> list[ServiceTier]:
        if scope_type == ServiceTierScopeType.ORG and not scope_org_id:
            raise ValidationError("scope_org_id is required when scope_type=ORG")
        if scope_type == ServiceTierScopeType.GLOBAL and scope_org_id is not None:
            raise ValidationError("scope_org_id must be omitted when scope_type=GLOBAL")
        if min_price is not None and max_price is not None and min_price > max_price:
            raise ValidationError("min_price cannot be greater than max_price")
        if min_days is not None and max_days is not None and min_days > max_days:
            raise ValidationError("min_days cannot be greater than max_days")

        return await self._tier_repo.list_by_filters(
            scope_type=scope_type.value if scope_type is not None else None,
            scope_org_id=scope_org_id,
            available_for=[a.value for a in available_for] if available_for else None,
            status=[s.value for s in status] if status else None,
            search=search,
            min_price=Decimal(str(min_price)) if min_price is not None else None,
            max_price=Decimal(str(max_price)) if max_price is not None else None,
            min_days=min_days,
            max_days=max_days,
        )

    async def get_tier(self, tier_id: str) -> ServiceTier:
        return await self._tier_repo.get_by_id_or_404(tier_id)

    async def create_tier(
        self,
        *,
        tier_name: str,
        description: str | None = None,
        duration_days: int,
        error_margin_kg: int,
        price_per_kg: Decimal,
        price_per_package: Decimal,
        base_price: Decimal,
        available_for: ServiceTierAudience,
        scope_type: ServiceTierScopeType = ServiceTierScopeType.GLOBAL,
        scope_org_id: str | None = None,
        color: str | None = None,
        icon: str | None = None,
        status: ServiceTierStatus = ServiceTierStatus.ACTIVE,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> ServiceTier:
        await self._validate_scope(scope_type=scope_type.value, scope_org_id=scope_org_id)
        if tier_name == SUPERFAST_TIER_NAME and scope_type == ServiceTierScopeType.GLOBAL:
            raise ValidationError("Superfast is a system tier and cannot be created via the API.")
        await self._ensure_unique_name_audience(
            scope_type=scope_type.value,
            scope_org_id=scope_org_id,
            tier_name=tier_name,
            available_for=available_for.value,
        )
        tier = await self._tier_repo.create(
            {
                "tier_name": tier_name,
                "description": description,
                "duration_days": duration_days,
                "error_margin_kg": error_margin_kg,
                "price_per_kg": _quantize_money(price_per_kg),
                "price_per_package": _quantize_money(price_per_package),
                "base_price": _quantize_money(base_price),
                "available_for": available_for.value,
                "scope_type": scope_type.value,
                "scope_org_id": scope_org_id,
                "color": color,
                "icon": icon,
                "status": status.value,
            }
        )

        await self._log_audit(
            "service_tier.create",
            entity_id=tier.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={
                "tier_name": tier.tier_name,
                "scope_type": tier.scope_type,
                "scope_org_id": tier.scope_org_id,
                "duration_days": tier.duration_days,
                "error_margin_kg": tier.error_margin_kg,
                "price_per_kg": str(tier.price_per_kg),
                "price_per_package": str(tier.price_per_package),
                "base_price": str(tier.base_price),
                "available_for": tier.available_for,
                "color": tier.color,
                "icon": tier.icon,
                "status": tier.status,
            },
            severity="NOTICE",
        )

        return tier

    async def update_tier(
        self,
        *,
        tier_id: str,
        data: dict[str, object],
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
        expected_version: int | None = None,
    ) -> ServiceTier:
        tier = await self._tier_repo.get_by_id_or_404(tier_id)

        if "scope_type" in data or "scope_org_id" in data:
            raise ValidationError("scope_type and scope_org_id cannot be updated once a tier is created.")

        unknown = set(data) - _TIER_UPDATE_ALLOWED
        if unknown:
            raise ValidationError(f"Cannot update unknown field(s): {', '.join(sorted(unknown))}")

        await self._assert_superfast_mutation_allowed(tier, operation="update", data=data)

        # Serialize enum values to their string representation for the DB layer.
        db_data: dict[str, object] = {}
        for key, value in data.items():
            if isinstance(value, (ServiceTierAudience, ServiceTierStatus)):
                db_data[key] = value.value
            elif key in {"price_per_kg", "price_per_package", "base_price"} and value is not None:
                db_data[key] = _quantize_money(Decimal(str(value)))
            else:
                db_data[key] = value

        next_name = db_data.get("tier_name", tier.tier_name)
        next_audience = db_data.get("available_for", tier.available_for)
        if isinstance(next_audience, ServiceTierAudience):
            next_audience = next_audience.value
        if next_name != tier.tier_name or next_audience != tier.available_for:
            await self._ensure_unique_name_audience(
                scope_type=tier.scope_type,
                scope_org_id=tier.scope_org_id,
                tier_name=str(next_name),
                available_for=str(next_audience),
                exclude_tier_id=tier_id,
            )

        updated = await self._tier_repo.update_by_id(tier_id, db_data, expected_version=expected_version)

        await self._log_audit(
            "service_tier.update",
            entity_id=tier_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "tier_name": tier.tier_name,
                "duration_days": tier.duration_days,
                "price_per_package": str(tier.price_per_package),
                "available_for": tier.available_for,
                "status": tier.status,
            },
            new_value={k: str(v) if hasattr(v, "quantize") else v for k, v in data.items()},
            severity="NOTICE",
        )

        return updated

    async def delete_tier(
        self,
        *,
        tier_id: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        existing = await self._tier_repo.get_by_id_or_404(tier_id)
        await self._assert_superfast_mutation_allowed(existing, operation="delete")

        # FK RESTRICT on org_service_tier_contract_lines.global_template_id → service_tier.id
        # blocks delete whenever any contract line points at this id, regardless of scope_type.
        global_template_ref_count = (
            await self._session.execute(select(func.count()).select_from(OrgServiceTierContractLine).where(OrgServiceTierContractLine.global_template_id == tier_id))
        ).scalar_one()
        if int(global_template_ref_count or 0) > 0:
            raise ValidationError(
                "Cannot delete this service tier: organisation contract lines still reference "
                "it as a global template (global_template_id). Remove it from all organisation "
                "pricing plans first, or replace those contract lines, or set the tier to "
                "INACTIVE instead of deleting."
            )

        # Custom contract lines require org_tier_id (ck_org_st_contract_mode_org_tier). When the
        # ORG tier row is removed, revert those lines to standard pricing on the same template.
        await self._session.execute(update(OrgServiceTierContractLine).where(OrgServiceTierContractLine.org_tier_id == tier_id).values(org_tier_id=None, mode="standard"))
        await self._session.flush()

        await self._tier_repo.hard_delete(tier_id)
        await self._log_audit(
            "service_tier.delete",
            entity_id=tier_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "tier_name": existing.tier_name,
                "scope_type": existing.scope_type,
                "scope_org_id": existing.scope_org_id,
                "duration_days": existing.duration_days,
                "price_per_package": str(existing.price_per_package),
                "available_for": existing.available_for,
                "color": existing.color,
                "icon": existing.icon,
            },
            severity="CRITICAL",
        )

    async def get_effective_tiers_with_source_for_org(self, organization_id: str) -> list[dict[str, Any]]:
        org = await self._session.get(Organization, organization_id)
        if org is None:
            raise NotFoundError(resource="Organization", id=organization_id)

        global_rows = await self._tier_repo.list_active_global()
        org_rows = await self._tier_repo.list_active_for_org(organization_id)
        merged = merge_effective_service_tiers(global_rows, org_rows)

        # Enrich each row with contract-line state (permitted, is_default, mode/plain_type).
        # org_service_tier_contract_lines is the live source — never stale like pricing_plans JSON.
        from app.modules.organizations.org_service_tier_contract_repository import (  # noqa: PLC0415
            OrgServiceTierContractRepository,
        )

        contract_repo = OrgServiceTierContractRepository(self._session)
        contract_lines = await contract_repo.list_for_organization(organization_id)
        by_global: dict[str, Any] = {ln.global_template_id: ln for ln in contract_lines}

        superfast = await self._tier_repo.find_global_superfast()
        superfast_id = str(superfast.id) if superfast is not None else None

        for row in merged:
            gid = row.get("global_tier_id")
            ln = by_global.get(gid) if gid else None
            is_superfast = superfast_id is not None and str(gid or "") == superfast_id
            if ln is not None:
                row["permitted"] = True if is_superfast else bool(ln.permitted)
                row["is_default"] = bool(ln.is_default)
                row["plain_type"] = str(ln.mode)  # "standard" | "custom"
            else:
                # ORG-only tiers (no matching global) were created for this org specifically.
                # GLOBAL tiers with no contract line have not been configured for this org yet.
                is_org_only = row.get("source_scope_type") == "ORG" and not row.get("is_override")
                row["permitted"] = True if is_superfast else is_org_only
                row["is_default"] = False
                row["plain_type"] = "custom" if is_org_only else "standard"
            if is_superfast:
                row["permitted_locked"] = True
                row["tier_name_locked"] = True
                row["is_system_tier"] = True

        return merged

    async def resolve_effective_tier_for_org(
        self,
        organization_id: str,
        *,
        tier_id: str | None = None,
        tier_name: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a single effective service tier for the given organisation.

        Uses the same merged/enriched data as ``get_effective_tiers_with_source_for_org`` —
        i.e. globals + org overrides + live contract-line state — so price calculation never
        reads stale data from ``organizations.pricing_plans`` JSON.

        Match preference:
          1. ``tier_id`` exact match against either the row's effective tier ``id`` or its
             ``global_tier_id`` (caller may pass either; both come back from the effective list)
          2. ``tier_name`` case-insensitive match against ``tier_name`` (exact, then substring)

        Returns a *flat* dict with the same fields shape that the
        ``GET /service-tiers/effective-for-org`` endpoint returns — base_price, price_per_kg,
        price_per_package, tier_name, color, icon, etc. — so price calculation can read them
        directly. The underlying merge step keeps the ORM row nested under a ``"tier"`` key,
        so we unwrap it here.

        Raises:
            ValidationError: when no identifier is supplied or the tier is not permitted.
            NotFoundError: when no tier matches.
        """
        if not tier_id and not tier_name:
            raise ValidationError("tier_id or tier_name is required to resolve a service tier")

        effective = await self.get_effective_tiers_with_source_for_org(organization_id)

        tid = (tier_id or "").strip() or None
        tname = (tier_name or "").strip() or None

        def _tier_name_of(row: dict[str, Any]) -> str:
            tier = row.get("tier")
            return str(getattr(tier, "tier_name", "") or row.get("tier_name") or "")

        def _tier_id_of(row: dict[str, Any]) -> str:
            tier = row.get("tier")
            return str(getattr(tier, "id", "") or row.get("source_tier_id") or "")

        match: dict[str, Any] | None = None
        if tid is not None:
            for row in effective:
                if _tier_id_of(row) == tid or str(row.get("global_tier_id") or "") == tid:
                    match = row
                    break

        if match is None and tname is not None:
            needle = tname.upper()
            for row in effective:
                name = _tier_name_of(row).upper()
                if name and name == needle:
                    match = row
                    break
            if match is None:
                for row in effective:
                    name = _tier_name_of(row).upper()
                    if name and needle in name:
                        match = row
                        break

        if match is None:
            label = tid or tname or ""
            raise NotFoundError(resource="ServiceTier", id=label)

        if match.get("permitted") is not True:
            label = tid or tname or ""
            raise ValidationError(f"Service tier '{label}' is not permitted for this organisation")

        # Flatten: merge tier ORM columns with enrichment fields into a single dict.
        tier_obj = match.get("tier")
        if tier_obj is None:
            return match
        st_value = tier_obj.status
        status_str = st_value.value if isinstance(st_value, ServiceTierStatus) else str(st_value)
        return {
            "id": tier_obj.id,
            "tier_name": tier_obj.tier_name,
            "description": tier_obj.description,
            "duration_days": tier_obj.duration_days,
            "error_margin_kg": tier_obj.error_margin_kg,
            "price_per_kg": float(tier_obj.price_per_kg),
            "price_per_package": float(tier_obj.price_per_package),
            "base_price": float(tier_obj.base_price),
            "available_for": tier_obj.available_for,
            "scope_type": tier_obj.scope_type,
            "scope_org_id": tier_obj.scope_org_id,
            "color": tier_obj.color,
            "icon": tier_obj.icon,
            "status": status_str,
            "is_override": bool(match.get("is_override")),
            "source_scope_type": match.get("source_scope_type"),
            "global_tier_id": match.get("global_tier_id"),
            "permitted": match.get("permitted"),
            "is_default": match.get("is_default"),
            "plain_type": match.get("plain_type"),
            "created_at": tier_obj.created_at,
            "updated_at": tier_obj.updated_at,
            "version": tier_obj.version,
        }

    async def upsert_org_tier_override(
        self,
        *,
        organization_id: str,
        tier_name: str,
        available_for: ServiceTierAudience,
        payload: dict[str, Any],
        conditions: dict[str, Any] | None = None,
        expected_version: int | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> ServiceTier:
        """Create or update an ORG-scoped tier row for (tier_name, available_for).

        ``conditions`` is reserved for API symmetry with suspension rules (unused).
        """
        _ = conditions
        await self._validate_scope(scope_type=ServiceTierScopeType.ORG.value, scope_org_id=organization_id)

        existing = await self._tier_repo.find_org_by_name_audience(
            organization_id=organization_id,
            tier_name=tier_name,
            available_for=available_for.value,
        )
        if existing is not None:
            filtered = {k: v for k, v in payload.items() if k in _TIER_UPDATE_ALLOWED}
            try:
                return await self.update_tier(
                    tier_id=existing.id,
                    data=filtered,
                    audit_user_id=audit_user_id,
                    audit_user_role=audit_user_role,
                    expected_version=expected_version,
                )
            except ConflictError:
                return await self.update_tier(
                    tier_id=existing.id,
                    data=filtered,
                    audit_user_id=audit_user_id,
                    audit_user_role=audit_user_role,
                    expected_version=None,
                )

        global_src = await self._tier_repo.find_global_by_name_audience(
            tier_name=tier_name,
            available_for=available_for.value,
        )

        def _dec(key: str, default: Decimal) -> Decimal:
            v = payload.get(key)
            if v is None:
                return _quantize_money(default)
            return _quantize_money(Decimal(str(v)))

        def _int(key: str, default: int) -> int:
            v = payload.get(key)
            if v is None:
                return default
            return int(v)

        def _coerce_status(v: object | None, fallback: ServiceTierStatus) -> ServiceTierStatus:
            if v is None:
                return fallback
            if isinstance(v, ServiceTierStatus):
                return v
            return ServiceTierStatus(str(v))

        if global_src is not None:
            gs_status = global_src.status if isinstance(global_src.status, ServiceTierStatus) else ServiceTierStatus(str(global_src.status))
            return await self.create_tier(
                tier_name=tier_name,
                description=payload.get("description", global_src.description),
                duration_days=int(payload.get("duration_days", global_src.duration_days)),
                error_margin_kg=_int("error_margin_kg", global_src.error_margin_kg),
                price_per_kg=_dec("price_per_kg", global_src.price_per_kg),
                price_per_package=_dec("price_per_package", global_src.price_per_package),
                base_price=_dec("base_price", global_src.base_price),
                available_for=available_for,
                scope_type=ServiceTierScopeType.ORG,
                scope_org_id=organization_id,
                color=payload.get("color", global_src.color),
                icon=payload.get("icon", global_src.icon),
                status=_coerce_status(payload.get("status"), gs_status),
                audit_user_id=audit_user_id,
                audit_user_role=audit_user_role,
            )

        if not payload.get("duration_days"):
            raise ValidationError("duration_days is required when no global tier exists for this name and audience")
        return await self.create_tier(
            tier_name=tier_name,
            description=payload.get("description"),
            duration_days=int(payload["duration_days"]),
            error_margin_kg=_int("error_margin_kg", 0),
            price_per_kg=_dec("price_per_kg", Decimal("0")),
            price_per_package=_dec("price_per_package", Decimal("0")),
            base_price=_dec("base_price", Decimal("0")),
            available_for=available_for,
            scope_type=ServiceTierScopeType.ORG,
            scope_org_id=organization_id,
            color=payload.get("color"),
            icon=payload.get("icon"),
            status=_coerce_status(payload.get("status"), ServiceTierStatus.ACTIVE),
            audit_user_id=audit_user_id,
            audit_user_role=audit_user_role,
        )
