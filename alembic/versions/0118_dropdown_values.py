"""create dropdown_values for vehicle admin dropdowns (keys hardcoded in app).

One row per option; ``dropdown_key`` matches ``DropdownConfigKey`` in code.
``DEFECT_CATEGORY`` rows match ``DefectCategory`` labels/colors from the admin UI.
``VEHICLE_AVAILABILITY`` uses UI colors (green / red / amber). Other keys mirror enums with the shared palette.
If legacy ``dynamic_config_*`` tables exist (from an earlier draft migration), data is
copied into ``dropdown_values`` then legacy tables are dropped.

Parent: 0117_status_automation_rules.
"""

from __future__ import annotations

from collections.abc import Sequence
import uuid

import sqlalchemy as sa
from alembic import op

from app.modules.dropdown_configs.enums import DropdownConfigKey
from app.modules.vehicles.enums import FuelType, MaintenanceType, ServiceType

revision: str = "0118_dropdown_values"
down_revision: str | None = "0117_status_automation_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DROPDOWN_CONFIG_KEY = sa.Enum(DropdownConfigKey, name="dropdown_config_key", native_enum=False)

_COLOR_PALETTE: list[str] = [
    "#5C6BC0",
    "#26A69A",
    "#FFA726",
    "#7E57C2",
    "#EC407A",
    "#29B6F6",
    "#66BB6A",
    "#FF7043",
    "#AB47BC",
    "#26C6DA",
    "#8D6E63",
    "#42A5F5",
    "#D4E157",
    "#EF5350",
    "#5E35B1",
]


def _human_label(code: str) -> str:
    if code == "MOT":
        return "MOT"
    return " ".join(part.capitalize() for part in code.split("_"))


def _rows_with_palette(members: list[str]) -> list[tuple[str, str, str]]:
    return [
        (m, _human_label(m), _COLOR_PALETTE[i % len(_COLOR_PALETTE)])
        for i, m in enumerate(members)
    ]


_SEED: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "FUEL_TYPE",
        _rows_with_palette([e.value for e in FuelType]),
    ),
    (
        "DEFECT_CATEGORY",
        [
            ("ROUTINE_SERVICE", "Routine Service", "#FF9800"),
            ("TYRES", "Tyres & Wheels", "#2196F3"),
            ("PART_REPLACEMENT", "Part Replacement", "#F44336"),
            ("BREAKDOWN", "Breakdown", "#9C27B0"),
            ("CABIN_EQUIPMENT", "Cabin Equipment", "#E91E63"),
            ("LIGHTS_AND_INDICATORS", "Lights & Indicators", "#00BCD4"),
            ("BODY_DAMAGE", "Body Damage", "#4CAF50"),
            ("MIRROR_AND_GLASS", "Mirror & Glass", "#64B5F6"),
            ("SAFETY_EQUIPMENT", "Safety Equipment", "#FFCA28"),
            ("OTHER", "Other", "#AB47BC"),
        ],
    ),
    (
        "MAINTENANCE_TYPE",
        _rows_with_palette([e.value for e in MaintenanceType]),
    ),
    (
        "SERVICE_TYPE",
        _rows_with_palette([e.value for e in ServiceType]),
    ),
    (
        "VEHICLE_AVAILABILITY",
        [
            ("ACTIVE", "Active", "#4CAF50"),
            ("UNAVAILABLE", "Unavailable", "#F44336"),
            ("IN_MAINTENANCE", "In Maintenance", "#FF9800"),
        ],
    ),
]


def _create_dropdown_values_table() -> None:
    op.create_table(
        "dropdown_values",
        sa.Column("dropdown_key", _DROPDOWN_CONFIG_KEY, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("color_hex", sa.String(length=9), nullable=True),
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dropdown_key", "code", name="uq_dropdown_values_key_code"),
    )
    op.create_index(op.f("ix_dropdown_values_dropdown_key"), "dropdown_values", ["dropdown_key"], unique=False)


def _seed_fresh(conn) -> None:
    for dropdown_key, triples in _SEED:
        for code, label, color_hex in triples:
            conn.execute(
                sa.text(
                    "INSERT INTO dropdown_values "
                    "(id, dropdown_key, code, label, color_hex) "
                    "VALUES (:id, :dropdown_key, :code, :label, :color_hex)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "dropdown_key": dropdown_key,
                    "code": code,
                    "label": label,
                    "color_hex": color_hex,
                },
            )


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    names = set(insp.get_table_names())

    if "dropdown_values" in names:
        return

    if "dynamic_config_values" in names and "dynamic_config_definitions" in names:
        _create_dropdown_values_table()
        conn.execute(
            sa.text(
                "INSERT INTO dropdown_values ("
                "id, dropdown_key, code, label, color_hex, "
                "created_at, updated_at"
                ") SELECT v.id, d.key, v.code, v.label, v.color_hex, "
                "v.created_at, v.updated_at "
                "FROM dynamic_config_values v "
                "JOIN dynamic_config_definitions d ON d.id = v.definition_id"
            )
        )
        op.drop_table("dynamic_config_values")
        op.drop_table("dynamic_config_definitions")
        return

    _create_dropdown_values_table()
    _seed_fresh(conn)


def downgrade() -> None:
    op.drop_index(op.f("ix_dropdown_values_dropdown_key"), table_name="dropdown_values")
    op.drop_table("dropdown_values")
