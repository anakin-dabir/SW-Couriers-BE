"""QuickBooks API client with token refresh and retry-safe request handling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

from app.common.exceptions import AppError, AuthenticationError
from app.core.config import settings
from app.integrations.quickbooks.auth import decrypt_token, encrypt_token, get_qbo_api_base_url, refresh_tokens
from app.integrations.quickbooks.models import QbConnection
from app.integrations.quickbooks.repository import QbConnectionRepository

logger = structlog.get_logger()


class QuickBooksClient:
    """Thin async QuickBooks client bound to one organization connection."""

    def __init__(self, repo: QbConnectionRepository, organization_id: str) -> None:
        self._repo = repo
        self._organization_id = organization_id
        self._base_url = get_qbo_api_base_url()
        self._timeout = httpx.Timeout(
            connect=settings.QUICKBOOKS_CONNECT_TIMEOUT_MS / 1000,
            read=settings.QUICKBOOKS_READ_TIMEOUT_MS / 1000,
            write=settings.QUICKBOOKS_READ_TIMEOUT_MS / 1000,
            pool=settings.QUICKBOOKS_CONNECT_TIMEOUT_MS / 1000,
        )

    @staticmethod
    def _needs_refresh(conn: QbConnection, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        lead_window = timedelta(seconds=settings.QUICKBOOKS_REFRESH_LEAD_SECONDS)
        return conn.access_token_expires_at <= current + lead_window

    async def _get_connection(self, *, force_refresh: bool = False) -> QbConnection:
        conn = await self._repo.get_active_by_org(self._organization_id)
        if conn is None:
            raise AuthenticationError("QuickBooks is not connected for this organization")
        if force_refresh or self._needs_refresh(conn):
            conn = await self._refresh(conn)
        return conn

    async def ensure_connection(self, *, force_refresh: bool = False) -> QbConnection:
        """Used by schedulers/tasks to refresh due connections without API calls."""
        return await self._get_connection(force_refresh=force_refresh)

    async def _refresh(self, conn: QbConnection) -> QbConnection:
        try:
            raw_refresh = decrypt_token(conn.refresh_token_enc)
            refreshed = await refresh_tokens(raw_refresh, conn.realm_id)
            updated = await self._repo.upsert_for_org(
                self._organization_id,
                {
                    "realm_id": refreshed["realm_id"],
                    "access_token_enc": encrypt_token(refreshed["access_token"]),
                    "refresh_token_enc": encrypt_token(refreshed["refresh_token"]),
                    "access_token_expires_at": refreshed["access_token_expires_at"],
                    "refresh_token_expires_at": refreshed["refresh_token_expires_at"],
                    "last_refreshed_at": datetime.now(UTC),
                    "is_active": True,
                    "last_error": None,
                    "last_error_at": None,
                },
            )
            return updated
        except Exception as exc:
            logger.warning(
                "quickbooks.token_refresh_failed",
                organization_id=self._organization_id,
                realm_id=getattr(conn, "realm_id", None),
                error=str(exc)[:500],
            )
            try:
                await self._repo.update_by_id(
                    conn.id,
                    {"last_error": str(exc)[:500], "last_error_at": datetime.now(UTC)},
                    expected_version=conn.version,
                )
            except Exception:
                # Best effort: stale connection version should not mask primary refresh failure.
                logger.warning(
                    "quickbooks.token_refresh_failed_persist_error",
                    organization_id=self._organization_id,
                )
            raise

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict:
        conn = await self._get_connection()
        access_token = decrypt_token(conn.access_token_enc)
        url = f"{self._base_url}/v3/company/{conn.realm_id}{path}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.request(method, url, params=params, json=payload, headers=headers)
            if resp.status_code == 401:
                conn = await self._get_connection(force_refresh=True)
                access_token = decrypt_token(conn.access_token_enc)
                headers["Authorization"] = f"Bearer {access_token}"
                resp = await client.request(method, url, params=params, json=payload, headers=headers)

        if resp.status_code >= 400:
            try:
                error_payload = resp.json()
                error_text = str(error_payload)
            except ValueError:
                error_text = resp.text
            raise AppError(f"QuickBooks API request failed ({resp.status_code}): {error_text[:1000]}")
        if resp.status_code == 204:
            return {}
        return resp.json()

    async def create_customer(self, payload: dict[str, Any]) -> dict:
        return await self.request("POST", "/customer", payload=payload)

    async def find_customer_by_email(self, email: str | None) -> dict[str, Any] | None:
        cleaned = str(email or "").strip()
        if not cleaned:
            return None
        escaped = cleaned.replace("'", "''")
        query = f"select Id, SyncToken, PrimaryEmailAddr from Customer where PrimaryEmailAddr = '{escaped}' maxresults 1"
        response = await self.request("GET", "/query", params={"query": query})
        customers = (response.get("QueryResponse", {}) or {}).get("Customer", []) or []
        if not customers:
            return None
        first = customers[0]
        return first if isinstance(first, dict) else None

    async def update_customer(self, payload: dict[str, Any]) -> dict:
        return await self.request("POST", "/customer", payload=payload)

    async def create_invoice(self, payload: dict[str, Any]) -> dict:
        return await self.request("POST", "/invoice", payload=payload)

    async def update_invoice(self, payload: dict[str, Any]) -> dict:
        return await self.request("POST", "/invoice", payload=payload)

    async def create_credit_memo(self, payload: dict[str, Any]) -> dict:
        return await self.request("POST", "/creditmemo", payload=payload)

    async def update_credit_memo(self, payload: dict[str, Any]) -> dict:
        return await self.request("POST", "/creditmemo", payload=payload)

    async def create_payment(self, payload: dict[str, Any]) -> dict:
        return await self.request("POST", "/payment", payload=payload)

    async def update_payment(self, payload: dict[str, Any]) -> dict:
        return await self.request("POST", "/payment", payload=payload)

    async def void_invoice(self, *, qb_invoice_id: str, sync_token: str) -> dict:
        return await self.request(
            "POST",
            "/invoice",
            params={"operation": "delete"},
            payload={"Id": qb_invoice_id, "SyncToken": sync_token},
        )

    async def void_credit_memo(self, *, qb_credit_memo_id: str, sync_token: str) -> dict:
        return await self.request(
            "POST",
            "/creditmemo",
            params={"operation": "delete"},
            payload={"Id": qb_credit_memo_id, "SyncToken": sync_token},
        )
