# Frontend guide — QuickBooks, billing overview, void credit notes

For the admin app (and any surface that touches billing + QuickBooks).  
Backend reference: [billing-overview-and-enhancements.md](./billing-overview-and-enhancements.md), [quickbooks-sync-logging.md](./quickbooks-sync-logging.md).

**API base:** `{API_URL}/api/v1`  
**QuickBooks prefix:** `/integrations/quickbooks`  
**Billing prefix:** `/billing`  
**Org overview:** `/organizations/{organizationId}/billing/overview`

**Auth headers (all authenticated calls):**

```http
Authorization: Bearer <access_token>
X-Client-Type: ADMIN
```

**Permissions:**

| Area | Resource | Level |
|------|----------|-------|
| QuickBooks settings, sync, logs | `QUICKBOOKS` | READ / WRITE |
| Billing, payments, credit notes, invoices | `BILLING` | READ / WRITE |
| Org billing overview tab | `BILLING` | READ |

There is **no separate `PAYMENTS` permission** on staging/production matrices — use `BILLING`.

---

## 1. What changed (FE checklist)

| # | Area | Action on FE |
|---|------|----------------|
| 1 | **OAuth connect** | Stop handling OAuth `code` in the SPA. Intuit → **API callback** → **302 redirect** to your settings page. |
| 2 | **Success / error page** | Add route handler for query params on `/settings/integrations/quickbooks`. |
| 3 | **Void credit note** | New modal + `POST .../void` with required `reason`. Show reversal invoice when applied. |
| 4 | **Credit note detail** | Display `reversal_invoice_id` / `reversal_invoice_number` + link to invoice. |
| 5 | **QB sync logs UI** | Show `PENDING` rows; read `payload` on detail (correlation, saga step, business). |
| 6 | **Void QB sync** | **Do not** call QB void/sync endpoints from FE — void CN API queues workers automatically. |
| 7 | **Billing overview** | New org tab calling overview API (not QB-specific but same release). |
| 8 | **Invoice create** | Optional `billing_contact_email` + `line_items[]`. |
| 9 | **Admin CN PDF** | Same PDF flow as B2B, with `organization_id` query param. |
| 10 | **Global QB** | One company-wide connection — no per-org connect/disconnect UI. |

---

## 2. QuickBooks OAuth — new flow

### Architecture

```text
[Admin clicks Connect]
       │
       ▼
GET /integrations/quickbooks/connect-url  (authenticated)
       │
       ▼
window.location = authorization_url   (Intuit login)
       │
       ▼
Intuit redirects to API (NOT your SPA):
  GET /api/v1/integrations/quickbooks/callback?code=...&state=...&realmId=...
       │
       ├── success → 302 → QUICKBOOKS_OAUTH_SUCCESS_URL?status=connected&connected=1&realm_id=...
       └── failure → 302 → QUICKBOOKS_OAUTH_ERROR_URL?status=error
       │
       ▼
[SPA settings page reads query params, shows toast, refreshes status]
```

### FE implementation

#### Step A — Connect button

```ts
// Pseudocode
const { data } = await api.get('/integrations/quickbooks/connect-url');
// data.authorization_url, data.state (state is for backend only; do not store in localStorage for security)
window.location.href = data.authorization_url;
```

- Use **full-page navigation** (not a popup) unless you also handle popup `postMessage` back to parent — backend redirect targets the configured admin URL, not `window.opener`.
- **Do not** register the SPA URL in Intuit as redirect URI. Only the API callback URL:

  `https://<api-host>/api/v1/integrations/quickbooks/callback`

#### Step B — Settings page after redirect

Route example: `/settings/integrations/quickbooks`  
Must match backend `QUICKBOOKS_OAUTH_SUCCESS_URL` / `QUICKBOOKS_OAUTH_ERROR_URL` host (allowlisted).

On mount:

```ts
const params = new URLSearchParams(window.location.search);
const status = params.get('status');

if (status === 'connected' || params.get('connected') === '1') {
  toast.success('QuickBooks connected');
  const realmId = params.get('realm_id'); // optional display
  // strip query params from URL (replaceState) so refresh does not re-toast
  router.replace('/settings/integrations/quickbooks');
  await refetchStatus();
}

if (status === 'error') {
  toast.error('QuickBooks connection failed');
  router.replace('/settings/integrations/quickbooks');
}
```

