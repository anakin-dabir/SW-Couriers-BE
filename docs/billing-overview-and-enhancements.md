# Billing overview and enhancements

Branch: `feat/billing-overview`  
Primary commits: `570b2f9` (feature), `9cbecc3` (permission cleanup), plus QuickBooks sync logging (see [quickbooks-sync-logging.md](./quickbooks-sync-logging.md)).

This document covers **billing, invoices, credit notes, QuickBooks void/OAuth, driver onboarding email, and permissions**. Sync-log observability is documented separately.

---

## Summary

| Area | What changed |
|------|----------------|
| **Database** | `0144_billing_enhancements` — `credit_notes.reversal_invoice_id`, `invoices.billing_contact_email` |
| **Billing overview** | Org dashboard API with canonical KPIs/charts (`billing.metrics`) |
| **Void credit note** | Applied → reversal invoice + QB chain; unapplied → simple QB void |
| **Invoices** | Create with optional `line_items` + `billing_contact_email`; reversal line items on void |
| **Admin credit notes** | Void endpoint; admin PDF request/status/signed-url (parity with B2B) |
| **QuickBooks** | OAuth redirect allowlist; void CN tasks; sync logging gateway |
| **Permissions** | Payment routes use `Resource.BILLING` (aligned with staging; no new rights) |
| **Driver email** | Set-password invite includes app store links |

---

## Database migration

**File:** `alembic/versions/0144_billing_enhancements.py`  
**Depends on:** `0143_admin_invoices_to_billing`

| Table | Column | Purpose |
|-------|--------|---------|
| `credit_notes` | `reversal_invoice_id` (FK → `invoices.id`, unique index) | Links voided **applied** CN to its reversal invoice |
| `invoices` | `billing_contact_email` (varchar 255) | Billing contact on invoice/PDF/QBO |

```bash
poetry run alembic upgrade head
```

---

## Billing overview API

**Purpose:** Single canonical payload for the org **Billing → Overview** tab (KPIs + charts). Definitions live in one module so UI and API stay aligned.

### Endpoint

```
GET /api/v1/organizations/{organization_id}/billing/overview
```

| Query | Values | Notes |
|-------|--------|-------|
| `period` | `today`, `yesterday`, `last_7_days`, `last_30_days` | Default: `last_30_days` |
| `chart_year` | 2000–2100 | Revenue/activity chart year; defaults to current year |

### Auth

- Roles: `ADMIN`, `SUPER_ADMIN`
- Permission: `Resource.BILLING` **READ**
- `assert_org_profile_access(user, organization_id)` — user must access that org profile

### Modules

| File | Role |
|------|------|
| `app/modules/billing/metrics.py` | `DEFINITIONS_VERSION`, period windows, VAT split helpers |
| `app/modules/billing/overview_repository.py` | SQL aggregations |
| `app/modules/billing/overview_service.py` | Orchestration |
| `app/modules/billing/v1/org_overview_routes.py` | Route |
| `app/modules/billing/v1/overview_schemas.py` | Response models |

### KPI rules (high level)

- **total_billed** — excludes `VOIDED` / `WRITTEN_OFF` invoices
- **payments_received**, **outstanding_balance**, **overdue_amount** — period-scoped with prior-period comparison
- **credit_notes_issued**, **refunds_issued** — period totals
- Response `meta.definitions_version` — bump when formulas change (currently `1.0`)

### Router mount

Registered in `app/router.py` under prefix `/organizations`, tag `Billing Overview (v1)`.

---

## Void credit note

### Admin API

```
POST /api/v1/billing/credit-notes/{credit_note_id}/void?organization_id={org_id}
```

Body: `{ "reason": "..." }` (required, non-blank)  
ACL: `Resource.BILLING` **WRITE** (admin)

### Behaviour (`BillingService.void_credit_note`)

```text
Already VOIDED / WRITTEN_OFF → return as-is (idempotent)
Applied total > 0:
  1. InvoiceService.create_reversal_for_credit_note_void(...)
  2. Set CN status VOIDED + reversal_invoice_id
  3. Audit: billing.credit_note.voided_with_reversal
  4. QuickBooks: enqueue_void_credit_note_chain(...)
Else (unapplied):
  1. Set CN status VOIDED
  2. Audit: billing.credit_note.voided
  3. QuickBooks: enqueue_void_credit_note(...)
```

### Reversal invoice (`InvoiceService.create_reversal_for_credit_note_void`)

