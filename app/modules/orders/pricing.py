from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ValidationError
from app.modules.org_discounts.enums import DiscountType
from app.modules.org_discounts.models import OrgDiscountConfig
from app.modules.orders.models import Order
from app.modules.organizations.enums import OrganizationStatus, VatRate
from app.modules.organizations.models import Organization, OrgPaymentConfig


_ZERO = Decimal("0")
_TWO_PLACES = Decimal("0.01")

_VAT_RATE_PCT: dict[str, Decimal] = {
    VatRate.STANDARD_20.value: Decimal("20.00"),
    VatRate.REDUCED_5.value: Decimal("5.00"),
    VatRate.ZERO_RATED.value: Decimal("0.00"),
    VatRate.EXEMPT.value: Decimal("0.00"),
}


def _q(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _s(value: Decimal) -> str:
    return format(_q(value), "f")


@dataclass(slots=True)
class PackageInput:
    index: int
    declared_weight_kg: float | None
    length_cm: float | None
    width_cm: float | None
    height_cm: float | None
    package_uuid: str | None = None
    package_ref: str | None = None
    price_breakdown: dict | None = None


@dataclass(slots=True)
class StopInput:
    index: int
    service_tier_name: str | None = None
    service_tier_id: str | None = None
    packages: list[PackageInput] = field(default_factory=list)
    stop_uuid: str | None = None
    tracking_id: str | None = None
    price_breakdown: dict | None = None
    resolved_plan: dict | None = field(default=None)


@dataclass(slots=True)
class PricingContext:
    org: Organization
    payment_config: OrgPaymentConfig
    discounts: list[OrgDiscountConfig]
    order_count: int


@dataclass(slots=True)
class PricingResult:
    subtotal: Decimal
    vat_amount: Decimal
    total_amount: Decimal
    breakdown: dict[str, Any]


async def load_pricing_context(session: AsyncSession, organization_id: str) -> PricingContext:
    """Load everything needed to price an order in a single pass.

    Reads the ACTIVE org row (including pricing_plans JSON), its payment config,
    enabled discounts, and the org's current order count. Tier names and rates
    come only from org.pricing_plans, not from the global ServiceTier table.
    """
    org_stmt = select(Organization).where(
        Organization.id == organization_id,
        Organization.status == OrganizationStatus.ACTIVE,
    )
    org = (await session.execute(org_stmt)).scalar_one_or_none()
    if org is None:
        raise ValidationError(f"Organization {organization_id} is not active or does not exist")

    pc_stmt = select(OrgPaymentConfig).where(OrgPaymentConfig.organization_id == organization_id)
    pc = (await session.execute(pc_stmt)).scalar_one_or_none()
    if pc is None:
        raise ValidationError(f"Organization {organization_id} payment config is missing")

    disc_stmt = select(OrgDiscountConfig).where(OrgDiscountConfig.organization_id == organization_id)
    discounts = list((await session.execute(disc_stmt)).scalars().all())

    count_stmt = select(func.count()).select_from(Order).where(Order.organization_id == organization_id)
    order_count = int((await session.execute(count_stmt)).scalar_one())

    return PricingContext(
        org=org,
        payment_config=pc,
        discounts=discounts,
        order_count=order_count,
    )


def validate_package_restrictions(org: Organization, stops: list[StopInput]) -> None:
    max_w = org.max_package_weight
    max_l = org.max_package_length
    max_wd = org.max_package_width
    max_h = org.max_package_height

    for stop in stops:
        for pkg in stop.packages:
            if max_w is not None and pkg.declared_weight_kg is not None and pkg.declared_weight_kg > max_w:
                raise ValidationError(
                    f"Stop {stop.index} package {pkg.index}: declared weight {pkg.declared_weight_kg}kg exceeds organisation limit {max_w}kg"
                )
            if max_l is not None and pkg.length_cm is not None and pkg.length_cm > max_l:
                raise ValidationError(
                    f"Stop {stop.index} package {pkg.index}: length {pkg.length_cm}cm exceeds organisation limit {max_l}cm"
                )
            if max_wd is not None and pkg.width_cm is not None and pkg.width_cm > max_wd:
                raise ValidationError(
                    f"Stop {stop.index} package {pkg.index}: width {pkg.width_cm}cm exceeds organisation limit {max_wd}cm"
                )
            if max_h is not None and pkg.height_cm is not None and pkg.height_cm > max_h:
                raise ValidationError(
                    f"Stop {stop.index} package {pkg.index}: height {pkg.height_cm}cm exceeds organisation limit {max_h}cm"
                )


def plan_tier_id(plan: dict | None) -> str | None:
    if not plan:
        return None
    val = plan.get("id_price_tier")
    return str(val) if val else None


def plan_display_name(plan: dict | None) -> str | None:
    if not plan:
        return None
    name = plan.get("plain_name")
    if name:
        return str(name)
    return None


def effective_tier_to_plan(tier: dict) -> dict:
    """Convert an effective-service-tier row (from ServiceTierService) to the plan dict
    shape this module's pricing math consumes.

    The effective row has columns like ``id``, ``tier_name``, ``base_price``, ``price_per_kg``,
    ``price_per_package``, ``global_tier_id``, ``plain_type``, ``color``, ``icon`` etc.
    The pricing math reads the legacy keys ``id_price_tier``, ``plain_name``, ``base_price``,
    ``price_per_package``, ``price_per_kg`` (used by :func:`_tier_prices` and
    :func:`_snapshot_plan`). The remaining columns (color / icon / availability / scope flags)
    are carried through so downstream snapshots and the FE breakdown have access to them too.
    """
    # Prefer the global tier id so discounts (which reference the global id) match correctly.
    id_price_tier = tier.get("global_tier_id") or tier.get("id")
    return {
        # Pricing-math fields (required by `_tier_prices` / `_snapshot_plan`).
        "id_price_tier": str(id_price_tier) if id_price_tier else None,
        "plain_name": tier.get("tier_name"),
        "plain_type": tier.get("plain_type"),
        "days": tier.get("duration_days"),
        "base_price": tier.get("base_price"),
        "price_per_package": tier.get("price_per_package"),
        "price_per_kg": tier.get("price_per_kg"),
        "tier_name_at_order_time": tier.get("tier_name"),
        # Presentation / metadata — carried through so the FE breakdown can render badges
        # and the order snapshot retains everything the org saw at the time of pricing.
        "service_tier_id": tier.get("id"),
        "global_tier_id": tier.get("global_tier_id"),
        "scope_type": tier.get("scope_type"),
        "source_scope_type": tier.get("source_scope_type"),
        "scope_org_id": tier.get("scope_org_id"),
        "available_for": tier.get("available_for"),
        "status": tier.get("status"),
        "color": tier.get("color"),
        "icon": tier.get("icon"),
        "description": tier.get("description"),
        "error_margin_kg": tier.get("error_margin_kg"),
        "duration_days": tier.get("duration_days"),
        "is_default": tier.get("is_default"),
        "permitted": tier.get("permitted"),
        "is_override": tier.get("is_override"),
    }


def resolve_plan_for_stop(
    stop: StopInput,
    org: Organization,
) -> dict:
    """Resolve the pricing plan for a stop.

    The preferred path is a plan pre-resolved via
    :func:`app.modules.service_tiers.service.ServiceTierService.resolve_effective_tier_for_org`
    and attached to ``stop.resolved_plan`` before pricing runs. That keeps the math in sync
    with the org's live contract lines + global/org-override merge.

    If no pre-resolved plan is present this falls back to the legacy
    ``organizations.pricing_plans`` JSON lookup for backwards compatibility, but new callers
    should always pre-resolve.
    """
    if stop.resolved_plan is not None:
        return stop.resolved_plan

    plans = org.pricing_plans or []
    if not plans:
        raise ValidationError(f"Stop {stop.index}: organisation has no pricing plans configured")

    tier_id = (stop.service_tier_id or "").strip() or None
    tier_name = (stop.service_tier_name or "").strip() or None
    if not tier_id and not tier_name:
        raise ValidationError(f"Stop {stop.index}: service_tier_name or service_tier_id is required")

    if tier_id:
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            if str(plan.get("id_price_tier") or "") == tier_id:
                return plan

    if tier_name:
        needle = tier_name.upper()
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            plain_name = str(plan.get("plain_name") or "").upper()
            if plain_name and plain_name == needle:
                return plan
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            plain_name = str(plan.get("plain_name") or "").upper()
            if plain_name and needle in plain_name:
                return plan

    label = tier_id or tier_name or ""
    raise ValidationError(
        f"Stop {stop.index}: no pricing plan matches service tier '{label}'. "
        "Ensure the organisation has a pricing plan referencing this tier."
    )


def _tier_prices(plan: dict) -> tuple[Decimal, Decimal, Decimal]:
    base_price = Decimal(str(plan.get("base_price") or "0"))
    per_package = Decimal(str(plan.get("price_per_package") or "0"))
    per_kg = Decimal(str(plan.get("price_per_kg") or "0"))
    return _q(base_price), _q(per_package), _q(per_kg)


def _snapshot_plan(plan: dict) -> dict:
    base_price, per_package, per_kg = _tier_prices(plan)
    plain = plan.get("plain_name")
    tier_label = plan.get("tier_name_at_order_time") or (str(plain) if plain is not None else None)
    return {
        "id_price_tier": plan.get("id_price_tier"),
        "plain_name": plan.get("plain_name"),
        "plain_type": plan.get("plain_type"),
        "days": plan.get("days"),
        "base_price": _s(base_price),
        "price_per_package": _s(per_package),
        "price_per_kg": _s(per_kg),
        "tier_name_at_order_time": tier_label,
        # Presentation metadata — populated when the plan came from the live effective-tier
        # service. Carries through so the FE breakdown can render the tier badge with the
        # same color/icon the user picked on Step 2 without a second lookup.
        "service_tier_id": plan.get("service_tier_id"),
        "global_tier_id": plan.get("global_tier_id"),
        "color": plan.get("color"),
        "icon": plan.get("icon"),
        "description": plan.get("description"),
        "duration_days": plan.get("duration_days") or plan.get("days"),
        "error_margin_kg": plan.get("error_margin_kg"),
        "available_for": plan.get("available_for"),
        "scope_type": plan.get("scope_type"),
        "source_scope_type": plan.get("source_scope_type"),
        "scope_org_id": plan.get("scope_org_id"),
        "is_default": plan.get("is_default"),
        "is_override": plan.get("is_override"),
    }


def _package_breakdown(pkg: PackageInput, per_package: Decimal, per_kg: Decimal) -> dict:
    weight = Decimal(str(pkg.declared_weight_kg or 0))
    weight_charge = _q(per_kg * weight)
    total = _q(per_package + weight_charge)
    return {
        "id": pkg.package_uuid,
        "package_id": pkg.package_ref,
        "package_index": pkg.index,
        "declared_weight_kg": float(weight) if pkg.declared_weight_kg is not None else None,
        "per_package_charge": _s(per_package),
        "weight_charge": {
            "price_per_kg": _s(per_kg),
            "weight_kg": float(weight) if pkg.declared_weight_kg is not None else None,
            "amount": _s(weight_charge),
        },
        "total": _s(total),
    }


def _discount_active(discount: OrgDiscountConfig, today: date) -> bool:
    if not discount.is_enabled:
        return False
    if discount.discount_type == DiscountType.VOLUME_TIERED:
        return True
    if discount.valid_from and today < discount.valid_from:
        return False
    if discount.valid_until and today > discount.valid_until:
        return False
    return True


def _volume_tier_pct(volume_tiers: list | None, order_count: int) -> Decimal:
    if not volume_tiers:
        return _ZERO
    for tier in volume_tiers:
        min_b = int(tier.get("min_bookings", 0))
        max_b = tier.get("max_bookings")
        upper_ok = max_b is None or order_count <= int(max_b)
        if order_count >= min_b and upper_ok:
            return Decimal(str(tier.get("discount_pct") or "0"))
    return _ZERO


def _apply_discounts(
    base_amount: Decimal,
    discounts: list[OrgDiscountConfig],
    stop_tier_id: str | None,
    order_count: int,
    today: date,
) -> tuple[Decimal, list[dict]]:
    """Apply per-stop discounts.

    Only discounts whose ``service_tier_id`` matches the stop's tier are considered.
    The cumulative discount is capped at ``base_amount`` so the stop never goes negative.
    """
    applied: list[dict] = []
    total = _ZERO
    for d in discounts:
        if not _discount_active(d, today):
            continue
        if stop_tier_id is not None and str(d.service_tier_id or "") != str(stop_tier_id):
            continue
        if d.discount_type == DiscountType.FIXED_PER_BOOKING:
            amount = _q(Decimal(str(d.value or "0")))
            if amount <= _ZERO:
                continue
            total += amount
            applied.append({
                "type": DiscountType.FIXED_PER_BOOKING.value,
                "service_tier_id": d.service_tier_id,
                "value": _s(Decimal(str(d.value or "0"))),
                "amount": _s(amount),
            })
        elif d.discount_type == DiscountType.PERCENTAGE:
            pct = Decimal(str(d.value or "0"))
            if pct <= _ZERO:
                continue
            amount = _q(base_amount * pct / Decimal("100"))
            if amount <= _ZERO:
                continue
            total += amount
            applied.append({
                "type": DiscountType.PERCENTAGE.value,
                "service_tier_id": d.service_tier_id,
                "value": _s(pct),
                "amount": _s(amount),
            })
        elif d.discount_type == DiscountType.VOLUME_TIERED:
            pct = _volume_tier_pct(d.volume_tiers, order_count)
            if pct <= _ZERO:
                continue
            amount = _q(base_amount * pct / Decimal("100"))
            if amount <= _ZERO:
                continue
            total += amount
            applied.append({
                "type": DiscountType.VOLUME_TIERED.value,
                "service_tier_id": d.service_tier_id,
                "value": _s(pct),
                "amount": _s(amount),
                "order_count": order_count,
            })

    if total > base_amount:
        total = base_amount
    return _q(total), applied


def _reapply_snapshot_discounts(
    base_amount: Decimal,
    snapshot_discounts: list[dict] | None,
) -> tuple[Decimal, list[dict]]:
    """Re-apply discounts using the stop's original snapshot.

    FIXED_PER_BOOKING keeps its absolute value unchanged. PERCENTAGE and
    VOLUME_TIERED keep their original percentage and are re-multiplied against
    the new base — preserving the discount rules that applied at order time.
    """
    applied: list[dict] = []
    total = _ZERO
    for entry in snapshot_discounts or []:
        if not isinstance(entry, dict):
            continue
        dtype = entry.get("type")
        if dtype == DiscountType.FIXED_PER_BOOKING.value:
            amount = _q(Decimal(str(entry.get("amount") or entry.get("value") or "0")))
            if amount <= _ZERO:
                continue
            total += amount
            applied.append({
                "type": dtype,
                "service_tier_id": entry.get("service_tier_id"),
                "value": _s(Decimal(str(entry.get("value") or "0"))),
                "amount": _s(amount),
            })
        elif dtype in (DiscountType.PERCENTAGE.value, DiscountType.VOLUME_TIERED.value):
            pct = Decimal(str(entry.get("value") or "0"))
            if pct <= _ZERO:
                continue
            amount = _q(base_amount * pct / Decimal("100"))
            if amount <= _ZERO:
                continue
            total += amount
            out = {
                "type": dtype,
                "service_tier_id": entry.get("service_tier_id"),
                "value": _s(pct),
                "amount": _s(amount),
            }
            if dtype == DiscountType.VOLUME_TIERED.value and entry.get("order_count") is not None:
                out["order_count"] = entry["order_count"]
            applied.append(out)
    if total > base_amount:
        total = base_amount
    return _q(total), applied


def _build_stop_breakdown(
    *,
    stop: StopInput,
    plan_snapshot: dict,
    base_price: Decimal,
    package_entries: list[dict],
    packages_subtotal: Decimal,
    service_tier_display: str | None,
    discount_entries: list[dict],
    total_discount: Decimal,
    pre_discount_subtotal: Decimal,
    subtotal_after_discount: Decimal,
    min_charge: Decimal,
    min_charge_applied: bool,
    subtotal: Decimal,
    vat_rate_name: str,
    vat_pct: Decimal,
    vat_amount: Decimal,
    total: Decimal,
) -> dict:
    return {
        "id": stop.stop_uuid,
        "tracking_id": stop.tracking_id,
        "stop_index": stop.index,
        "service_tier": service_tier_display,
        "service_tier_id": plan_snapshot.get("id_price_tier"),
        "pricing_plan": plan_snapshot,
        "base_price": _s(base_price),
        "packages": package_entries,
        "packages_count": len(package_entries),
        "packages_subtotal": _s(packages_subtotal),
        "pre_discount_subtotal": _s(pre_discount_subtotal),
        "discounts": discount_entries,
        "total_discount": _s(total_discount),
        "subtotal_after_discount": _s(subtotal_after_discount),
        "min_charge": _s(min_charge),
        "min_charge_applied": min_charge_applied,
        "subtotal": _s(subtotal),
        "vat_rate": vat_rate_name,
        "vat_rate_pct": _s(vat_pct),
        "vat_amount": _s(vat_amount),
        "total": _s(total),
    }


def _price_stop_packages(
    stop: StopInput,
    per_package_price: Decimal,
    per_kg_price: Decimal,
) -> tuple[list[dict], Decimal]:
    package_entries: list[dict] = []
    packages_subtotal = _ZERO
    for pkg in stop.packages:
        pkg_bd = _package_breakdown(pkg, per_package_price, per_kg_price)
        pkg.price_breakdown = pkg_bd
        packages_subtotal += Decimal(pkg_bd["total"])
        package_entries.append(pkg_bd)
    return package_entries, _q(packages_subtotal)


def _apply_min_charge(subtotal: Decimal, min_charge_raw: Any) -> tuple[Decimal, Decimal, bool]:
    min_charge = _ZERO
    if min_charge_raw is not None:
        min_charge = _q(Decimal(str(min_charge_raw)))
    if min_charge > _ZERO and subtotal < min_charge:
        return min_charge, min_charge, True
    return subtotal, min_charge, False


def compute_price_breakdown(
    *,
    ctx: PricingContext,
    stops: list[StopInput],
    order_uuid: str | None = None,
    order_id: str | None = None,
) -> PricingResult:
    """Compute pricing per delivery stop, then aggregate to the order.

    Discounts, the organisation's minimum-charge-per-booking, and VAT are all
    evaluated on each stop independently. The order-level totals are pure sums
    of the per-stop totals.
    """
    today = date.today()
    pc = ctx.payment_config
    vat_rate_name = pc.vat_rate.value if pc.vat_rate is not None else VatRate.STANDARD_20.value
    vat_pct = _VAT_RATE_PCT.get(vat_rate_name, Decimal("20.00"))
    min_charge_raw = ctx.org.min_charge_per_booking

    stops_breakdown: list[dict] = []
    order_subtotal = _ZERO
    order_vat = _ZERO
    order_total = _ZERO

    for stop in stops:
        plan = resolve_plan_for_stop(stop, ctx.org)
        stop.resolved_plan = plan
        plan_snapshot = _snapshot_plan(plan)
        base_price, per_package_price, per_kg_price = _tier_prices(plan)

        package_entries, packages_subtotal = _price_stop_packages(stop, per_package_price, per_kg_price)
        pre_discount_subtotal = _q(base_price + packages_subtotal)

        total_discount, discount_entries = _apply_discounts(
            pre_discount_subtotal,
            ctx.discounts,
            str(plan.get("id_price_tier") or "") or None,
            ctx.order_count,
            today,
        )
        subtotal_after_discount = _q(pre_discount_subtotal - total_discount)
        subtotal, min_charge, min_charge_applied = _apply_min_charge(subtotal_after_discount, min_charge_raw)

        vat_amount = _q(subtotal * vat_pct / Decimal("100"))
        total = _q(subtotal + vat_amount)

        service_tier_display = plan_display_name(plan) or stop.service_tier_name
        stop_bd = _build_stop_breakdown(
            stop=stop,
            plan_snapshot=plan_snapshot,
            base_price=base_price,
            package_entries=package_entries,
            packages_subtotal=packages_subtotal,
            service_tier_display=service_tier_display,
            discount_entries=discount_entries,
            total_discount=total_discount,
            pre_discount_subtotal=pre_discount_subtotal,
            subtotal_after_discount=subtotal_after_discount,
            min_charge=min_charge,
            min_charge_applied=min_charge_applied,
            subtotal=subtotal,
            vat_rate_name=vat_rate_name,
            vat_pct=vat_pct,
            vat_amount=vat_amount,
            total=total,
        )
        stop.price_breakdown = stop_bd
        stops_breakdown.append(stop_bd)

        order_subtotal += subtotal
        order_vat += vat_amount
        order_total += total

    order_subtotal = _q(order_subtotal)
    order_vat = _q(order_vat)
    order_total = _q(order_total)

    order_breakdown = {
        "id": order_uuid,
        "order_id": order_id,
        "currency": "GBP",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "stops": stops_breakdown,
        "packages_count": sum(s["packages_count"] for s in stops_breakdown),
        "subtotal": _s(order_subtotal),
        "vat_amount": _s(order_vat),
        "total": _s(order_total),
    }

    return PricingResult(
        subtotal=order_subtotal,
        vat_amount=order_vat,
        total_amount=order_total,
        breakdown=order_breakdown,
    )


def recompute_price_breakdown_from_snapshot(
    *,
    order_snapshot: dict,
    stops: list[StopInput],
    order_uuid: str | None = None,
    order_id: str | None = None,
) -> PricingResult:
    """Recompute pricing per stop using each stop's own discount/VAT snapshot.

    Each stop's stored ``price_breakdown`` carries its pricing_plan, discounts,
    vat_rate and vat_rate_pct. Per-package math is redone with new
    dimensions/weights; tier rates, discount rules, VAT rate and min-charge
    decision are preserved from the original snapshot so recompute never
    changes the pricing rules that applied at order time.
    """
    stops_breakdown: list[dict] = []
    order_subtotal = _ZERO
    order_vat = _ZERO
    order_total = _ZERO

    order_vat_rate_fallback = order_snapshot.get("vat_rate") or VatRate.STANDARD_20.value

    for stop in stops:
        plan = stop.resolved_plan
        if not plan:
            raise ValidationError(
                f"Stop {stop.index}: missing pricing snapshot — cannot recompute without the stored pricing_plan"
            )
        stop_snapshot = stop.price_breakdown or {}
        plan_snapshot = _snapshot_plan(plan)
        if plan.get("tier_name_at_order_time") and not plan_snapshot.get("tier_name_at_order_time"):
            plan_snapshot["tier_name_at_order_time"] = plan.get("tier_name_at_order_time")
        base_price, per_package_price, per_kg_price = _tier_prices(plan)

        package_entries, packages_subtotal = _price_stop_packages(stop, per_package_price, per_kg_price)
        pre_discount_subtotal = _q(base_price + packages_subtotal)

        total_discount, discount_entries = _reapply_snapshot_discounts(
            pre_discount_subtotal, stop_snapshot.get("discounts")
        )
        subtotal_after_discount = _q(pre_discount_subtotal - total_discount)

        min_charge = _q(Decimal(str(stop_snapshot.get("min_charge") or "0")))
        min_charge_applied = bool(stop_snapshot.get("min_charge_applied"))
        if min_charge > _ZERO and subtotal_after_discount < min_charge:
            subtotal = min_charge
            min_charge_applied = True
        else:
            subtotal = subtotal_after_discount

        vat_rate_name = stop_snapshot.get("vat_rate") or order_vat_rate_fallback
        vat_pct = Decimal(str(stop_snapshot.get("vat_rate_pct") or _VAT_RATE_PCT.get(vat_rate_name, Decimal("20.00"))))
        vat_amount = _q(subtotal * vat_pct / Decimal("100"))
        total = _q(subtotal + vat_amount)

        service_tier_display = plan.get("plain_name") or plan.get("tier_name_at_order_time") or stop.service_tier_name
        stop_bd = _build_stop_breakdown(
            stop=stop,
            plan_snapshot=plan_snapshot,
            base_price=base_price,
            package_entries=package_entries,
            packages_subtotal=packages_subtotal,
            service_tier_display=service_tier_display,
            discount_entries=discount_entries,
            total_discount=total_discount,
            pre_discount_subtotal=pre_discount_subtotal,
            subtotal_after_discount=subtotal_after_discount,
            min_charge=min_charge,
            min_charge_applied=min_charge_applied,
            subtotal=subtotal,
            vat_rate_name=vat_rate_name,
            vat_pct=vat_pct,
            vat_amount=vat_amount,
            total=total,
        )
        stop.price_breakdown = stop_bd
        stops_breakdown.append(stop_bd)

        order_subtotal += subtotal
        order_vat += vat_amount
        order_total += total

    order_subtotal = _q(order_subtotal)
    order_vat = _q(order_vat)
    order_total = _q(order_total)

    order_breakdown = {
        "id": order_uuid,
        "order_id": order_id,
        "currency": order_snapshot.get("currency") or "GBP",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "stops": stops_breakdown,
        "packages_count": sum(s["packages_count"] for s in stops_breakdown),
        "subtotal": _s(order_subtotal),
        "vat_amount": _s(order_vat),
        "total": _s(order_total),
    }

    return PricingResult(
        subtotal=order_subtotal,
        vat_amount=order_vat,
        total_amount=order_total,
        breakdown=order_breakdown,
    )
