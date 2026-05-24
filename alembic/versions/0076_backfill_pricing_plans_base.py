"""backfill_pricing_plans_base_price

For every organisation that has a pricing_plans JSON column, look up the
price_per_package of each plan's referenced service_tier and inject it as
base_price.  Plans that already have base_price are left untouched.
Plans whose id_price_tier no longer exists in service_tier are skipped.

Revision ID: 0076_backfill_pricing_plans_base
Revises: 0075_org_contract_title_expiry
Create Date: 2026-04-17 00:00:00.000000
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0076_backfill_pricing_plans_base"
down_revision: Union[str, None] = "0075_org_contract_title_expiry"
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    orgs = conn.execute(
        sa.text(
            "SELECT id, pricing_plans FROM organizations WHERE pricing_plans IS NOT NULL"
        )
    ).fetchall()

    for org_id, plans in orgs:
        # Skip nulls, non-lists (scalars/objects), and empty arrays
        if not isinstance(plans, list) or not plans:
            continue

        updated = False
        new_plans = []

        for plan in plans:
            if plan.get("base_price") is not None:
                new_plans.append(plan)
                continue

            tier_id = plan.get("id_price_tier")
            if not tier_id:
                new_plans.append(plan)
                continue

            row = conn.execute(
                sa.text("SELECT price_per_package FROM service_tier WHERE id = :tid"),
                {"tid": tier_id},
            ).fetchone()

            if row is None:
                plan["base_price"] = plan.get("price_per_package")
            else:
                plan["base_price"] = str(row[0])

            new_plans.append(plan)
            updated = True

        if updated:
            conn.execute(
                sa.text("UPDATE organizations SET pricing_plans = :plans WHERE id = :oid"),
                {"plans": json.dumps(new_plans), "oid": org_id},
            )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE organizations
            SET pricing_plans = (
                SELECT jsonb_agg(plan - 'base_price')
                FROM jsonb_array_elements(pricing_plans::jsonb) AS plan
            )
            WHERE id IN (
                SELECT id FROM (
                    SELECT id, pricing_plans
                    FROM organizations
                    WHERE pricing_plans IS NOT NULL
                      AND jsonb_typeof(pricing_plans::jsonb) = 'array'
                ) sub
                WHERE jsonb_array_length(pricing_plans::jsonb) > 0
            )
            """
        )
    )
