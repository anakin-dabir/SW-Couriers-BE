"""Client inactivity ORM model — global singleton for B2B inactivity policy."""

from sqlalchemy import Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel


class ClientInactivityConfig(BaseModel):
    """Global admin-managed config for automatic B2B client user inactivity.

    Singleton table — exactly one row is maintained.
    """

    __tablename__ = "client_inactivity_configs"

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    inactive_after_days: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default="60")

    def __repr__(self) -> str:
        return f"<ClientInactivityConfig enabled={self.enabled} days={self.inactive_after_days}>"
