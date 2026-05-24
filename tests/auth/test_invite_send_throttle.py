from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import AuthUser
from app.common.enums import ClientType
from app.modules.auth.models import ActivationLinkRequest, Invite
from app.modules.auth.service import AuthService
from app.modules.notifications.enums import NotificationEvent, NotificationType
from app.modules.notifications.models import Notification
from app.modules.user.models import User


class _FakeRedisInviteDailyCap:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def ttl(self, key: str) -> int:
        return -2

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._data[key] = value
        return True

    async def incr(self, key: str) -> int:
        n = int(self._data.get(key, "0")) + 1
        self._data[key] = str(n)
        return n

    async def expire(self, key: str, ttl: int) -> bool:
        return True


def _auth_user(u: User) -> AuthUser:
    return AuthUser(
        id=u.id,
        role=str(u.role),
        client_type=ClientType.ADMIN,
        jti="test-jti-invite-throttle",
        organization_id=None,
    )


@pytest.mark.asyncio
async def test_create_invite_daily_cap_sixth_is_throttled_without_new_row(
    db_session: AsyncSession,
    user_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inviter = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)
    target = await user_factory(
        role="ADMIN",
        status="PENDING_VERIFICATION",
        email_verified=False,
        email="throttle-invitee@example.com",
    )
    fake = _FakeRedisInviteDailyCap()
    monkeypatch.setattr("app.modules.auth.service.get_redis", lambda: fake)

    svc = AuthService(db_session)
    au = _auth_user(inviter)

    for _ in range(5):
        r = await svc.create_invite(au, target.id)
        assert r.throttled is False
        assert r.invite is not None
        assert r.raw_token is not None
        assert r.public_invite_id == r.invite.id

    r6 = await svc.create_invite(au, target.id)
    assert r6.throttled is True
    assert r6.invite is None
    assert r6.raw_token is None
    assert r6.user.id == target.id
    assert r6.public_invite_id

    n = await db_session.scalar(select(func.count()).select_from(Invite).where(Invite.user_id == target.id))
    assert n == 5


@pytest.mark.asyncio
async def test_resend_invite_for_activation_link_request_resolves_shared_request(
    db_session: AsyncSession,
    user_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inviter = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)
    other_admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    target = await user_factory(
        role="ADMIN",
        status="PENDING_VERIFICATION",
        email_verified=False,
        email="activation-request-target@example.com",
    )
    unrelated_target = await user_factory(
        role="ADMIN",
        status="PENDING_VERIFICATION",
        email_verified=False,
        email="activation-request-other@example.com",
    )
    shared_request = ActivationLinkRequest(requester_user_id=target.id)
    db_session.add_all(
        [
            shared_request,
            Notification(
                recipient_id=inviter.id,
                organization_id=None,
                event=NotificationEvent.ADMIN_ACTIVATION_LINK_REQUESTED.value,
                notification_type=NotificationType.ADMIN_INTERNAL.value,
                subject=None,
                body="Target requested a new activation link.",
                context_json={"requester_user_id": target.id},
            ),
            Notification(
                recipient_id=other_admin.id,
                organization_id=None,
                event=NotificationEvent.ADMIN_ACTIVATION_LINK_REQUESTED.value,
                notification_type=NotificationType.ADMIN_INTERNAL.value,
                subject=None,
                body="Target requested a new activation link.",
                context_json={"requester_user_id": target.id},
            ),
            Notification(
                recipient_id=other_admin.id,
                organization_id=None,
                event=NotificationEvent.ADMIN_ACTIVATION_LINK_REQUESTED.value,
                notification_type=NotificationType.ADMIN_INTERNAL.value,
                subject=None,
                body="Someone else requested a new activation link.",
                context_json={"requester_user_id": unrelated_target.id},
            ),
        ]
    )
    await db_session.flush()

    fake = _FakeRedisInviteDailyCap()
    monkeypatch.setattr("app.modules.auth.service.get_redis", lambda: fake)

    result = await AuthService(db_session).resend_invite_for_activation_link_request(_auth_user(inviter), shared_request.id)

    notifications = list((await db_session.execute(select(Notification).order_by(Notification.created_at))).scalars().all())
    matching = [n for n in notifications if n.context_json["requester_user_id"] == target.id]
    unrelated = next(n for n in notifications if n.context_json["requester_user_id"] == unrelated_target.id)
    await db_session.refresh(shared_request)
    assert matching
    assert unrelated.read_at is None
    assert shared_request.status == "RESOLVED"
    assert shared_request.resolved_by_user_id == inviter.id
    assert shared_request.resolved_invite_id == result.public_invite_id
    assert shared_request.resolved_at is not None
    assert all(n.read_at is None for n in matching)