**Security:** The redirect never includes OAuth `code` or `state` — only `status`, `connected`, `realm_id`. Do not expect or log tokens on the FE.

#### Step C — Connection status card

```ts
GET /integrations/quickbooks/status
```

Response fields to show:

| Field | UI |
|-------|-----|
| `connected` | Connected / Not connected badge |
| `realm_id` | Company ID (when connected) |
| `connection_status` | e.g. active / revoked |
| `expires_at` | Token expiry warning |
| `failed_syncs` | Counter → link to failures list |
| `last_error` / `last_error_at` | Inline alert |

#### Step D — Disconnect

```ts
POST /integrations/quickbooks/disconnect
```

Requires `QUICKBOOKS` WRITE. Refresh status after success.

### Dev / staging env (admin `.env`)

Align with API `.env.example`:

```env
QUICKBOOKS_OAUTH_SUCCESS_URL=http://localhost:5173/settings/integrations/quickbooks
QUICKBOOKS_OAUTH_ERROR_URL=http://localhost:5173/settings/integrations/quickbooks
```

Admin host must appear in `QUICKBOOKS_OAUTH_REDIRECT_ALLOWED_HOSTS` on the API.

### Optional: JSON callback (debug only)

`GET .../callback?...&format=json` returns JSON instead of redirect — **not for production UX**; Intuit will not send users there with `format=json`.

---

## 3. QuickBooks integrations page (existing + enhancements)

### Manual entity sync (unchanged API)

```http
POST /integrations/quickbooks/customers/{customerId}/sync
POST /integrations/quickbooks/invoices/{invoiceId}/sync
POST /integrations/quickbooks/credit-notes/{creditNoteId}/sync
POST /integrations/quickbooks/payments/{paymentId}/sync
Body: { "force": false }
```

Response `QuickBooksSyncResult`:

```json
{
  "queued": true,
  "job_id": "qb:invoice:...",
  "entity_type": "invoice",
  "local_entity_id": "...",
  "sync_status": "QUEUED"
}
```

**UI:**

- If `queued: false` → show “Already queued or deduplicated” (ARQ job id collision), not an error.
- Poll entity `qb_sync_status` on invoice/CN/payment row, or open sync log filtered by `local_entity_id`.

### Sync health / reconcile

```http
GET /integrations/quickbooks/sync-health
GET /integrations/quickbooks/reconcile
```

Use for dashboard widgets (failed counts, missing links).

### Sync settings

```http
GET  /integrations/quickbooks/settings
PATCH /integrations/quickbooks/settings
```

Form fields: `strict_mapping_mode`, `sync_attachments`, `auto_retry_enabled`, `max_retry_attempts`, `retry_backoff_seconds`, `allow_force_reapply_credit`.

### Mappings CRUD

Unchanged: `GET /mappings`, `PUT /mappings/{type}/{localKey}`, `DELETE ...`.

---

## 4. Sync failures / logs UI (important updates)

### List failures

```http
GET /integrations/quickbooks/failures?status=FAILED&status=PENDING&limit=100
```

Query params:

| Param | Notes |
|-------|--------|
| `status` | Repeat for multiple: `FAILED`, `PENDING`, `SYNCED`, `SKIPPED` |
| `entity_type` | `customer`, `invoice`, `credit_note`, `payment` |
| `local_entity_id` | Filter saga for one CN / invoice |
| `job_id` | Partial match via `search` also covers job id |
| `period` | `TODAY`, `LAST_7_DAYS`, `LAST_30_DAYS`, etc. |
| `date_from` / `date_to` | Custom range (max 90 days) |

**FE changes:**

1. Add **PENDING** tab or include PENDING in default filter — void chain and enqueues now create PENDING rows immediately.
2. Treat `event_type` as **string** in UI — OpenAPI enum may lag. New values include:
   - `CREDIT_NOTE_VOID_QUEUED`
   - `CREDIT_NOTE_VOID_CHAIN_QUEUED`
   - `CREDIT_NOTE_VOID_CHAIN_STEP`
   - `PAYMENT_SYNC_SKIPPED`
3. Show status badge: PENDING (amber), SYNCED (green), FAILED (red), SKIPPED (grey).

### Failure detail

```http
GET /integrations/quickbooks/failures/{logId}
```

Use `payload` (detail only):

