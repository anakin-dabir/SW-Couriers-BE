from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvKind = Literal["development", "staging", "production", "test"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
CookieSameSite = Literal["lax", "strict", "none"]
BraintreeEnv = Literal["sandbox", "production"]
QuickBooksEnv = Literal["sandbox", "production"]
EmailProviderKind = Literal["smtp", "resend", "console"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        env_nested_delimiter="__",
        case_sensitive=True,
        extra="ignore",
    )

    # App
    ENVIRONMENT: EnvKind = Field(default="development")
    APP_ENV: EnvKind = Field(default="development")
    DEBUG: bool = False
    LOG_LEVEL: LogLevel = Field(default="INFO")

    # API
    API_PREFIX: str = Field(default="")
    TRUST_X_FORWARDED_FOR: bool = False

    # Database
    DATABASE_URL: str = ""
    DATABASE_ECHO: bool = False
    DATABASE_POOL_SIZE: int = Field(default=5, ge=1, le=100)
    DATABASE_MAX_OVERFLOW: int = Field(default=10, ge=0, le=50)

    # Redis (docker-compose maps Redis to 6380 for local dev)
    REDIS_URL: str = Field(default="redis://localhost:6380/0")

    # CORS
    CORS_ORIGINS: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174,http://localhost:5175,http://127.0.0.1:5175"
    )
    CORS_ALLOW_METHODS: str = Field(default="GET,POST,PUT,PATCH,DELETE,OPTIONS")
    CORS_ALLOW_HEADERS: str = Field(
        default="Authorization,Content-Type,Accept,Origin,X-Client-Type,X-Idempotency-Key,X-Doc-Access-Token,X-Driver-Doc-Access-Token,X-Password-Reset-Token,X-API-Key,X-Vehicle-Doc-Access-Token"
    )

    # Auth / cookies
    COOKIE_SAMESITE: CookieSameSite = Field(default="none")
    VERIFICATION_LINK_BASE_URL: str = Field(default="", max_length=2_000)

    # Frontend base URL used for in-app shared document links in emails.
    FRONTEND_BASE_URL: str = Field(default="http://localhost:3000", max_length=2_000)

    # Link base URLs per client type (for invite, verify-email links). Fallback: VERIFICATION_LINK_BASE_URL.
    # Web clients: https://portal.example.com. Driver (Flutter app): use app deep-link scheme, e.g. swcouriers://
    LINK_BASE_URL_ADMIN: str = Field(default="http://localhost:5173", max_length=2_000)
    LINK_BASE_URL_CUSTOMER_B2B: str = Field(default="http://localhost:5174", max_length=2_000)
    LINK_BASE_URL_CUSTOMER_B2C: str = Field(default="http://localhost:5175", max_length=2_000)
    LINK_BASE_URL_WAREHOUSE: str = Field(default="http://localhost:5176", max_length=2_000)
    # Driver app / universal links: e.g. swcouriers:// or https://driver-invite.example.com (no trailing slash).
    LINK_BASE_URL_DRIVER: str = Field(default="swcouriers://", max_length=2_000)
    DRIVER_ACTIVATION_INVITE_EXPIRE_DAYS: int = Field(default=7, ge=1, le=30)
    DRIVER_ACTIVATION_RESEND_MAX_PER_HOUR: int = Field(default=5, ge=1, le=30)
    EMAIL_LINK_ALLOWED_APP_SCHEMES: str = Field(default="swcouriers", max_length=500)
    JWT_SECRET_KEY: SecretStr
    JWT_ALGORITHM: Literal["HS256", "HS384", "HS512"] = Field(default="HS256")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, ge=1, le=60)
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, ge=1, le=90)
    JWT_REFRESH_SECRET_KEY: SecretStr
    JWT_EMAIL_VERIFICATION_SECRET_KEY: SecretStr = SecretStr("")

    # Optional MaxMind GeoLite2 City database (.mmdb) for session list location labels.
    # Download from MaxMind (license required); leave empty to omit location_label in API.
    GEOIP_MAXMIND_CITY_DB_PATH: str = Field(default="", max_length=1024)

    # Braintree
    BRAINTREE_MERCHANT_ID: str = ""
    BRAINTREE_PUBLIC_KEY: str = ""
    BRAINTREE_PRIVATE_KEY: SecretStr = SecretStr("")
    BRAINTREE_ENVIRONMENT: BraintreeEnv = Field(default="sandbox")
    BRAINTREE_REQUIRE_THREE_D_SECURE_FOR_VAULT: bool = True

    # Cloudflare R2 (S3-compatible object storage)
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: SecretStr = SecretStr("")
    R2_BUCKET_NAME: str = Field(default="sw-couriers", min_length=1)
    R2_ENDPOINT_URL: str = ""

    # Cloudflare Images (for profile photos and non-sensitive images)
    # We align env var names with existing infrastructure:
    #   CF_ACCOUNT_ID        → account identifier
    #   CF_API_TOKEN         → API token with Images permissions (for upload)
    #   CF_IMAGE_CDN_URL      → serving base only: https://imagedelivery.net/<account_hash> (no /image_id/variant)
    #   CF_ACCOUNT_HASH       → optional; used if CF_IMAGE_CDN_URL is empty or still has doc placeholders
    #   CF_IMAGES_SIGNING_KEY      → HMAC key from Dashboard → Hosted Images → Keys (signed URLs)
    #   CF_PRIVATE_IMAGE_TOKEN     → same value as CF_IMAGES_SIGNING_KEY if your env uses this name instead
    CF_ACCOUNT_ID: str = ""
    CF_API_TOKEN: SecretStr = SecretStr("")
    CF_IMAGES_BASE_URL: str = ""  # optional API base override (uploads only; not for CDN serving)
    CF_ACCOUNT_HASH: str = ""  # Images delivery hash (Dashboard → Images); builds CDN base when URL is wrong/missing
    CF_IMAGE_CDN_URL: str = ""  # CDN base URL, e.g. https://imagedelivery.net/<account_hash>
    CF_IMAGES_SIGNING_KEY: SecretStr = SecretStr("")
    CF_PRIVATE_IMAGE_TOKEN: SecretStr = SecretStr("")  # alias for CF_IMAGES_SIGNING_KEY (either may be set)

    # Twilio
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: SecretStr = SecretStr("")
    TWILIO_FROM_NUMBER: str = ""

    # Email
    EMAIL_PROVIDER: EmailProviderKind = Field(default="smtp")
    SMTP_HOST: str = ""
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: SecretStr = SecretStr("")
    EMAIL_FROM_ADDRESS: str = Field(default="noreply@swcouriers.com")
    EMAIL_FROM_NAME: str = Field(default="SW Couriers", max_length=200)
    EMAIL_LOGO_URL: str = Field(default="", max_length=2_000)
    SUPPORT_EMAIL: str = Field(default="support@swcouriers.com", max_length=255)
    FINANCE_TEAM_EMAIL: str = Field(default="", max_length=255)

    # Firebase Cloud Messaging (push notifications)
    FCM_PROJECT_ID: str = ""
    FCM_SERVICE_ACCOUNT_JSON: SecretStr = SecretStr("")

    # Creditsafe Connect API (credit assessment)
    CREDITSAFE_API_URL: str = ""
    CREDITSAFE_USERNAME: str = ""
    CREDITSAFE_PASSWORD: SecretStr = SecretStr("")

    # Ideal Postcodes
    IDEAL_POSTCODES_API_KEY: SecretStr = SecretStr("")

    # Google Maps Platform — Geocoding API (server key; restrict by IP/API in GCP)
    GOOGLE_MAPS_API_KEY: SecretStr = SecretStr("")
    # QuickBooks
    QUICKBOOKS_CLIENT_ID: str = ""
    QUICKBOOKS_CLIENT_SECRET: SecretStr = SecretStr("")
    QUICKBOOKS_ENV: QuickBooksEnv = Field(default="sandbox")
    QUICKBOOKS_REDIRECT_URI: str = Field(default="", max_length=2048)
    QUICKBOOKS_SCOPES: str = Field(default="com.intuit.quickbooks.accounting")
    QUICKBOOKS_WEBHOOK_VERIFIER_TOKEN: SecretStr = SecretStr("")
    QUICKBOOKS_API_BASE_URL: str = Field(default="", max_length=2048)
    QUICKBOOKS_ENCRYPTION_KEY: SecretStr = SecretStr("")
    QUICKBOOKS_CONNECT_TIMEOUT_MS: int = Field(default=3000, ge=500, le=30000)
    QUICKBOOKS_READ_TIMEOUT_MS: int = Field(default=10000, ge=500, le=60000)
    QUICKBOOKS_REFRESH_LEAD_SECONDS: int = Field(default=600, ge=30, le=86400)
    QUICKBOOKS_REFRESH_SAFETY_MAX_AGE_SECONDS: int = Field(default=86400, ge=300, le=2592000)
    QUICKBOOKS_SCOPE_ID: str = Field(default="", max_length=64)  # deprecated; global singleton no longer reads this
    QUICKBOOKS_OAUTH_SUCCESS_URL: str = Field(default="", max_length=2048)
    QUICKBOOKS_OAUTH_ERROR_URL: str = Field(default="", max_length=2048)
    QUICKBOOKS_OAUTH_REDIRECT_ALLOWED_HOSTS: str = Field(
        default="",
        max_length=4000,
        description="Comma-separated hostnames allowed for QB OAuth post-connect redirects.",
    )

    DRIVER_APP_PLAY_STORE_URL: str = Field(default="", max_length=2048)
    DRIVER_APP_APP_STORE_URL: str = Field(default="", max_length=2048)

    # Sentry
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str | None = None
    SENTRY_RELEASE: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = Field(default=0.1, ge=0.0, le=1.0)

    # Swagger/ReDoc (empty = docs return 401; override in .env for local dev)
    DOCS_USER: str = ""
    DOCS_PASSWORD: SecretStr = SecretStr("")

    # Orders — reject unknown stop_notes.note_type on create/update when true (after alias normalization).
    STRICT_STOP_NOTE_TYPES: bool = Field(default=False)

    # Local file storage (dev/test only — swap for R2 upload in production)
    LOCAL_UPLOAD_DIR: str = Field(default="uploads")

    # OSRM
    OSRM_BASE_URL: str = Field(default="http://localhost:5000")

    # Rate limiting

    # - RATE_LIMIT_DEFAULT: general API default
    # - RATE_LIMIT_AUTH: auth endpoints (login, register, password reset, refresh, logout)
    # - RATE_LIMIT_DRIVERS_WRITE / READ: admin drivers/holidays management APIs
    # - RATE_LIMIT_SUSPENSION_RULES_WRITE / READ: admin suspension-rules APIs
    # - RATE_LIMIT_SERVICE_TIER_WRITE / READ: admin service-tier (pricing) APIs
    # - RATE_LIMIT_QUICKBOOKS_SYNC / RESYNC / RECONCILE: high-impact QuickBooks trigger endpoints
    # - RATE_LIMIT_QUICKBOOKS_CALLBACK: OAuth callback IP throttling
    # - RATE_LIMIT_QUICKBOOKS_CALLBACK_DUPLICATE: duplicate callback attempts per state token
    # - RATE_LIMIT_SHARED_DOC_PASSWORD: public shared-document /verify, /access, /download (SlowAPI)
    # - RATE_LIMIT_DOC_OTP_VERIFY: org + driver document OTP verify (SlowAPI); lockout also enforced in service (Redis)
    RATE_LIMIT_DEFAULT: str = Field(default="300/minute")
    RATE_LIMIT_AUTH: str = Field(default="10/minute")
    RATE_LIMIT_DRIVERS_WRITE: str = Field(default="10/minute")
    RATE_LIMIT_DRIVERS_READ: str = Field(default="120/minute")

    RATE_LIMIT_SUSPENSION_RULES_WRITE: str = Field(default="5/minute")
    RATE_LIMIT_SUSPENSION_RULES_READ: str = Field(default="60/minute")

    RATE_LIMIT_SERVICE_TIER_WRITE: str = Field(default="60/minute")
    RATE_LIMIT_SERVICE_TIER_READ: str = Field(default="60/minute")

    RATE_LIMIT_SHARED_DOC_PASSWORD: str = Field(default="5/minute")
    RATE_LIMIT_DOC_OTP_VERIFY: str = Field(default="5/minute")
    RATE_LIMIT_SHARED_DOC_OTP: str = Field(default="5/minute")
    RATE_LIMIT_QUICKBOOKS_SYNC: str = Field(default="10/minute")
    RATE_LIMIT_QUICKBOOKS_RESYNC: str = Field(default="5/minute")
    RATE_LIMIT_QUICKBOOKS_RECONCILE: str = Field(default="3/minute")
    RATE_LIMIT_QUICKBOOKS_CALLBACK: str = Field(default="10/minute")
    RATE_LIMIT_QUICKBOOKS_CALLBACK_DUPLICATE: str = Field(default="3/minute")
    QUICKBOOKS_ORG_QUEUE_MAX_PENDING: int = Field(default=500, ge=1, le=50000)

    # Status automation runtime rollout controls
    STATUS_AUTOMATION_RUNTIME_ENABLED: bool = Field(default=True)
    STATUS_AUTOMATION_SHADOW_MODE: bool = Field(default=False)
    STATUS_AUTOMATION_ENABLED_ORG_IDS: str = Field(default="")

    # Idempotency
    IDEMPOTENCY_KEY_TTL: int = Field(default=86400, ge=60, le=604800)

    @property
    def email_verification_secret(self) -> str:
        """Dedicated secret for email verification JWTs.

        Falls back to JWT_SECRET_KEY in dev/test for convenience.
        In production, a dedicated secret is required to prevent
        a single key compromise from cascading across token types.
        """
        val = self.JWT_EMAIL_VERIFICATION_SECRET_KEY.get_secret_value()
        if not val:
            if self.is_production:
                raise ValueError("JWT_EMAIL_VERIFICATION_SECRET_KEY is required in production. " "Using the access-token secret as fallback is a security risk.")
            return self.JWT_SECRET_KEY.get_secret_value()
        return val

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def cors_allow_methods_list(self) -> list[str]:
        return [m.strip() for m in self.CORS_ALLOW_METHODS.split(",") if m.strip()]

    @property
    def cors_allow_headers_list(self) -> list[str]:
        return [h.strip() for h in self.CORS_ALLOW_HEADERS.split(",") if h.strip()]

    @property
    def is_test(self) -> bool:
        return self.APP_ENV == "test"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def status_automation_enabled_org_ids(self) -> set[str]:
        return {o.strip() for o in self.STATUS_AUTOMATION_ENABLED_ORG_IDS.split(",") if o.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]


settings = get_settings()
