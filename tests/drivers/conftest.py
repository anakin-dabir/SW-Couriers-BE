"""Driver test package: bypass driver document step-up for existing API tests.

Full OTP + token flow is covered in test_driver_doc_access_api.py only.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _bypass_driver_document_stepup_auth(request: pytest.FixtureRequest, app) -> None:
    """Skip X-Driver-Doc-Access-Token for legacy driver tests; keep real checks in doc-access tests."""
    node_path = getattr(request.node, "path", None)
    name = getattr(node_path, "name", None) or ""
    # Only enforce real token validation for dedicated OTP/token tests
    if name in ("test_driver_doc_access_api.py", "test_draft_doc_access_integration.py"):
        yield
        return

    from app.common.deps import _require_driver_doc_access

    async def _ok() -> None:
        return None

    app.dependency_overrides[_require_driver_doc_access] = _ok
    yield
    app.dependency_overrides.pop(_require_driver_doc_access, None)
