"""User repository — data-access layer for User model.

Queries only. Business logic lives in services. Data-level RBAC
scoping (Layer 3) is applied via scope_filters on list queries.
"""

# pyright: reportAttributeAccessIssue=false

from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.common.enums import UserRole, UserStatus
from app.common.repository import BaseRepository
from app.modules.user.models import User


class UserRepository(BaseRepository):
    """Repository for the User model."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, User)

    # ── Lookup helpers ───────────────────────────

    async def get_by_ids(self, ids: list[str]) -> dict[str, "User"]:
        """Fetch multiple users by ID in a single query. Returns a dict keyed by user ID."""
        if not ids:
            return {}
        stmt = select(User).where(User.id.in_(ids))
        result = await self.session.execute(stmt)
        return {u.id: u for u in result.scalars().all()}

    async def find_by_email(self, email: str) -> User | None:
        """Find a user by email (case-insensitive). Email is stored normalized at registration."""
        normalized = email.strip().lower()
        stmt = select(User).where(User.email == normalized)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        """Check if an email is already registered (case-insensitive)."""
        normalized = email.strip().lower()
        stmt = select(func.count()).select_from(User).where(User.email == normalized)
        result = await self.session.execute(stmt)
        return result.scalar_one() > 0

    # ── Lockout management ───────────────────────

    async def increment_failed_attempts(self, user_id: str) -> int:
        """Atomically increment failed_login_attempts by 1. Returns the new count."""
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(
                failed_login_attempts=User.failed_login_attempts + 1,
                updated_at=datetime.now(UTC),
            )
            .returning(User.failed_login_attempts)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one()

    async def lock_account(self, user_id: str, locked_until: datetime) -> None:
        """Lock a user account until the given timestamp (auto-lockout only).

        Does not change status — when locked_until expires, user can log in again.
        Admin suspension is a separate action (status=suspended).
        """
        stmt = update(User).where(User.id == user_id).values(locked_until=locked_until, updated_at=datetime.now(UTC))
        await self.session.execute(stmt)
        await self.session.flush()

    async def reset_failed_attempts(self, user_id: str) -> None:
        """Reset failed login attempts and clear lockout."""
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(
                failed_login_attempts=0,
                locked_until=None,
                updated_at=datetime.now(UTC),
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

    # ── Email verification ───────────────────────

    async def verify_email(self, user_id: str) -> None:
        """Mark a user's email as verified and activate the account."""
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(
                email_verified=True,
                status=UserStatus.ACTIVE,
                updated_at=datetime.now(UTC),
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

    # ── Login tracking ───────────────────────────

    async def update_last_login(self, user_id: str) -> None:
        """Stamp last_login = now() on every successful authentication."""
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(last_login=datetime.now(UTC), updated_at=datetime.now(UTC))
        )
        await self.session.execute(stmt)
        await self.session.flush()

    # ── Atomic password reset ────────────────────

    async def atomic_password_reset(
        self,
        user_id: str,
        new_hash: str,
        now: datetime,
        token_issued_at: datetime,
    ) -> bool:
        """Atomically set password only if it hasn't been changed since the token was issued.

        Prevents race conditions where two concurrent reset requests both succeed.
        Returns True if the row was actually updated (i.e. this caller won the race).
        """
        stmt = (
            update(User)
            .where(
                User.id == user_id,
                or_(
                    User.password_changed_at.is_(None),
                    User.password_changed_at <= token_issued_at,
                ),
            )
            .values(password_hash=new_hash, password_changed_at=now, updated_at=now)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0

    async def increment_session_sv(self, user_id: str) -> int:
        """Atomically increment session generation for logout-all race safety."""
        now = datetime.now(UTC)
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(session_sv=User.session_sv + 1, updated_at=now)
            .returning(User.session_sv)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(result.scalar_one())

    async def list_account_managers(
        self,
        *,
        search: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[User], int]:
        """Return paginated ADMIN + SUPER_ADMIN users, optionally filtered by name/email.

        Used to populate the account manager dropdown in the UI.
        """
        admin_roles = [UserRole.ADMIN, UserRole.SUPER_ADMIN]
        stmt = select(User).where(User.role.in_(admin_roles))
        count_stmt = select(func.count()).select_from(User).where(User.role.in_(admin_roles))

        if search:
            pattern = f"%{search}%"
            search_filter = or_(
                User.first_name.ilike(pattern),
                User.last_name.ilike(pattern),
                User.email.ilike(pattern),
            )
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        offset = (page - 1) * size
        stmt = stmt.order_by(User.first_name.asc(), User.last_name.asc()).offset(offset).limit(size)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total
