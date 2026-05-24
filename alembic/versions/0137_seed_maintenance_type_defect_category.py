"""Seed dropdown_values for MAINTENANCE_TYPE and DEFECT_CATEGORY.

Revision ID: 0137_seed_dropdown_configs
Revises: 0136_vehicle_dropdown_strings
Create Date: 2026-05-15

"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0137_seed_dropdown_configs"
down_revision: Union[str, None] = "0136_vehicle_dropdown_strings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFECT_CATEGORY: list[tuple[str, str, str]] = [
    ("ROUTINE_SERVICE", "Routine Service", "#FF9800"),
    ("TYRES_AND_WHEELS", "Tyres & Wheels", "#2196F3"),
    ("PART_REPLACEMENT", "Part Replacement", "#F44336"),
    ("BREAKDOWN", "Breakdown", "#9C27B0"),
    ("CABIN_EQUIPMENT", "Cabin Equipment", "#E91E63"),
    ("LIGHTS_AND_INDICATORS", "Lights & Indicators", "#00BCD4"),
    ("BODY_DAMAGE", "Body Damage", "#4CAF50"),
    ("MIRROR_AND_GLASS", "Mirror & Glass", "#64B5F6"),
    ("SAFETY_EQUIPMENT", "Safety Equipment", "#FFCA28"),
    ("OTHER", "Other", "#AB47BC"),
]

_MAINTENANCE_TYPE: list[tuple[str, str, str]] = [
    ("ROUTINE_SERVICE", "Routine Service", "#FF9800"),
    ("TYRES_AND_WHEELS", "Tyres & Wheels", "#2196F3"),
    ("PART_REPLACEMENT", "Part Replacement", "#F44336"),
    ("BREAKDOWN", "Breakdown", "#9C27B0"),
    ("CABIN_EQUIPMENT", "Cabin Equipment", "#E91E63"),
    ("LIGHTS_AND_INDICATORS", "Lights & Indicators", "#00BCD4"),
    ("BODY_DAMAGE", "Body Damage", "#4CAF50"),
    ("MIRROR_AND_GLASS", "Mirror & Glass", "#64B5F6"),
    ("SAFETY_EQUIPMENT", "Safety Equipment", "#FFCA28"),
    ("OTHER", "Other", "#AB47BC"),
]

_SEED: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("DEFECT_CATEGORY", _DEFECT_CATEGORY),
    ("MAINTENANCE_TYPE", _MAINTENANCE_TYPE),
]


def upgrade() -> None:
    conn = op.get_bind()
    for dropdown_key, rows in _SEED:
        conn.execute(
            sa.text("DELETE FROM dropdown_values WHERE dropdown_key = :key"),
            {"key": dropdown_key},
        )
        for code, label, color_hex in rows:
            conn.execute(
                sa.text("INSERT INTO dropdown_values " "(id, dropdown_key, code, label, color_hex) " "VALUES (:id, :dropdown_key, :code, :label, :color_hex)"),
                {
                    "id": str(uuid.uuid4()),
                    "dropdown_key": dropdown_key,
                    "code": code,
                    "label": label,
                    "color_hex": color_hex,
                },
            )
    conn.execute(sa.text("DELETE FROM dropdown_values WHERE dropdown_key = 'FUEL_TYPE' AND code = 'HYBRID'"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO dropdown_values (id, dropdown_key, code, label, color_hex) "
            "VALUES (:id, 'FUEL_TYPE', 'HYBRID', 'Hybrid', '#D4E157') "
            "ON CONFLICT (dropdown_key, code) DO NOTHING"
        ),
        {"id": str(uuid.uuid4())},
    )
    for dropdown_key, rows in _SEED:
        codes = [code for code, _, _ in rows]
        conn.execute(
            sa.text("DELETE FROM dropdown_values " "WHERE dropdown_key = :dropdown_key AND code = ANY(:codes)"),
            {"dropdown_key": dropdown_key, "codes": codes},
        )
