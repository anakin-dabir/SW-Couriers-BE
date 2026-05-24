from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.modules.delivery_attempts.models import DeliveryAttemptConfig


class DeliveryAttemptConfigRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DeliveryAttemptConfig)

    async def get_singleton(self) -> DeliveryAttemptConfig | None:
        """Return the single global config row, or None if not yet seeded."""
        result = await self.session.execute(
            select(DeliveryAttemptConfig).limit(1)
        )
        return result.scalars().first()
