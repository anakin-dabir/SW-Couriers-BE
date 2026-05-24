from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.common.enums import LogEvent
from app.common.exceptions import ValidationError
from app.core.config import settings

logger = structlog.get_logger()

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class CreditsafeNoCompanyFound(Exception):
    """Raised when a Creditsafe search returns no matching company.

    Callers should treat this as a recoverable signal and may proceed to
    order a Fresh Investigation via ``request_fresh_investigation``.
    """

_STUB_REPORT: dict[str, Any] = {
    "provider": "creditsafe_stub",
    "companySummary": {
        "businessName": "Stub Company Ltd",
        "country": "GB",
        "companyRegistrationNumber": "00000000",
        "companyStatus": {"status": "Active"},
        "latestTurnoverFigure": {"value": 1_000_000, "currency": "GBP"},
        "creditRating": {"commonValue": "B", "commonDescription": "Caution — trading experience and/or data available suggests risk."},
    },
    "creditScore": {
        "currentCreditRating": {
            "commonValue": "B",
            "commonDescription": "Caution",
            "creditLimit": {"value": 5000, "currency": "GBP"},
            "providerValue": {"value": "48", "maxValue": "100", "minValue": "1"},
        },
    },
    "companyIdentification": {
        "basicInformation": {
            "registeredCompanyName": "Stub Company Ltd",
            "companyRegistrationNumber": "00000000",
            "country": "GB",
            "companyStatus": {"status": "Active"},
        },
        "activityClassifications": [{"classification": "Freight transport by road", "code": "49410"}],
    },
    "directors": {
        "currentDirectors": [
            {"name": "John Doe", "directorType": "Director", "dateOfBirth": "1980-01-15"},
        ],
    },
    "contactInformation": {
        "mainAddress": {
            "simpleValue": "124 City Road, London, EC1V 2NX",
            "street": "124 City Road",
            "city": "London",
            "postCode": "EC1V 2NX",
            "country": "GB",
        },
    },
    "indicators": [],
    "negativeInformation": {"countyCourtJudgements": [], "companyMortgages": []},
    "paymentData": {},
}


async def authenticate() -> str:
    """Authenticate against the Creditsafe Connect API and return a JWT bearer token.

    Sends a POST request to ``{CREDITSAFE_API_URL}/authenticate`` with the
    configured username and password. Returns the raw token string.

    Raises ``ValidationError`` when credentials are missing, invalid, or the
    Creditsafe service is unreachable.
    """
    base = settings.CREDITSAFE_API_URL.rstrip("/")
    username = settings.CREDITSAFE_USERNAME
    password = settings.CREDITSAFE_PASSWORD.get_secret_value()

    if not username or not password:
        raise ValidationError("Creditsafe credentials are not configured.")

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{base}/authenticate",
                json={"username": username, "password": password},
            )
            resp.raise_for_status()
            return resp.json()["token"]
    except httpx.HTTPStatusError as exc:
        logger.error(LogEvent.CREDITSAFE_AUTH_FAILED, status=exc.response.status_code)
        raise ValidationError("Creditsafe authentication failed. Please verify credentials.") from exc
    except httpx.HTTPError as exc:
        logger.error(LogEvent.CREDITSAFE_AUTH_FAILED, error=str(exc))
        raise ValidationError("Unable to reach Creditsafe authentication service.") from exc


async def search_company(token: str, *, reg_no: str | None = None, company_name: str | None = None, country: str = "GB") -> dict[str, Any]:
    """Search for a company on Creditsafe by registration number or name.

    Sends a GET request to ``{CREDITSAFE_API_URL}/companies`` with ``countries``
    set to *country* and either ``regNo`` or ``name`` as the search filter.
    Returns the first matching company dict, which includes the ``connectId``
    needed to fetch a full credit report.

    Raises ``ValidationError`` if no search criteria are given, no results are
    found, or the search API returns an error.
    """
    if not reg_no and not company_name:
        raise ValidationError("Either registration number or company name is required for Creditsafe search.")

    base = settings.CREDITSAFE_API_URL.rstrip("/")
    params: dict[str, str] = {"countries": country, "pageSize": "1"}
    if reg_no:
        params["regNo"] = reg_no
    else:
        params["name"] = company_name  # type: ignore[assignment]

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{base}/companies",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error(LogEvent.CREDITSAFE_SEARCH_FAILED, status=exc.response.status_code, reg_no=reg_no)
        raise ValidationError("Creditsafe company search failed.") from exc
    except httpx.HTTPError as exc:
        logger.error(LogEvent.CREDITSAFE_SEARCH_FAILED, error=str(exc), reg_no=reg_no)
        raise ValidationError("Unable to reach Creditsafe search service.") from exc

    companies = data.get("companies", [])
    if not companies:
        logger.warning(LogEvent.CREDITSAFE_NO_RESULTS, reg_no=reg_no, company_name=company_name)
        raise CreditsafeNoCompanyFound(
            f"No matching company found on Creditsafe (reg_no={reg_no}, name={company_name})."
        )

    return companies[0]


