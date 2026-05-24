"""Seed system-owned Superfast service tier and backfill organisation pricing.

Revision ID: 0151_seed_superfast_system_tier
Revises: 0150_order_drafts_total_amount
Create Date: 2026-05-22
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "0152_seed_superfast_system_tier"
down_revision: str | None = "0151_stop_attempt_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUPERFAST_TIER_NAME = "Superfast"
SUPERFAST_AVAILABLE_FOR = "BOTH"
SUPERFAST_DURATION_DAYS = 1
SUPERFAST_BASE_PRICE = Decimal("0.00")
SUPERFAST_PRICE_PER_PACKAGE = Decimal("125.00")
SUPERFAST_PRICE_PER_KG = Decimal("0.00")
SUPERFAST_ERROR_MARGIN_KG = 0


def _tier_list_price(*, base_price: Decimal, price_per_package: Decimal) -> str:
    return str((base_price + price_per_package).quantize(Decimal("0.01")))


def _superfast_plan_entry(*, tier_id: str) -> dict:
    """Shape matches PricingPlanEntry / fe_demo_lib._pricing_plan_from_tier."""
    list_price = _tier_list_price(
        base_price=SUPERFAST_BASE_PRICE,
        price_per_package=SUPERFAST_PRICE_PER_PACKAGE,
    )
    return {
        "id_price_tier": tier_id,
        "plain_name": SUPERFAST_TIER_NAME,
        "plain_type": "standard",
        "days": SUPERFAST_DURATION_DAYS,
        "base_price": list_price,
        "price_per_package": list_price,
        "price_per_kg": str(SUPERFAST_PRICE_PER_KG.quantize(Decimal("0.01"))),
        "permitted": True,
        "is_default": False,
        "selected": False,
    }


def _load_pricing_plans(raw: object | None) -> list[dict]:
    if not isinstance(raw, list):
        return []
    return [dict(p) for p in raw if isinstance(p, dict)]


def upgrade() -> None:
    conn = op.get_bind()

    tier_id = conn.execute(
        sa.text(
            """
            SELECT id::text
            FROM service_tier
            WHERE tier_name = :tier_name
              AND available_for = :available_for
              AND scope_type = 'GLOBAL'
              AND scope_org_id IS NULL
            LIMIT 1
            """
        ),
        {"tier_name": SUPERFAST_TIER_NAME, "available_for": SUPERFAST_AVAILABLE_FOR},
    ).scalar_one_or_none()

    if tier_id is None:
        tier_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                """
                INSERT INTO service_tier (
                    id,
                    tier_name,
                    description,
                    duration_days,
                    error_margin_kg,
                    price_per_kg,
                    price_per_package,
                    base_price,
                    scope_type,
                    scope_org_id,
                    available_for,
                    color,
                    icon,
                    status,
                    version,
                    created_at,
                    updated_at
                ) VALUES (
                    :tier_id,
                    :tier_name,
                    :description,
                    :duration_days,
                    :error_margin_kg,
                    :price_per_kg,
                    :price_per_package,
                    :base_price,
                    'GLOBAL',
                    NULL,
                    :available_for,
                    :color,
                    :icon,
                    'ACTIVE',
                    1,
                    now(),
                    now()
                )
                """
            ),
            {
                "tier_id": tier_id,
                "tier_name": SUPERFAST_TIER_NAME,
                "description": "Express delivery tier",
                "duration_days": SUPERFAST_DURATION_DAYS,
                "error_margin_kg": SUPERFAST_ERROR_MARGIN_KG,
                "price_per_kg": SUPERFAST_PRICE_PER_KG,
                "price_per_package": SUPERFAST_PRICE_PER_PACKAGE,
                "base_price": SUPERFAST_BASE_PRICE,
                "available_for": SUPERFAST_AVAILABLE_FOR,
                "color": "#E63946",
                "icon": "bolt",
            },
        )

    assert tier_id is not None

    org_rows = conn.execute(sa.text("SELECT id, pricing_plans FROM organizations")).fetchall()
    for org_id, pricing_plans_raw in org_rows:
        plans = _load_pricing_plans(pricing_plans_raw)
        has_superfast = any(str(p.get("id_price_tier") or "") == tier_id for p in plans)
        if not has_superfast:
            plans.append(_superfast_plan_entry(tier_id=tier_id))
        else:
            for plan in plans:
                if str(plan.get("id_price_tier") or "") == tier_id:
                    plan["permitted"] = True
                    plan.setdefault("plain_type", "standard")
                    plan.setdefault("plain_name", SUPERFAST_TIER_NAME)
                    plan.setdefault("days", SUPERFAST_DURATION_DAYS)
                    list_price = _tier_list_price(
                        base_price=SUPERFAST_BASE_PRICE,
                        price_per_package=SUPERFAST_PRICE_PER_PACKAGE,
                    )
                    plan.setdefault("base_price", list_price)
                    plan.setdefault("price_per_package", list_price)
                    plan.setdefault("price_per_kg", str(SUPERFAST_PRICE_PER_KG.quantize(Decimal("0.01"))))

        conn.execute(
            sa.text("UPDATE organizations SET pricing_plans = :plans WHERE id = :org_id"),
            {"org_id": org_id, "plans": json.dumps(plans)},
        )

        existing_line = conn.execute(
            sa.text(
                """
                SELECT id::text
                FROM org_service_tier_contract_lines
                WHERE organization_id = :org_id
                  AND global_template_id = :tier_id
                LIMIT 1
                """
            ),
            {"org_id": org_id, "tier_id": tier_id},
        ).scalar_one_or_none()

        if existing_line is None:
            max_sort = conn.execute(
                sa.text(
                    """
                    SELECT COALESCE(MAX(sort_order), -1)
                    FROM org_service_tier_contract_lines
                    WHERE organization_id = :org_id
                    """
                ),
                {"org_id": org_id},
            ).scalar_one()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO org_service_tier_contract_lines (
                        id,
                        organization_id,
                        global_template_id,
                        mode,
                        permitted,
                        is_default,
                        org_tier_id,
                        sort_order,
                        created_at,
                        updated_at,
                        version
                    ) VALUES (
                        :id,
                        :org_id,
                        :tier_id,
                        'standard',
                        true,
                        false,
                        NULL,
                        :sort_order,
                        now(),
                        now(),
                        1
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "org_id": org_id,
                    "tier_id": tier_id,
                    "sort_order": int(max_sort) + 1,
                },
            )
        else:
            conn.execute(
                sa.text(
                    """
                    UPDATE org_service_tier_contract_lines
                    SET permitted = true, updated_at = now()
                    WHERE organization_id = :org_id
                      AND global_template_id = :tier_id
                    """
                ),
                {"org_id": org_id, "tier_id": tier_id},
            )


def downgrade() -> None:
    conn = op.get_bind()
    tier_id = conn.execute(
        sa.text(
            """
            SELECT id::text
            FROM service_tier
            WHERE tier_name = :tier_name
              AND available_for = :available_for
              AND scope_type = 'GLOBAL'
              AND scope_org_id IS NULL
            LIMIT 1
            """
        ),
        {"tier_name": SUPERFAST_TIER_NAME, "available_for": SUPERFAST_AVAILABLE_FOR},
    ).scalar_one_or_none()
    if tier_id is None:
        return

    conn.execute(
        sa.text("DELETE FROM org_service_tier_contract_lines WHERE global_template_id = :tier_id"),
        {"tier_id": tier_id},
    )

    org_rows = conn.execute(sa.text("SELECT id, pricing_plans FROM organizations")).fetchall()
    for org_id, pricing_plans_raw in org_rows:
        plans = _load_pricing_plans(pricing_plans_raw)
        filtered = [p for p in plans if str(p.get("id_price_tier") or "") != tier_id]
        if len(filtered) != len(plans):
            conn.execute(
                sa.text("UPDATE organizations SET pricing_plans = :plans WHERE id = :org_id"),
                {"org_id": org_id, "plans": json.dumps(filtered)},
            )

    conn.execute(
        sa.text(
            """
            DELETE FROM service_tier
            WHERE id = :tier_id
              AND tier_name = :tier_name
              AND scope_type = 'GLOBAL'
            """
        ),
        {"tier_id": tier_id, "tier_name": SUPERFAST_TIER_NAME},
    )
