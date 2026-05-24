"""App-wide constants."""

API_VERSION = "0.1.0"
SERVICE_NAME = "sw-couriers-api"

# ── Account lockout ──────────────────────────
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15

# ── Password policy ──────────────────────────
MIN_PASSWORD_LENGTH = 8

# ── Pagination defaults ──────────────────────
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


# ── Session limit ─────────────────────────────
MAX_ACTIVE_SESSIONS_PER_USER = 3

# ── Password reset throttle ──────────────────
PWD_RESET_MAX_DAILY_REQUESTS = 5
PWD_RESET_BASE_COOLDOWN_SECONDS = 30  # 30s, 60s, 120s, 240s, 480s

INVITE_SEND_MAX_DAILY_REQUESTS = 5
INVITE_SEND_BASE_COOLDOWN_SECONDS = 30

INVITE_LINK_REMINDER_SUPPRESS_TTL_SECONDS = 604_800

# ── GPS Telemetry ────────────────────────────
GPS_BATCH_MAX_SIZE = 100
GPS_STALE_THRESHOLD_SECONDS = 300  # 5 minutes
GPS_MAX_SPEED_KPH = 200  # plausibility check

# ── Payments ────────────────────────────────
MAX_PAYMENT_METHODS_PER_OWNER = 5

# ── Delivery ─────────────────────────────────
MAX_DELIVERY_ATTEMPTS = 3

# ── Cache TTL (seconds) ──────────────────────
CACHE_TTL_GEOCODE = 2_592_000  # 30 days
CACHE_TTL_OSRM_ROUTE = 3_600  # 1 hour
CACHE_TTL_DRIVER_POSITION = 60  # 1 minute
