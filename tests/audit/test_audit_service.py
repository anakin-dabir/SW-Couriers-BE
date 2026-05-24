"""Unit tests for AuditService write behaviour."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.audit.service import AuditService


@pytest.mark.asyncio
async def test_log_reraises_on_persistence_failure() -> None:
    session = MagicMock()
    nested_cm = AsyncMock()
    nested_cm.__aenter__ = AsyncMock(return_value=None)
    nested_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=nested_cm)
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    session.add = MagicMock()
    session.flush = AsyncMock(side_effect=RuntimeError("audit db down"))

    service = AuditService(session)

    with pytest.raises(RuntimeError, match="audit db down"):
        await service.log(
            action="organization.updated",
            entity_type="organization",
            entity_id="00000000-0000-0000-0000-000000000001",
            organization_id="00000000-0000-0000-0000-000000000002",
        )
