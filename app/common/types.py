from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

from fastapi import APIRouter

if TYPE_CHECKING:
    from fastapi import Request

    from app.common.deps import AuthUser


class ModuleConfig(TypedDict):
    router: APIRouter
    prefix: str
    tags: list[str]


class VersionConfig(TypedDict):
    prefix: str
    modules: list[ModuleConfig]


VersionedModulesList = list[VersionConfig]


@dataclass(frozen=True, slots=True)
class AuditContext:
    """Request-scoped context for audit logging. Built in the route, passed to service methods."""

    user_id: str
    user_role: str
    ip_address: str | None = None
    user_agent: str | None = None
    # Logical device session id (carried via JWT 'sid' claim). Enables linking audit rows to a session.
    session_id: str | None = None
    # Stable id shared by all audit rows emitted from the same HTTP request. Sourced from
    # 'X-Request-ID' if provided by the client; otherwise generated server-side.
    correlation_id: str | None = None

    @classmethod
    def from_request(cls, user: AuthUser, request: Request) -> AuditContext:
        from uuid import uuid4

        from app.common.utils import get_client_ip

        correlation_id = request.headers.get("x-request-id") or str(uuid4())

        return cls(
            user_id=user.id,
            user_role=user.role,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            session_id=user.sid,
            correlation_id=correlation_id,
        )


@dataclass(frozen=True, slots=True)
class BulkUploadResult[T]:
    """Result of a bulk upload: per-index success and failure tracking."""

    succeeded: list[tuple[int, T]]
    failed: list[tuple[int, str]]

    @property
    def all_succeeded(self) -> bool:
        return len(self.failed) == 0

    @property
    def total_count(self) -> int:
        return len(self.succeeded) + len(self.failed)