- Finalized invoice for **applied gross** (VAT split via `DEFAULT_VAT_RATE` / `split_vat_from_gross`)
- **Notes:** CN number + void reason (up to 2000 chars)
- **Line item:** single service line via `_reversal_line_item_description()` — includes CN number, applied GBP, void reason (max 255 chars)
- Line items persisted **before** QB enqueue (`queue_qb_sync=False` on create, then `_replace_line_items`, then `_enqueue_qb_invoice_sync`)
- QB trigger: `billing.void_credit_note_reversal` with shared saga `correlation_id`

### Credit note detail schema

`CreditNoteDetailResponse` exposes:

- `reversal_invoice_id`
- `reversal_invoice_number` (from relationship when loaded; uses `__dict__.get("reversal_invoice")` to avoid lazy-load 500s)

### QuickBooks void saga

| Case | ARQ job | Worker |
|------|---------|--------|
| Unapplied | `VOID_QB_CREDIT_NOTE` | `void_qb_credit_note_task` → `void_credit_note_now` |
| Applied | `VOID_QB_CREDIT_NOTE_CHAIN` | `void_qb_credit_note_chain_task` — reversal invoice sync → affected invoices → void credit memo |

Workers registered in `app/workers/master.py`.  
Observability: [quickbooks-sync-logging.md](./quickbooks-sync-logging.md).

---

## Invoice create enhancements

### API

`POST /api/v1/invoices` (admin only)

New/extended body fields:

| Field | Description |
|-------|-------------|
| `billing_contact_email` | Optional; stored on invoice, used in PDF/QBO |
| `line_items[]` | Optional; if present, sum of `total_price` must match `subtotal` (±0.02) |
| `finalize` | When true, `create_and_finalize` in one step |

After create, if `line_items` provided → `_replace_line_items` before returning detail.

### Model

`Invoice.billing_contact_email` on `app/modules/invoices/models.py`.

---

## Admin credit note PDF

Admin routes mirror B2B credit-note PDF flow (previously B2B-only):

| Method | Path |
|--------|------|
| POST | `/api/v1/billing/credit-notes/{id}/pdf` |
| GET | `/api/v1/billing/credit-notes/{id}/pdf` |
| POST | `/api/v1/billing/credit-notes/{id}/pdf/signed-url` |

Requires `organization_id` query param for admin scope.  
ACL: `Resource.BILLING` READ (PDF) / existing write deps for void.

---

## QuickBooks OAuth redirect

**Problem:** Intuit callback must redirect to admin UI without open-redirect risk.

**New:** `app/common/oauth_redirect.py`

- `validate_oauth_redirect_url()` — https only (http allowed in dev/test); host must be in allowlist
- `build_oauth_redirect()` — safe query merge
- Allowlist built from `QUICKBOOKS_OAUTH_REDIRECT_ALLOWED_HOSTS`, `LINK_BASE_URL_ADMIN`, `VERIFICATION_LINK_BASE_URL`

**Updated:** `app/integrations/quickbooks/routes.py` — callback uses validated success/error URLs from settings.

### Environment (`.env.example`)

```env
QUICKBOOKS_OAUTH_SUCCESS_URL=http://localhost:5173/settings/integrations/quickbooks
QUICKBOOKS_OAUTH_ERROR_URL=http://localhost:5173/settings/integrations/quickbooks
QUICKBOOKS_OAUTH_REDIRECT_ALLOWED_HOSTS=localhost,swdev.shiftopus.co.uk,shiftopus.co.uk,shiftopus.workers.dev
```

---

## Permissions (staging-aligned)

**No new permission resources.** Payment and billing admin routes use existing **`Resource.BILLING`**.

| Change | Detail |
|--------|--------|
| `AllowedPaymentAccess` | ADMIN, SUPER_ADMIN, CUSTOMER_B2B → `Resource.BILLING` (not `PAYMENTS`) |
| `permission/defaults.py` | Removed erroneous `PAYMENTS: W` from admin matrix comment/entry; matrix matches staging |
| Docs/tests | Swagger and tests reference `BILLING` for payment KPIs and admin payment access |

Overview uses **`BILLING` READ** (same ACL as invoices and admin account statements).

---

## Driver set-password invite (deeplink email)

**Template:** `app/mailer/templates/driver_set_password_invite.html`

- App store buttons via `DRIVER_APP_PLAY_STORE_URL` / `DRIVER_APP_APP_STORE_URL`
- Link built with `build_driver_set_password_link()` → `{LINK_BASE_URL_DRIVER}/set-password?email=...&token=...`

**Config:**

```env
LINK_BASE_URL_DRIVER=swcouriers://
DRIVER_APP_PLAY_STORE_URL=https://play.google.com/store/apps/details?id=...
DRIVER_APP_APP_STORE_URL=https://apps.apple.com/app/id...
```