async def get_credit_report(token: str, connect_id: str) -> dict[str, Any]:
    """Fetch the full credit report for a company from Creditsafe.

    Sends a GET request to ``{CREDITSAFE_API_URL}/companies/{connect_id}``
    with ``template=full`` and ``includeIndicators=true``. Returns the
    complete report dictionary containing ``companySummary``, ``creditScore``,
    ``companyIdentification``, ``directors``, ``contactInformation``,
    ``negativeInformation``, ``paymentData``, ``indicators``, and other
    sections as provided by the Creditsafe Connect API.

    Raises ``ValidationError`` if the report cannot be retrieved.
    """
    base = settings.CREDITSAFE_API_URL.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{base}/companies/{connect_id}",
                params={"template": "full", "includeIndicators": "true"},
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error(LogEvent.CREDITSAFE_REPORT_FAILED, status=exc.response.status_code, connect_id=connect_id)
        raise ValidationError("Failed to retrieve Creditsafe credit report.") from exc
    except httpx.HTTPError as exc:
        logger.error(LogEvent.CREDITSAFE_REPORT_FAILED, error=str(exc), connect_id=connect_id)
        raise ValidationError("Unable to reach Creditsafe report service.") from exc

    return data.get("report", data)


async def run_credit_assessment(*, reg_no: str | None = None, company_name: str | None = None) -> tuple[str, dict[str, Any]]:
    """Run a full credit assessment for a company via the Creditsafe Connect API.

    Orchestrates the three-step flow: authenticate → search company → fetch
    credit report. Returns a tuple of ``(connect_id, report_dict)`` where
    *connect_id* is the Creditsafe company identifier and *report_dict* is
    the complete report JSON stored as-is in the ``creditsafe_report`` JSONB
    column.

    When ``CREDITSAFE_API_URL`` is empty (local dev / test), returns a
    deterministic stub response so the workflow can proceed without real
    API credentials.

    Raises ``CreditsafeNoCompanyFound`` when the company search returns no
    results — callers may follow up by requesting a Fresh Investigation.
    Raises ``ValidationError`` if any other step in the pipeline fails.
    """
    if not settings.CREDITSAFE_API_URL:
        stub_id = f"stub-{(reg_no or 'unknown')[:20]}"
        return stub_id, _STUB_REPORT

    token = await authenticate()
    company = await search_company(token, reg_no=reg_no, company_name=company_name)

    connect_id: str = company.get("connectId", company.get("id", ""))
    if not connect_id:
        raise ValidationError("Creditsafe returned a company match without a connectId.")

    report = await get_credit_report(token, connect_id)
    return connect_id, report


async def request_fresh_investigation(
    *,
    reg_no: str | None = None,
    company_name: str | None = None,
    country: str = "GB",
) -> dict[str, Any]:
    """Order a Fresh Investigation from Creditsafe for a company not in their database.

    Sends a POST request to ``{CREDITSAFE_API_URL}/freshinvestigations`` with
    the registration number and/or trading name. Creditsafe typically takes
    2-3 business days to complete and make a full report available via the
    standard search/report endpoints. Returns the raw response dict, which
    includes at least a ``reference`` or ``id`` identifying the order.

    When ``CREDITSAFE_API_URL`` is empty (local dev / test), returns a
    deterministic stub response so the workflow can proceed without real
    API credentials.

    Raises ``ValidationError`` if neither identifier is provided or the
    Creditsafe investigation API is unavailable.
    """
    if not reg_no and not company_name:
        raise ValidationError("Either registration number or company name is required to order a fresh investigation.")

    if not settings.CREDITSAFE_API_URL:
        return {
            "provider": "creditsafe_stub",
            "reference": f"stub-inv-{(reg_no or company_name or 'unknown')[:20]}",
            "status": "IN_PROGRESS",
            "country": country,
            "regNo": reg_no,
            "companyName": company_name,
        }

    token = await authenticate()
    base = settings.CREDITSAFE_API_URL.rstrip("/")
    payload: dict[str, Any] = {"country": country}
    if reg_no:
        payload["regNo"] = reg_no
    if company_name:
        payload["companyName"] = company_name

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{base}/freshinvestigations",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error(
            LogEvent.CREDITSAFE_FRESH_INVESTIGATION_FAILED,
            status=exc.response.status_code,
            reg_no=reg_no,
        )
        raise ValidationError("Creditsafe fresh investigation request failed.") from exc
    except httpx.HTTPError as exc:
        logger.error(
            LogEvent.CREDITSAFE_FRESH_INVESTIGATION_FAILED,
            error=str(exc),
            reg_no=reg_no,
        )
        raise ValidationError("Unable to reach Creditsafe fresh investigation service.") from exc
