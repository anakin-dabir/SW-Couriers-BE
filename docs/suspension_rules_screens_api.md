# Suspension rules — screens and APIs

Base path: `{API_PREFIX}/v1/suspension-rules` (e.g. `/api/v1/suspension-rules`).

All endpoints require **ADMIN** auth (`Bearer` + `X-Client-Type: ADMIN`).  
Client B2B settings use the **same ADMIN identity**, scoped by `org_id` in URLs—not customer JWTs.

**Namespace:** Use **`/v1/suspension-rules/...`** only for rule-engine reads/writes below. Per-org effective/inventory **GET** mirrors under **`/v1/organizations/{org_id}/suspension-rules`** are **not** exposed—avoid relying on them.

---

## Rule kinds

There is no backend “kind” enum—derive from **`SuspensionRuleSetResponse`**:

**DEFAULT:**  
`is_default_rule === true` → GLOBAL template (`scope_type: GLOBAL`).

**CUSTOMISED:**  
`is_customised_rule === true` → ORG row linked to a global (`global_rule_set_id` / parent).

**NEW:**  
`is_new_rule === true` → ORG-only rule with no global parent.

Also use **`scope_type`** (`GLOBAL` \| `ORG`) and **`scope_org_id`** so the UI blocks wrong-screen deletes/edits.

---

## Screen A — platform admin settings

List globals:  
`GET /rule-sets?scope_type=GLOBAL` — optional `rule_type`, `status`, pagination

Create global:  
`POST /rule-sets` — `scope_type: GLOBAL`, `scope_org_id: null`, conditions

Get rule:  
`GET /rule-sets/{rule_set_id}`

Update:  
`PATCH /rule-sets/{rule_set_id}` — optional `version` (optimistic locking)

Delete:  
`DELETE /rule-sets/{rule_set_id}`

Audit:  
`GET /activity`

Supporting (broader admin):  
`POST /risk-events`

---

## Screen B — client B2B settings

### Load rules

**Evaluation (matches runtime / scheduled job — ACTIVE overlay only):**  
`GET /effective-rule-sets/{org_id}` — optional query `rule_type`

**Inventory (ACTIVE + INACTIVE rows for admin UI):**  
`GET /orgs/{org_id}/applicable-rule-sets` — optional `rule_type`; each item includes **`is_effective_for_org`**.  
**DEFAULT rows are omitted** when an **ACTIVE CUSTOMISED** org rule exists for that global parent—the customised row represents that template. After **restore-default**, the customised row is deleted and the DEFAULT template row returns.

Optional filtered list (ORG templates only):  
`GET /rule-sets?scope_type=ORG&scope_org_id={org_id}`

---

### DEFAULT rules

- Read-only on Screen B for lifecycle; manage globals in Screen A.
- Do **not** DELETE globals from Screen B.
- Toggle GLOBAL **ACTIVE/INACTIVE** only via Screen A: `PATCH /rule-sets/{rule_set_id}`.

---

### CUSTOMISED rules

Customise:  
`POST /orgs/{org_id}/rule-sets/{global_rule_set_id}/customise`

Edit:  
`PATCH /rule-sets/{rule_set_id}` — ORG row id

Toggle status:  
`PATCH /orgs/{org_id}/rule-sets/{rule_set_id}/status`

Restore:  
`POST /orgs/{org_id}/rule-sets/{rule_set_id}/restore-default`

Use **`can_restore_default`** where relevant.

---

### NEW rules

Create:  
`POST /rule-sets` — `scope_type: ORG`, `scope_org_id: {org_id}`

Edit:  
`PATCH /rule-sets/{rule_set_id}`

Toggle status:  
`PATCH /orgs/{org_id}/rule-sets/{rule_set_id}/status`

Delete:  
`DELETE /rule-sets/{rule_set_id}` — ORG ids only (`scope_type === ORG`, `scope_org_id === org_id`)

---

### Optional — global suppression (no CUSTOMISED clone)

Opt-out removes that GLOBAL id from **effective** DEFAULT rows for this org only (shared GLOBAL row unchanged).

List: `GET /orgs/{org_id}/global-rule-suppressions`  
Toggle: `PUT /orgs/{org_id}/global-rule-sets/{global_rule_set_id}/suppression` — `{ "suppressed": true | false }`

`global_rule_set_id` must reference a **GLOBAL** rule set.

---

### Optional — legacy upsert

`PUT /orgs/{org_id}/rule-types/{rule_type}/override` — prefer explicit customise / NEW flows when building richer UX.

---

## Delete policy

**Screen A**

- May delete GLOBAL templates (`DELETE /rule-sets/{id}`).
- Prefer not deleting ORG rows here—do org edits on Screen B.

**Screen B**

- Do **not** delete GLOBAL ids.
- May delete **ORG** rows only (`scope_type === ORG`, `scope_org_id === org_id`).

The API does not enforce screen context—guard with **`scope_type`** / **`scope_org_id`** in the UI.

---

## Status rules

**ORG** (CUSTOMISED / NEW):  
`PATCH /orgs/{org_id}/rule-sets/{rule_set_id}/status` — `{ "status": "ACTIVE" | "INACTIVE", "version"? }`

**GLOBAL:**  
`PATCH /rule-sets/{rule_set_id}` — Screen A only

---

## Semantics note

Effective resolution uses **ACTIVE** rows only. **restore-default** deletes the customised ORG row so the linked GLOBAL default applies again on both **effective** and **applicable** list endpoints.

---

## Checklist

- Screen B primary data uses **`GET /effective-rule-sets/{org_id}`** (badges: `is_default_rule` / `is_customised_rule` / `is_new_rule`).
- **`GET …/applicable-rule-sets`** lists the same inventory shape including INACTIVE GLOBAL/NEW rows where relevant; after **restore-default**, the deleted customised row no longer appears.
- Screen B does not **DELETE** GLOBAL templates.
- Restore customised behaviour with **`POST …/restore-default`**, not DELETE on the global row.
- Global edits and GLOBAL status only via Screen A (`PATCH /rule-sets/{rule_set_id}`).
- Optional suppression via **`GET/PUT …/global-rule-suppressions`** when product needs opt-out without a CUSTOMISED row.
