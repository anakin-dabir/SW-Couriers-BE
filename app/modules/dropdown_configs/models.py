from __future__ import annotations

from sqlalchemy import Enum, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModelNoVersion
from app.modules.dropdown_configs.enums import DropdownConfigKey


class DropdownValue(BaseModelNoVersion):
    __tablename__ = "dropdown_values"

    dropdown_key: Mapped[DropdownConfigKey] = mapped_column(
        Enum(DropdownConfigKey, native_enum=False),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    color_hex: Mapped[str | None] = mapped_column(String(9), nullable=True)

    __table_args__ = (UniqueConstraint("dropdown_key", "code", name="uq_dropdown_values_key_code"),)