Token is for the **mobile app** (`X-Invite-Token` on activation API), not a query param on the REST API.

---

## QuickBooks sync logging (cross-cutting)

Phase 1 adds a single enqueue/log gateway so every QB operation writes `qb_sync_logs` rows (PENDING + terminal), with correlation for void sagas.

**Doc:** [quickbooks-sync-logging.md](./quickbooks-sync-logging.md)  
**Code:** `app/integrations/quickbooks/sync_logging.py`, updates to `service.py` / `tasks.py`  
**Domain wiring:** `BillingService._enqueue_qb_payment_sync`, `InvoiceService._enqueue_qb_invoice_sync`

---

## Edge cases

| Scenario | Handling |
|----------|----------|
| Void already voided CN | Idempotent return |
| Void applied CN without customer | `ValidationError` before reversal |
| Void with empty reason | `ValidationError` |
| Reversal description too long | Truncated to 255 chars on line item |
| CN detail without eager `reversal_invoice` | Schema uses `__dict__.get` — no lazy-load 500 |
| Payment QB sync, zero allocation | `_enqueue_qb_payment_sync` skips enqueue |
| OAuth redirect to unknown host | `ValidationError` |
| QB log DB failure | Sync continues (`_log_sync` never raises) |
| ARQ dedupe (enqueue returns null) | PENDING log with `enqueue.queued: false` |

---

## Tests

| File | Coverage |
|------|----------|
| `tests/billing/test_billing_metrics.py` | Overview period/KPI helpers |
| `tests/billing/test_billing_api.py` | Overview route, BILLING permission gates |
| `tests/billing/test_billing_service_credit_notes_unit.py` | Void credit note service |
| `tests/billing/test_credit_notes_admin_api.py` | Admin void + PDF |
| `tests/billing/test_billing_service_payments_unit.py` | Payment QB enqueue delegate |
| `tests/invoices/test_invoices_service.py` | Reversal line description |
| `tests/common/test_oauth_redirect.py` | Redirect allowlist |
| `tests/integrations/quickbooks/test_sync_logging*.py` | Sync log unit + service |
| `tests/admins/test_admins_api.py` | Admin permission docs alignment |
| `tests/permission/test_defaults.py` | Default matrix |

### Run

```bash
# Billing + QB integration
poetry run pytest tests/billing/ tests/integrations/quickbooks/ tests/common/test_oauth_redirect.py -q

# Sync logging only
poetry run pytest tests/integrations/quickbooks/test_sync_logging.py tests/integrations/quickbooks/test_sync_logging_service.py -v
```

Last verified: **238 passed** (`tests/integrations/quickbooks/` + `tests/billing/`).

---

## Deploy checklist

1. **Migrate:** `alembic upgrade head` (includes `0144_billing_enhancements`).
2. **Env:** QB OAuth URLs + allowlist hosts; `LINK_BASE_URL_DRIVER`; app store URLs.
3. **Workers:** ARQ master includes `void_qb_credit_note_task`, `void_qb_credit_note_chain_task`, existing sync tasks.
4. **Intuit portal:** Redirect URI matches `QUICKBOOKS_REDIRECT_URI`.
5. **Frontend:** Wire overview to `GET .../billing/overview`; void CN modal posts reason to void endpoint.
6. **Permissions:** No migration for PAYMENTS — ensure admins have `BILLING` as today on staging.

---

## File index (main touchpoints)

```
alembic/versions/0144_billing_enhancements.py
app/modules/billing/metrics.py
app/modules/billing/overview_repository.py
app/modules/billing/overview_service.py
app/modules/billing/v1/org_overview_routes.py
app/modules/billing/v1/overview_schemas.py
app/modules/billing/service.py          # void_credit_note, payment QB enqueue
app/modules/billing/v1/routes.py        # void, admin PDF
app/modules/billing/v1/schemas.py       # reversal fields on CN detail
app/modules/invoices/service.py         # create_reversal_for_credit_note_void, line_items
app/modules/invoices/v1/routes.py       # billing_contact_email, line_items on create
app/common/oauth_redirect.py
app/common/deps.py                      # AllowedPaymentAccess → BILLING
app/integrations/quickbooks/routes.py   # OAuth callback
app/integrations/quickbooks/service.py  # void enqueue, void_credit_note_now
app/integrations/quickbooks/tasks.py    # void chain worker
app/integrations/quickbooks/sync_logging.py
app/workers/master.py
app/router.py                           # billing overview mount
docs/quickbooks-sync-logging.md
```

---

## Related docs

- [QuickBooks sync logging](./quickbooks-sync-logging.md) — `qb_sync_logs`, correlation, payloads, test matrix
