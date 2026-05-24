"""Auth repository — data-access layer for RefreshToken model.

Handles CRUD for refresh tokens (hashed storage, revocation, cleanup).
No business logic — that lives in AuthService.
"""

# pyright: reportAttributeAccessIssue=false

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlalchemy.sql import func

from app.core.config import settings
from app.modules.auth.enums import ActivationLinkRequestStatus
from app.modules.auth.models import ActivationLinkRequest, Invite, RefreshToken, Session
from app.modules.user.models import User


class RefreshTokenRepository:
    """Repository for refresh token management."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Create ───────────────────────────────────

    async def create(
        self,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        access_jti: str | None = None,
        user_agent: str | None = None,
        ip_address: str | None = None,
        session_id: str | None = None,
    ) -> RefreshToken:
        """Store a new hashed refresh token with its paired access JTI."""
        record = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            access_jti=access_jti,
            user_agent=user_agent,
            ip_address=ip_address,
            session_id=session_id,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    # ── Read ─────────────────────────────────────

    async def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Find a refresh token by its SHA-256 hash."""
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_active_by_access_jti(self, user_id: str, access_jti: str) -> RefreshToken | None:
        """Find an active refresh token using its paired access token JTI."""
        now = datetime.now(UTC)
        stmt = (
            select(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.access_jti == access_jti,
                RefreshToken.revoked.is_(False),
                RefreshToken.expires_at > now,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_active_for_user(self, user_id: str) -> int:
        """Count non-revoked, non-expired refresh tokens for a user."""
        stmt = (
            select(func.count())
            .select_from(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked.is_(False),
                RefreshToken.expires_at > datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one() or 0

    async def revoke_oldest_keeping(self, user_id: str, keep_count: int) -> int:
        """Revoke oldest (by created_at) active tokens for user, keeping at most keep_count.

        Uses a two-step approach (SELECT then UPDATE) to avoid fragile subquery
        with OFFSET inside UPDATE which may not be respected by all planners.
        Returns the number of tokens revoked.
        """
        now = datetime.now(UTC)
        id_stmt = (
            select(RefreshToken.id)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked.is_(False),
                RefreshToken.expires_at > now,
            )
            .order_by(RefreshToken.created_at.desc())
        )
        result = await self.session.execute(id_stmt)
        all_ids = [row[0] for row in result.fetchall()]

        ids_to_revoke = all_ids[keep_count:] if len(all_ids) > keep_count else []
        if not ids_to_revoke:
            return 0

        revoke_stmt = update(RefreshToken).where(RefreshToken.id.in_(ids_to_revoke)).values(revoked=True, revoked_at=now)
        result = await self.session.execute(revoke_stmt)
        await self.session.flush()
        return result.rowcount

    async def get_active_access_jtis(self, user_id: str) -> list[str]:
        """Return all access JTIs paired with active (non-revoked, non-expired) refresh tokens."""
        stmt = select(RefreshToken.access_jti).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked.is_(False),
            RefreshToken.expires_at > datetime.now(UTC),
            RefreshToken.access_jti.isnot(None),
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.fetchall()]

    async def get_active_access_jtis_for_sessions(self, session_ids: list[str]) -> list[str]:
        """Return access JTIs paired with active refresh tokens belonging to the given sessions."""
        if not session_ids:
            return []
        now = datetime.now(UTC)
        stmt = select(RefreshToken.access_jti).where(
            RefreshToken.session_id.in_(session_ids),
            RefreshToken.revoked.is_(False),
            RefreshToken.expires_at > now,
            RefreshToken.access_jti.isnot(None),
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.fetchall()]

    # ── Revoke ───────────────────────────────────

    async def revoke(self, token_hash: str) -> None:
        """Revoke a single refresh token by hash."""
        stmt = update(RefreshToken).where(RefreshToken.token_hash == token_hash).values(revoked=True, revoked_at=datetime.now(UTC))
        await self.session.execute(stmt)
        await self.session.flush()

    async def revoke_if_active(self, token_hash: str) -> bool:
        """Atomically revoke a refresh token only if it is currently active.

        Returns True if this call flipped the token from active -> revoked.
        """
        now = datetime.now(UTC)
        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked.is_(False),
                RefreshToken.expires_at > now,
            )
            .values(revoked=True, revoked_at=now)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0

    async def attach_session_id_if_missing(self, token_hash: str, session_id: str) -> None:
        """Attach a logical session_id to an existing refresh row (backcompat migration).

        Used when the refresh token was issued before `session_id` existed.
        """
        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.session_id.is_(None),
            )
            .values(session_id=session_id)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Revoke all active refresh tokens for a user (logout everywhere).

        Returns the number of tokens revoked.
        """
        now = datetime.now(UTC)
        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked.is_(False),
            )
            .values(revoked=True, revoked_at=now)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount

    async def revoke_refresh_tokens_for_sessions(self, session_ids: list[str]) -> int:
        """Revoke all active refresh tokens for the given sessions.

        Returns number of refresh-token rows revoked.
        """
        if not session_ids:
            return 0
        now = datetime.now(UTC)
        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.session_id.in_(session_ids),
                RefreshToken.revoked.is_(False),
                RefreshToken.expires_at > now,
            )
            .values(revoked=True, revoked_at=now)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount

    # ── Cleanup ──────────────────────────────────

    async def delete_expired(self) -> int:
        """Hard-delete expired tokens. Run periodically via Arq task.

        Returns the number of tokens deleted.
        """
        stmt = delete(RefreshToken).where(
            RefreshToken.expires_at < datetime.now(UTC),
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount


class SessionRepository:
    """Repository for logical device sessions (sessions table)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_session(
        self,
        *,
        user_id: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> Session:
        now = datetime.now(UTC)
        inactivity_expires_at_dt = now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)

        record = Session(
            user_id=user_id,
            user_agent=user_agent,
            ip_address=ip_address,
            last_seen_at=now,
            inactivity_expires_at=inactivity_expires_at_dt,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def touch_session(
        self,
        *,
        session_id: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> None:
        now = datetime.now(UTC)
        inactivity_expires_at_dt = now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        stmt = (
            update(Session)
            .where(Session.session_id == session_id)
            .values(
                last_seen_at=now,
                inactivity_expires_at=inactivity_expires_at_dt,
                user_agent=user_agent,
                ip_address=ip_address,
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def list_active_sessions(
        self,
        *,
        user_id: str,
    ) -> list[Session]:
        now = datetime.now(UTC)
        rt = RefreshToken
        sess = Session
        has_active_refresh = exists(
            select(1).where(
                rt.user_id == user_id,
                rt.session_id == sess.session_id,
                rt.revoked.is_(False),
                rt.expires_at > now,
                rt.session_id.isnot(None),
            )
        )
        stmt = (
            select(sess)
            .where(
                sess.user_id == user_id,
                sess.revoked.is_(False),
                has_active_refresh,
            )
            .order_by(sess.last_seen_at.desc().nulls_last())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def session_is_active_for_user(self, user_id: str, session_id: str) -> bool:
        sid = str(session_id).strip()
        stmt = (
            select(Session.session_id)
            .where(
                Session.user_id == user_id,
                Session.session_id == sid,
                Session.revoked.is_(False),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def revoke_session(self, *, user_id: str, session_id: str) -> int:
        sid = str(session_id).strip()
        now = datetime.now(UTC)
        stmt = (
            update(Session)
            .where(Session.user_id == user_id, Session.session_id == sid, Session.revoked.is_(False))
            .values(revoked=True, revoked_at=now)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(result.rowcount or 0)

    async def revoke_sessions_except(self, *, user_id: str, keep_session_id: str) -> list[str]:
        """Revoke all sessions for user except `keep_session_id`. Returns revoked session ids."""
        keep = str(keep_session_id).strip()
        now = datetime.now(UTC)
        # Fetch ids first (small cardinality, max ~3 active sessions).
        id_stmt = select(Session.session_id).where(
            Session.user_id == user_id,
            Session.revoked.is_(False),
            Session.session_id != keep,
        )
        result = await self.session.execute(id_stmt)
        revoked_ids = [row[0] for row in result.fetchall()]
        if not revoked_ids:
            return []

        stmt = (
            update(Session)
            .where(Session.user_id == user_id, Session.session_id.in_(revoked_ids))
            .values(revoked=True, revoked_at=now)
        )
        await self.session.execute(stmt)
        await self.session.flush()
        return revoked_ids


# ── Invites ──────────────────────────────────────


class InviteRepository:
    """Repository for user invite records (flow B: invite = link for existing user). Token stored as SHA-256 hash."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        invited_by_user_id: str | None = None,
    ) -> Invite:
        """Store a new invite for an existing user."""
        record = Invite(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            invited_by_user_id=invited_by_user_id,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def find_latest_invite_id_for_user(self, user_id: str) -> str | None:
        stmt = select(Invite.id).where(Invite.user_id == user_id).order_by(Invite.created_at.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def invalidate_pending_invites_for_user(self, user_id: str) -> None:
        """Mark all unused invites for this user as used so old links stop working."""
        now = datetime.now(UTC)
        stmt = (
            update(Invite)
            .where(Invite.user_id == user_id, Invite.used_at.is_(None))
            .values(used_at=now)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def find_by_token_hash_with_user(self, token_hash: str) -> Invite | None:
        """Lookup invite by token hash with user loaded (any expiry / used state — caller classifies)."""
        stmt = (
            select(Invite)
            .join(User, Invite.user_id == User.id)
            .options(joinedload(Invite.user))
            .where(Invite.token_hash == token_hash)
        )
        result = await self.session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def find_pending_by_token_hash(self, token_hash: str) -> Invite | None:
        """Find a non-used, non-expired invite by token hash, with user loaded via JOIN (one query).

        Invites for users who are already email-verified are excluded so stale rows
        cannot continue the activation workflow after account activation.
        """
        now = datetime.now(UTC)
        stmt = (
            select(Invite)
            .join(Invite.user)
            .options(joinedload(Invite.user))
            .where(
                Invite.token_hash == token_hash,
                Invite.used_at.is_(None),
                Invite.expires_at > now,
                User.email_verified.is_(False),
            )
        )
        result = await self.session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def mark_used(self, invite_id: str) -> bool:
        """Atomically mark invite as used. Returns True only if this call claimed it.

        Uses WHERE used_at IS NULL to guarantee exactly-once semantics under
        concurrent requests — the second caller sees rowcount=0 and knows it lost.
        """
        stmt = update(Invite).where(Invite.id == invite_id, Invite.used_at.is_(None)).values(used_at=datetime.now(UTC))
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0


    async def mark_used_and_invalidate_sibling_invites(self, invite_id: str, user_id: str) -> bool:
        """Claim ``invite_id`` if still unused, then mark every other pending invite for ``user_id`` used.

        Prevents previously issued invite links from remaining valid after successful activation.
        """
        now = datetime.now(UTC)
        claim = (
            update(Invite)
            .where(Invite.id == invite_id, Invite.user_id == user_id, Invite.used_at.is_(None))
            .values(used_at=now)
        )
        result = await self.session.execute(claim)
        if not result.rowcount:
            await self.session.flush()
            return False

        invalidate = (
            update(Invite)
            .where(
                Invite.user_id == user_id,
                Invite.used_at.is_(None),
            )
            .values(used_at=now)
        )
        await self.session.execute(invalidate)
        await self.session.flush()
        return True

    async def update_email_status(
        self,
        invite_id: str,
        status: str,
        email_sent_at: datetime | None = None,
        email_last_error: str | None = None,
    ) -> None:
        """Update invite email delivery status (called by Arq worker after send or on failure)."""
        values: dict = {"email_status": status}
        if email_sent_at is not None:
            values["email_sent_at"] = email_sent_at
        if email_last_error is not None:
            values["email_last_error"] = email_last_error
        stmt = update(Invite).where(Invite.id == invite_id).values(**values)
        await self.session.execute(stmt)
        await self.session.flush()


class ActivationLinkRequestRepository:
    """Repository for the shared work item behind fan-out admin reminder notifications."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_pending_for_user(self, requester_user_id: str) -> ActivationLinkRequest | None:
        stmt = (
            select(ActivationLinkRequest)
            .where(
                ActivationLinkRequest.requester_user_id == requester_user_id,
                ActivationLinkRequest.status == ActivationLinkRequestStatus.PENDING,
            )
            .order_by(ActivationLinkRequest.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create_pending(self, requester_user_id: str) -> tuple[ActivationLinkRequest, bool]:
        stmt = (
            pg_insert(ActivationLinkRequest)
            .values(requester_user_id=requester_user_id, status=ActivationLinkRequestStatus.PENDING)
            .on_conflict_do_nothing(
                index_elements=[ActivationLinkRequest.requester_user_id],
                index_where=text("status = 'PENDING'"),
            )
            .returning(ActivationLinkRequest)
        )
        result = await self.session.execute(stmt)
        created = result.scalar_one_or_none()
        await self.session.flush()
        if created is not None:
            return created, True
        existing = await self.get_pending_for_user(requester_user_id)
        if existing is None:
            raise RuntimeError("activation link request conflict without pending row")
        return existing, False

    async def get_by_id_for_update(self, request_id: str) -> ActivationLinkRequest | None:
        stmt = (
            select(ActivationLinkRequest)
            .where(ActivationLinkRequest.id == request_id)
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_ids(self, request_ids: list[str]) -> dict[str, ActivationLinkRequest]:
        if not request_ids:
            return {}
        stmt = select(ActivationLinkRequest).where(ActivationLinkRequest.id.in_(request_ids))
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        return {row.id: row for row in rows}

    async def resolve_pending_by_id(
        self,
        request_id: str,
        *,
        resolved_by_user_id: str | None,
        resolved_invite_id: str,
    ) -> bool:
        now = datetime.now(UTC)
        stmt = (
            update(ActivationLinkRequest)
            .where(
                ActivationLinkRequest.id == request_id,
                ActivationLinkRequest.status == ActivationLinkRequestStatus.PENDING,
            )
            .values(
                status=ActivationLinkRequestStatus.RESOLVED,
                resolved_by_user_id=resolved_by_user_id,
                resolved_invite_id=resolved_invite_id,
                resolved_at=now,
                updated_at=now,
            )
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0
