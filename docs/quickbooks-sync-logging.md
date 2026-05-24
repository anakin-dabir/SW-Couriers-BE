# QuickBooks sync logging

Production-grade observability for all QuickBooks (QBO) sync operations: append-only `qb_sync_logs`, correlation across multi-step sagas, and safe payloads (no secrets or PII).

## Goals

| Pillar | Implementation |
|--------|----------------|
| **Complete** | Every queue (PENDING) and every terminal outcome (SYNCED / FAILED / SKIPPED) writes a log row |
| **Secure** | `sanitize_sync_payload()` strips tokens, secrets, emails (including nested `business`); IDs and amounts only |
| **Fast** | Single INSERT per row; logging never blocks or fails billing HTTP/worker commits |
| **Scalable** | Indexed columns; 8KB JSON payload cap (`json.dumps` byte check); ARQ dedupe unchanged |

## Architecture

```text
Domain (billing / invoices)
        │
        ▼
QuickBooksService.enqueue_*_sync  ──or──  _queue_sync_job
        │                                      │
        ├─ enqueue(ARQ job)                      │
        └─ _log_sync (PENDING)                   │
                    │  (_require_organization_id)
                    ▼
            qb_sync_logs (append-only)
                    ▲
                    │
        Worker: sync_*_now / void_*_now
        (ContextVar correlation optional)
```

**Rule:** Do not call `enqueue(Job.SYNC_QB_*)` from domain modules. Use `QuickBooksService.enqueue_invoice_sync`, `enqueue_payment_sync`, or void helpers.

### Core modules

| File | Role |
|------|------|
| `app/integrations/quickbooks/sync_logging.py` | Correlation IDs, `ContextVar`, payload build/sanitize |
| `app/integrations/quickbooks/service.py` | `_log_sync`, `_queue_sync_job`, all enqueue/sync paths |
| `app/integrations/quickbooks/tasks.py` | Sets `SyncLogContext` for void chain and payment jobs |

## Log row shape

| Column | Usage |
|--------|--------|
| `organization_id` | Always `QB_GLOBAL_NAMESPACE_ID` (global singleton QBO connection) |
| `entity_type` | `customer`, `invoice`, `credit_note`, `payment`, `credit_application` |
| `local_entity_id` | SW entity UUID |
| `event_type` | e.g. `INVOICE_QUEUED`, `CREDIT_NOTE_VOID_CHAIN_QUEUED` |
| `action` | `Queued`, `Created`, `Updated`, `No Change`, `VOIDED`, … |
| `status` | `PENDING`, `SYNCED`, `FAILED` |
| `job_id` | ARQ job id or deterministic `_job_id` |
| `attempt_no` | Worker retry attempt |
| `error_code` / `error_message` | Classified failure (message max 500 chars) |
| `payload` | JSONB — correlation, trigger, business context (see below) |

### Standard `payload` JSON

```json
{
  "correlation_id": "qb:void-cn:00000000-0000-4000-8000-000000000901:cn-uuid:v2",
  "trigger_source": "billing.void_credit_note",
  "trigger_entity_id": "{credit_note_id}",
  "step": "reversal_invoice_sync",
  "business": {
    "credit_note_number": "CN-2026-00004",
    "reversal_invoice_id": "...",
    "affected_invoice_ids": ["..."],
    "void_reason": "Reverse applied credit",
    "applied_total": "340.00"
  },
  "enqueue": {
    "job_name": "void_qb_credit_note_chain_task",
    "queued": true
  }
}
```

**Blocked keys** (top-level and nested dicts): `access_token`, `refresh_token`, `password`, `email`, `authorization`, `private_note_full`, plus any key containing `token` or `secret`.

**Size limits:** string values ≤ 500 chars; lists ≤ 50 items; dicts ≤ 32 keys; total serialized payload ≤ 8KB (`_truncated: true` when exceeded).

## Trigger sources

| `trigger_source` | When |
|--------------------|------|
| `quickbooks.manual_sync` | Admin POST `/integrations/quickbooks/.../sync` |
| `invoice.create_and_finalize` | Record / reversal invoice finalized |
| `invoice.sync` | Other invoice lifecycle enqueue |
| `invoice.void` / `invoice.write_off` | Invoice voided in SW → QBO void |
| `billing.void_credit_note` | Void credit note (simple or chain) |
| `billing.void_credit_note_reversal` | Reversal invoice queued before chain |
| `billing.payment_sync` | Payment allocated / voided with positive allocation |
| `quickbooks.sync_payment_now` | In-worker skip path |

## End-to-end flows

### Void credit note — applied (UI modal)

