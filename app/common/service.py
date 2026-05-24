from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import SessionDep


class BaseService:
    """Base for all services. Standardises the constructor contract.

    Every service takes ``session`` (required) and ``request`` (optional).
    Sub-classes extract what they need from ``request`` in their own
    ``__init__``.

    Usage in routes::

        AuthServiceDep = Annotated[AuthService, Depends(AuthService.dep)]
    """

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        self._session = session
        self._request = request

    @classmethod
    def dep(cls, request: Request, session: SessionDep):
        """FastAPI dependency — resolves session + request and returns an instance."""
        return cls(session, request)
