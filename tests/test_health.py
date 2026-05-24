"""Tests for the health endpoint."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_200(client: AsyncClient) -> None:
    """GET /api/health returns 200 OK."""
    response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_returns_ok_status(client: AsyncClient) -> None:
    """GET /api/health returns JSON with status 'ok'."""
    response = await client.get("/health")
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_response_is_json(client: AsyncClient) -> None:
    """GET /api/health has application/json content-type."""
    response = await client.get("/health")
    assert response.headers["content-type"].startswith("application/json")
