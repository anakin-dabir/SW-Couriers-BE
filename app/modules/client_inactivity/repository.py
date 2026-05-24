from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import UserRole, UserStatus
from app.common.repository import BaseRepository
from app.modules.client_inactivity.models import ClientInactivityConfig
from app.modules.user.models import User


class ClientInactivityConfigRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ClientInactivityConfig)

    async def get_singleton(self) -> ClientInactivityConfig | None:
        result = await self.session.execute(select(ClientInactivityConfig).limit(1))
        return result.scalars().first()


class ClientInactivityUserRepository:
    """User queries for the inactivity policy job."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_b2b_inactivity_candidates(self, *, cutoff: datetime) -> list[User]:
        activity_at = func.coalesce(User.last_login, User.created_at)
        stmt = select(User).where(
            User.role == UserRole.CUSTOMER_B2B,
            User.status == UserStatus.ACTIVE,
            User.organization_id.is_not(None),
            activity_at < cutoff,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_inactive_for_inactivity(self, user_ids: list[str], *, inactivated_at: datetime, reason: str) -> int:
        if not user_ids:
            return 0
        stmt = (
            update(User)
            .where(User.id.in_(user_ids), User.status == UserStatus.ACTIVE)
            .values(
                status=UserStatus.INACTIVE,
                inactive_reason=reason,
                inactivated_at=inactivated_at,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount or 0

    async def reactivate_on_login(self, user_id: str, *, reason: str) -> bool:
        stmt = (
            update(User)
            .where(
                User.id == user_id,
                User.status == UserStatus.INACTIVE,
                User.inactive_reason == reason,
            )
            .values(
                status=UserStatus.ACTIVE,
                inactive_reason=None,
                inactivated_at=None,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return (result.rowcount or 0) > 0
