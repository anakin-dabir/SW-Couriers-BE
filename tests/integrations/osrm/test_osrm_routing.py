from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.common.exceptions import ValidationError
from app.integrations.osrm.routing import (
    first_route_geometry,
    format_coordinate_path,
    route,
    route_linestring_coordinates,
    route_summary,
    table,
)


def test_format_coordinate_path() -> None:
    assert format_coordinate_path([(-0.1, 51.5), (-0.2, 51.6)]) == "-0.1,51.5;-0.2,51.6"


def test_format_coordinate_path_rejects_empty() -> None:
    with pytest.raises(ValidationError, match="At least one coordinate"):
        format_coordinate_path([])


def test_first_route_geometry_and_linestring() -> None:
    payload = {
        "code": "Ok",
        "routes": [
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-0.1, 51.5], [-0.11, 51.51]],
                }
            }
        ],
    }
    assert first_route_geometry(payload) == payload["routes"][0]["geometry"]
    assert route_linestring_coordinates(payload) == [[-0.1, 51.5], [-0.11, 51.51]]


def test_route_summary() -> None:
    payload = {"routes": [{"distance": 1000.5, "duration": 120.25}]}
    assert route_summary(payload) == {"distance": 1000.5, "duration": 120.25}


@pytest.mark.asyncio
async def test_route_calls_osrm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.integrations.osrm.routing.settings.OSRM_BASE_URL", "http://osrm.test")

    mock_response = AsyncMock()
    mock_response.raise_for_status = lambda: None
    mock_response.json = lambda: {"code": "Ok", "routes": [{"distance": 1, "duration": 2}]}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.osrm.routing.httpx.AsyncClient", return_value=mock_client):
        data = await route([(-0.12, 51.5), (-0.13, 51.51)])

    assert data["code"] == "Ok"
    mock_client.get.assert_awaited_once()
    call_kw = mock_client.get.await_args
    assert call_kw[0][0] == "http://osrm.test/route/v1/driving/-0.12,51.5;-0.13,51.51"


@pytest.mark.asyncio
async def test_route_requires_two_points(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.integrations.osrm.routing.settings.OSRM_BASE_URL", "http://osrm.test")
    with pytest.raises(ValidationError, match="At least two coordinates"):
        await route([(-0.12, 51.5)])


@pytest.mark.asyncio
async def test_osrm_error_code_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.integrations.osrm.routing.settings.OSRM_BASE_URL", "http://osrm.test")

    mock_response = AsyncMock()
    mock_response.raise_for_status = lambda: None
    mock_response.json = lambda: {"code": "NoRoute"}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.osrm.routing.httpx.AsyncClient", return_value=mock_client), pytest.raises(ValidationError, match="NoRoute"):
        await table([(-0.12, 51.5), (-0.13, 51.51)])
