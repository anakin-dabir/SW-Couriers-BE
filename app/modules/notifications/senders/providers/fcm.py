"""FCM push provider — sends via Firebase Cloud Messaging HTTP v1 API.

Auth uses the service account JSON stored in FCM_SERVICE_ACCOUNT_JSON:
  1. Signs a JWT (RS256) with the SA private key using PyJWT
  2. Exchanges it at Google's OAuth2 token endpoint for a short-lived access token
  3. Caches the access token in Redis so all workers share it
"""

import json
import time

import structlog

from app.common.enums.logger import LogEvent
from app.modules.notifications.enums import NotificationStatus
from app.modules.notifications.senders.providers.base import BasePushProvider

logger = structlog.get_logger()

_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_JWT_LIFETIME = 3600
# Refresh 5 min before actual Google expiry
_TOKEN_REFRESH_MARGIN = 300
_REDIS_KEY = "fcm:access_token"


class FCMProvider(BasePushProvider):
    """Firebase Cloud Messaging HTTP v1 provider (Android + iOS via FCM)."""

    async def send(
        self,
        *,
        device_token: str,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> tuple[NotificationStatus, str | None, str | None]:
        import httpx

        from app.core.config import settings

        project_id = (settings.FCM_PROJECT_ID or "").strip()
        if not project_id:
            logger.warning(LogEvent.NOTIFICATION_PUSH_NOT_CONFIGURED, reason="FCM_PROJECT_ID not set")
            return NotificationStatus.FAILED, "FCM not configured", None

        access_token = await self._get_access_token()
        if access_token is None:
            return NotificationStatus.FAILED, "FCM auth failed", None

        url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

        message_payload: dict = {
            "message": {
                "token": device_token,
                "notification": {
                    "title": title,
                    "body": body,
                },
            },
        }
        if data:
            message_payload["message"]["data"] = {k: str(v) for k, v in data.items()}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    url,
                    json=message_payload,
                    headers={"Authorization": f"Bearer {access_token}"},
                )

            if resp.status_code >= 400:
                error_body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                error_detail = error_body.get("error", {})
                error_msg = error_detail.get("message", f"HTTP {resp.status_code}")
                logger.error(LogEvent.NOTIFICATION_PUSH_SEND_FAILED, status_code=resp.status_code, error=error_msg)
                return NotificationStatus.FAILED, error_msg, None

            resp_data = resp.json()
            message_name = resp_data.get("name", "")
            logger.info(LogEvent.NOTIFICATION_PUSH_SENT, message_name=message_name)
            return NotificationStatus.SENT, None, message_name

        except httpx.TimeoutException:
            logger.error(LogEvent.NOTIFICATION_PUSH_SEND_TIMEOUT)
            return NotificationStatus.FAILED, "FCM request timeout", None
        except Exception as exc:
            logger.error(LogEvent.NOTIFICATION_PUSH_SEND_ERROR, error=type(exc).__name__)
            return NotificationStatus.FAILED, type(exc).__name__, None

    async def _get_access_token(self) -> str | None:
        from app.core.redis import get_redis

        redis = get_redis()

        cached: bytes | None = await redis.get(_REDIS_KEY)
        if cached is not None:
            return cached.decode()

        token, ttl = await self._exchange_token()
        if token is None:
            return None

        await redis.set(_REDIS_KEY, token, ex=ttl)
        return token

    async def _exchange_token(self) -> tuple[str | None, int]:
        import httpx
        import jwt

        from app.core.config import settings

        raw_json = settings.FCM_SERVICE_ACCOUNT_JSON.get_secret_value()
        if not raw_json:
            logger.warning(LogEvent.NOTIFICATION_PUSH_NOT_CONFIGURED, reason="FCM_SERVICE_ACCOUNT_JSON not set")
            return None, 0

        try:
            sa_info = json.loads(raw_json)
        except json.JSONDecodeError:
            logger.error(LogEvent.NOTIFICATION_PUSH_SA_JSON_INVALID)
            return None, 0

        private_key = sa_info.get("private_key")
        client_email = sa_info.get("client_email")
        if not private_key or not client_email:
            logger.error(LogEvent.NOTIFICATION_PUSH_SA_JSON_MISSING_FIELDS)
            return None, 0

        now = int(time.time())
        payload = {
            "iss": client_email,
            "scope": _FCM_SCOPE,
            "aud": _TOKEN_URI,
            "iat": now,
            "exp": now + _JWT_LIFETIME,
        }

        try:
            assertion = jwt.encode(payload, private_key, algorithm="RS256")
        except Exception as exc:
            logger.error(LogEvent.NOTIFICATION_PUSH_JWT_SIGN_FAILED, error=type(exc).__name__)
            return None, 0

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    _TOKEN_URI,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion,
                    },
                )

            if resp.status_code != 200:
                logger.error(
                    LogEvent.NOTIFICATION_PUSH_TOKEN_EXCHANGE_FAILED,
                    status_code=resp.status_code,
                )
                return None, 0

            token_data = resp.json()
            access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", _JWT_LIFETIME)
            ttl = max(expires_in - _TOKEN_REFRESH_MARGIN, 60)

            return access_token, ttl

        except Exception as exc:
            logger.error(LogEvent.NOTIFICATION_PUSH_TOKEN_EXCHANGE_ERROR, error=type(exc).__name__)
            return None, 0
