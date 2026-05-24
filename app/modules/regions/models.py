"""Region ORM model. Regions define geographic boundaries using PostGIS polygons.

Dispatchers and depots are assigned to regions. RBAC Layer 3 scopes
dispatcher access by region_id.
"""

from geoalchemy2 import Geometry
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel


class Region(BaseModel):
    """Geographic region with PostGIS polygon boundary."""

    __tablename__ = "regions"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # PostGIS polygon boundary — SRID 4326 (WGS84, standard GPS)
    boundary = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326, spatial_index=True),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    def __repr__(self) -> str:
        return f"<Region {self.code}: {self.name}>"