```ts
type SyncLogPayload = {
  correlation_id?: string;      // e.g. qb:void-cn:...:cnId:v2
  trigger_source?: string;    // billing.void_credit_note, billing.void_credit_note_reversal, ...
  step?: string;              // reversal_invoice_sync, void_credit_memo, chain_failed
  business?: {
    credit_note_number?: string;
    reversal_invoice_id?: string;
    affected_invoice_ids?: string[];
    void_reason?: string;
    applied_total?: string;
  };
  enqueue?: { job_name?: string; queued?: boolean };
  reason?: string;            // skip reasons
};
```

**Support UX:** On credit note void, add “View QB sync trail” linking to failures list with `local_entity_id={creditNoteId}` or search `correlation_id` in payload (client-side filter until API adds `correlation_id` query — planned).

### Bulk resync

Unchanged:

```http
POST /integrations/quickbooks/resync/bulk
POST /integrations/quickbooks/resync/{entityType}/{localEntityId}
```

Idempotency: `X-Idempotency-Key` header on bulk endpoints.

---

## 5. Void credit note (billing UI)

### API

```http
POST /api/v1/billing/credit-notes/{creditNoteId}/void?organization_id={orgId}
Content-Type: application/json

{ "reason": "Customer requested reversal" }
```

- **ACL:** `BILLING` WRITE (admin)
- **`reason`:** required, 1–2000 chars
- **Idempotent:** already voided → 200 with `status: "VOID"`

### Response (`CreditNoteDetailResponse`)

New fields:

```ts
reversal_invoice_id?: string | null;
reversal_invoice_number?: string | null;
qb_sync_status?: string | null;  // often QUEUED after void
status: 'VOID' | 'OPEN' | 'PARTIALLY_APPLIED' | 'FULLY_APPLIED';
```

### UX spec

```text
┌─────────────────────────────────────────┐
│ Void credit note CN-2026-00004          │
├─────────────────────────────────────────┤
│ Applied: £340.00 to 2 invoice(s)        │  ← if applied_total > 0
│ This will:                              │
│  • Create a reversal invoice            │
│  • Void the credit note in SW            │
│  • Queue QuickBooks sync (background)   │
│                                         │
│ Reason * [________________________]     │
│                                         │
│ [Cancel]  [Void credit note]            │
└─────────────────────────────────────────┘
```

**After success:**

| Case | Show |
|------|------|
| Unapplied | Toast + refresh list; status VOID |
| Applied | Toast + link “Reversal invoice {reversal_invoice_number}” → invoice detail |
| QB | Badge `qb_sync_status` QUEUED → optional link to QB failures filtered by CN id |

**Do not:**

- Call `POST /integrations/quickbooks/credit-notes/{id}/sync` for void — backend queues void chain automatically.
- Allow void without reason.
- Hide reversal invoice — finance needs the line item on the reversal invoice in SW/QBO.

### QB background saga (for loading states)

Applied void order:

1. Reversal invoice synced (`billing.void_credit_note_reversal`)
2. Each affected invoice re-synced
3. Credit memo voided in QBO

FE can show a single “QuickBooks sync in progress” on CN detail until `qb_sync_status` is `SYNCED` or failures list shows FAILED for that `local_entity_id`.

---

## 6. Billing overview tab (org profile)

Not under QuickBooks routes — separate org billing dashboard.

```http
GET /organizations/{organizationId}/billing/overview?period=last_30_days&chart_year=2026
```

**ACL:** `BILLING` READ + org profile access.

**Period values:** `today`, `yesterday`, `last_7_days`, `last_30_days`

**Response shape:**

```ts
{
  meta: { period_start, period_end, prior_*, timezone, definitions_version, chart_year },
  kpis: {
    total_billed: { value, change_pct, comparison_label },
    payments_received: { ... },
    outstanding_balance: { ... },
    overdue_amount: { ... },
    credit_notes_issued: { ... },
    refunds_issued: { ... },
  },
  charts: {
    revenue_trend: [{ month, revenue, refunds, net_revenue }],
    payment_method_usage: [{ method, amount, percent }],
    invoice_status: [{ status, count, total_value }],
    billing_activity: [{ month, invoices_amount, invoices_count, payments_amount, payments_count }],
  }
}
```

**UI notes:**

- Amounts are **strings** (decimal serialization) — use existing money formatter.
- `definitions_version` in meta — display in dev tools or “?” tooltip if KPIs look wrong after deploy.
- **total_billed** excludes voided/written-off invoices (backend rule).

