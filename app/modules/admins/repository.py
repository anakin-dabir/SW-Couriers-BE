from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.modules.admins.models import Admin


class AdminRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Admin)

    async def find_by_user_id(self, user_id: str) -> Admin | None:
        return await self.find_one(user_id=user_id)