1. **Local:** `BillingService.void_credit_note` — reversal invoice + line item, CN → `VOIDED`.
2. **Queue:** `INVOICE_QUEUED` for reversal (`billing.void_credit_note_reversal`, shared `correlation_id`).
3. **Queue:** `CREDIT_NOTE_VOID_CHAIN_QUEUED` with `business` (CN#, reversal id, affected invoices, reason, amount).
4. **Worker** (`void_qb_credit_note_chain_task`, `ContextVar` correlation):
   - `CREDIT_NOTE_VOID_CHAIN_STEP` / `reversal_invoice_sync` (PENDING)
   - `sync_invoice_now` → `INVOICE_CREATED` or `UPDATED`
   - For each affected invoice: step log + `sync_invoice_now`
   - Step `void_credit_memo` + `void_credit_note_now` → `CREDIT_NOTE_VOIDED`
5. **On failure:** `chain_failed` (FAILED) + ARQ retry if transient.

### Void credit note — unapplied

1. `CREDIT_NOTE_VOID_QUEUED` (PENDING)
2. Worker: `void_credit_note_now` → `CREDIT_NOTE_VOIDED` or `CREDIT_NOTE_VOID_SKIPPED` (no QBO mapping)

### Payment sync

1. Only when **allocated amount > 0** → `PAYMENT_QUEUED` via `enqueue_payment_sync` (`BillingService._enqueue_qb_payment_sync`).
2. Worker may sync customer + invoices inline (logs inherit payment job `correlation_id` from `ContextVar`).
3. Skip path (no positive allocations, no existing QBO payment): `PAYMENT_SYNC_SKIPPED` (SYNCED + reason).

### Invoice / customer / credit note

- Admin or domain enqueue → `*_QUEUED` (PENDING).
- Worker → `*_CREATED`, `*_UPDATED`, `*_NO_CHANGE`, or `FAILED`.
- Invoice sync may emit `CREDIT_APPLICATION_APPLIED` per application.

## Correlation IDs

```python
correlation_id_for_void_credit_note(
    organization_id=QB_GLOBAL_NAMESPACE_ID,  # normalized before call
    credit_note_id="...",
    version=None,  # stable: qb:void-cn:{namespace}:{cn_id}
)
# With version: qb:void-cn:{namespace}:{cn_id}:v{version}  (per void attempt)
```

Query support UI / API (planned): `WHERE payload->>'correlation_id' = ?` ordered by `created_at`.

## Production safeguards

| Safeguard | Behavior |
|-----------|----------|
| `_log_sync` never raises | DB errors → `quickbooks.sync_log_write_failed` warning, returns `None` |
| No recursive logging | Persists only via `_sync_log_repo.log` |
| Namespace normalization | `_log_sync` and `_queue_sync_job` use `_require_organization_id` |
| Enqueue dedupe | ARQ returns `None` → PENDING row with `enqueue.queued: false` and stable `job_id` |
| Context isolation | `contextvars` per async task |
| Reversal before QB | Reversal invoice line items saved before `enqueue_invoice_sync` |
| Zero-allocation payments | `_enqueue_qb_payment_sync` skips enqueue when allocated ≤ 0 |

## Edge cases (handled)

| Scenario | Expected log / behavior |
|----------|-------------------------|
| Sync log DB write fails | Billing/QB sync continues; no exception from `_log_sync` |
| ARQ enqueue deduped (`None`) | `PENDING` + `enqueue.queued: false` |
| Void CN missing in DB | No enqueue, no log |
| Void CN already synced | Queue void job + `CREDIT_NOTE_VOID_QUEUED` |
| Applied void saga | Chain correlation + step logs; shared `correlation_id` on reversal invoice |
| Payment with no allocations / no QBO payment | `PAYMENT_SYNC_SKIPPED` |
| Payment with zero total allocation | No `PAYMENT_QUEUED` from billing |
| Payload contains tokens/email | Stripped at all nesting levels |
| Payload > 8KB after sanitize | `_truncated: true` |
| Empty payload | `NULL` in DB (sanitize returns `None`) |
| Bulk resync stale entity | Row skipped (`NotFoundError`), no 500 |

## Test coverage

| File | What it verifies |
|------|------------------|
| `tests/integrations/quickbooks/test_sync_logging.py` | Sanitize, context, correlation, JSON-safe types, truncation |
| `tests/integrations/quickbooks/test_sync_logging_service.py` | `_log_sync` failure, namespace, `_queue_sync_job`, void enqueue, payment skip, domain delegates |
| `tests/billing/test_billing_service_payments_unit.py` | Payment enqueue uses `enqueue_payment_sync` |
| `tests/invoices/test_invoices_service.py` | Reversal line item description for void |

### Run tests

```bash
# Sync logging focused
poetry run pytest tests/integrations/quickbooks/test_sync_logging.py tests/integrations/quickbooks/test_sync_logging_service.py -v

# QuickBooks + billing regression
poetry run pytest tests/integrations/quickbooks/ tests/billing/ -q
```

## Gaps / roadmap

| Item | Status |
|------|--------|
| Central `_queue_sync_job` for all domain enqueues | Done |
| Void chain PENDING + step logs | Done |
| Payment skip log | Done |
| Nested secret stripping in `business` | Done |
| `GET /logs?correlation_id=` filter | Planned |
| `duration_ms` on execution logs | Planned |
| Auto-sync CN on issue | Product decision |

## Deploy

No new migration for sync logging (uses existing `qb_sync_logs` JSONB).

Ensure workers register: `void_qb_credit_note_task`, `void_qb_credit_note_chain_task` in `app/workers/master.py`.

Related billing migration: `alembic upgrade head` for `0144_billing_enhancements` (`reversal_invoice_id`, `billing_contact_email`).