---

## 7. Invoice create (admin)

```http
POST /api/v1/invoices
```

New body fields:

```ts
billing_contact_email?: string;  // max 255
line_items?: Array<{
  description: string;
  quantity: number;
  unit_price: string;
  total_price: string;
  line_type?: string;
}>;
finalize?: boolean;
```

Validation: if `line_items` present, sum of `total_price` must match `subtotal` (±0.02).

**FE:** Add optional billing contact field and line-item grid on create/finalize form.

---

## 8. Admin credit note PDF

Mirror B2B routes with admin scope:

```http
POST /billing/credit-notes/{id}/pdf?organization_id={orgId}
GET  /billing/credit-notes/{id}/pdf?organization_id={orgId}
POST /billing/credit-notes/{id}/pdf/signed-url?organization_id={orgId}
Body (signed-url): { "disposition": "inline" | "attachment" }
```

Poll `status` until `ready`, then open signed URL.

---

## 9. TypeScript / OpenAPI client updates

Regenerate API client from OpenAPI after backend deploy, then:

```ts
// Extend locally until schema published:
export type QuickBooksLogEventType =
  | /* existing OpenAPI union */
  | 'CREDIT_NOTE_VOID_QUEUED'
  | 'CREDIT_NOTE_VOID_CHAIN_QUEUED'
  | 'CREDIT_NOTE_VOID_CHAIN_STEP'
  | 'PAYMENT_SYNC_SKIPPED'
  | 'CREDIT_NOTE_VOIDED'
  | 'CREDIT_NOTE_VOID_SKIPPED';

export interface CreditNoteDetail {
  // ...existing
  reversal_invoice_id?: string | null;
  reversal_invoice_number?: string | null;
}
```

---

## 10. Error handling

| HTTP | When | FE |
|------|------|-----|
| 400 | Invalid void reason, date range on logs | Show field errors |
| 403 | Missing QUICKBOOKS / BILLING | Hide action or upsell permission |
| 404 | CN / invoice not in org scope | Not found |
| 409 | Invoice not draft on update | Conflict message |
| 302 | OAuth callback | Browser follows redirect — SPA route handles query |

---

## 11. E2E test checklist (QA)

### OAuth

- [ ] Connect redirects to Intuit and back to settings page with `status=connected`
- [ ] Error path shows `status=error` toast
- [ ] URL bar cleaned after handling params
- [ ] Disconnect clears status card

### Void credit note

- [ ] Unapplied void: status VOID, no reversal invoice
- [ ] Applied void: reversal invoice link visible, reason stored
- [ ] Void without reason → 400
- [ ] Double void → idempotent success
- [ ] QB failures show PENDING then SYNCED (or FAILED with message)

### Sync logs

- [ ] PENDING rows visible after void / manual sync
- [ ] Detail payload shows `correlation_id` and `business.void_reason`
- [ ] Bulk resync still works on FAILED rows

### Billing overview

- [ ] Period switcher changes KPIs
- [ ] Charts render with string amounts

---

## 12. What NOT to build on FE

| Anti-pattern | Why |
|--------------|-----|
| SPA as OAuth redirect URI | Intuit must hit API callback only |
| Parse OAuth `code` in SPA | Security; backend exchanges code |
| Per-organization QB connect | Backend uses global singleton namespace |
| Manual QB void after CN void | `void_credit_note` already enqueues chain |
| Separate PAYMENTS permission checks | Use `BILLING` only |
| Assume sync is synchronous | All QB work is queued; poll status/logs |

---

## 13. Quick reference — routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/integrations/quickbooks/connect-url` | Start OAuth |
| GET | `/integrations/quickbooks/callback` | Intuit only (redirect) |
| GET | `/integrations/quickbooks/status` | Connection card |
| POST | `/integrations/quickbooks/disconnect` | Disconnect |
| GET | `/integrations/quickbooks/failures` | Sync log list |
| GET | `/integrations/quickbooks/failures/{id}` | Log detail + payload |
| POST | `/billing/credit-notes/{id}/void` | Void CN (+ QB queue) |
| GET | `/organizations/{orgId}/billing/overview` | Overview dashboard |

---

## 14. Related backend docs

- [billing-overview-and-enhancements.md](./billing-overview-and-enhancements.md)
- [quickbooks-sync-logging.md](./quickbooks-sync-logging.md)
