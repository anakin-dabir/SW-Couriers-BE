"""Account statement module constants."""

from __future__ import annotations

from datetime import date

PDF_TEMPLATE_VERSION = "v1"
SIGNED_URL_EXPIRY_SECONDS = 300
MAX_PERIOD_DAYS = 366
MAX_LEDGER_ROWS = 5000
MAX_LINE_ITEMS_PER_INVOICE = 200

COMPANY_NAME = "SW COURIERS"
COMPANY_EMAIL = "accounts@swcouriers.co.uk"
COMPANY_ADDRESS = "55 Bridge End, Cardiff, CF10 2BN, United Kingdom"

# Local wall-clock time when schedules fire (org timezone).
SCHEDULE_RUN_HOUR = 6
SCHEDULE_RUN_MINUTE = 0

MIN_CUSTOM_INTERVAL_DAYS = 7
MAX_CUSTOM_INTERVAL_DAYS = 366

# Stored in ``custom_cron`` when CUSTOM has no interval — one statement for valid_from..valid_to.
CUSTOM_SCHEDULE_ONCE = "once"

# Ongoing MONTHLY_FIRST / QUARTERLY schedules when the client omits valid_to.
DEFAULT_SCHEDULE_VALID_TO = date(2099, 12, 31)
