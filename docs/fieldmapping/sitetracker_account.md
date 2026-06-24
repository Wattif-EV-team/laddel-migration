# Field mapping — SiteTracker Account (create)

> **Status:** 🟢 **Mapping agreed (first batch)** — grain, scope and all review questions
> resolved. Maps the **`laddel`** source onto the SiteTracker (Salesforce) **`Account`**
> sObject ("Company"). Re-verify every field against a live `describe` of the **laddel**
> SiteTracker org before building — the field list below comes from the **Wattif** sandbox
> (see [sitetracker-reference.md](../sitetracker-reference.md) and
> [reference/README.md](../../reference/README.md)) and may differ.

## Endpoint & payload source

- **sObject:** `Account` (standard Salesforce object, used as "Company").
- **Create:** `POST /services/data/vXX.0/sobjects/Account/` → `201` `{ "id": "..." }`.
- **Update:** `PATCH /services/data/vXX.0/sobjects/Account/{id}` → `204`.
- **No lookup-before-create.** Idempotency is by `mapping_key` only (see
  [Grain & idempotency](#grain--idempotency)): a populated `target_sf_account_id` ⇒ update,
  `NULL` ⇒ create. We do **not** SOQL-match pre-existing Accounts.
- Authoritative field metadata: the `sitetracker_describe_Account.json` snapshot in
  [reference/projectsaturn/research-test/sitetracker](../../reference/projectsaturn/research-test/sitetracker/).
- Reference implementation (re-implement, do **not** import):
  [CreateOrUpdateSiteTrackerAccounts.py](../../reference/projectsaturn/CreateOrUpdateSiteTrackerAccounts.py)
  and view [322_target_sitetracker_accounts.sql](../../reference/projectsaturn/db/project-sleet/322_target_sitetracker_accounts.sql).

## Grain & idempotency

- **Grain: one Account per `laddel.customer`.** Each customer row becomes exactly one
  SiteTracker Account — no collapsing by org number.
  `mapping_key = 'Laddel|SiteTrackerAccount|' || c.customer_id`.
- **Idempotency: `mapping_key` only — no SOQL lookup.** The target view exposes
  `target_sf_account_id` via the mapping table; `NULL` ⇒ create, otherwise update. The script
  does **not** try to match pre-existing Accounts in SiteTracker, so it can create a duplicate
  of a record it didn't create — acceptable per the agreed approach.
- `Business_Registration_Number__c` is still emitted as a **payload field** (normalised org
  number) but is **no longer** a match key. It is blank for private individuals.

## Scope

Primary source tables are the **same as Partner** ([partner.md](partner.md)):
`customer → facility_contact → facility → organization`.

- **Batch filter: `organization.migration_status = 'READY'`** (status lives on the
  **organization**). A customer is in scope if it is linked — via `facility_contact` →
  `facility` — to **any** organization flagged `READY`.
- **Migrate all such customers**, including those **without** an org number (4 in scope are
  private individuals). No `priceModel` / `migration_project_code` filter is applied.
- Within scope there are **425 distinct customers**.

### Source query

```sql
SELECT DISTINCT
    c.customer_id,
    c.name, c.organization_number,
    c.address, c.postal_code, c.city,
    c.email, c.phone
FROM      laddel.customer         c
JOIN      laddel.facility_contact fc ON fc.customer_id    = c.customer_id
JOIN      laddel.facility         f  ON f.facility_id     = fc.facility_id
JOIN      laddel.organization     o  ON o.organization_id = f.organization_id
WHERE  o.migration_status = 'READY'
```

> A customer linked to facilities in several `READY` organizations still yields a **single**
> Account (grain = `customer_id`); `DISTINCT` / `GROUP BY c.customer_id` deduplicates.

## Field mapping

Legend — **Default** = constant we emit; **`c.`** = `laddel.customer` column;
*(omit)* = field deliberately left out of the payload.

### Identity

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `Name` | string | **yes** | `TRIM(c.name)` | Company / customer name (NOT NULL in source). |
| `Business_Registration_Number__c` | string | no | `REGEXP_REPLACE(TRIM(c.organization_number), '[^0-9]', '')` | Normalised org number (digits only); blank for private individuals. Payload only — not a match key. |
| `Type` | picklist | no | `'Customer'` (Default) | Confirmed correct picklist value. |
| `Industry` | picklist | no | *(omit)* | No source. |

### Billing address

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `BillingStreet` | string | no | `TRIM(c.address)` | |
| `BillingCity` | string | no | `TRIM(c.city)` | |
| `BillingPostalCode` | string | no | `TRIM(c.postal_code)` | |
| `BillingCountry` | string | no | `'Norway'` (Default) | All source data is Norwegian. Verify expected format (`Norway` vs `Norway(NOR)`) against the live org; reference used plain `Norway`. |

> **Shipping address omitted** (Q5) — only the billing block is emitted.

### Contact

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `Email__c` | string | no | `LOWER(NULLIF(TRIM(c.email), ''))` | Custom field. Verify it exists in laddel's SiteTracker org before build. |
| `Phone` | string | no | `NULLIF(TRIM(c.phone), '')` | Standard `Account.Phone`. Often empty in source. |
| `Website` | string | no | *(omit)* | No source. |

### Fields with source data but no obvious Account target

These `laddel.customer` columns exist but don't map cleanly to a standard Account field —
listed so the reviewer can decide (likely belong on a different object, e.g. billing, or are
out of scope for SiteTracker):

| Source column | Notes |
|---|---|
| `c.account_number` | Bank account number — no SiteTracker Account field. Likely not migrated here. |
| `c.KID` | KID (payment reference). Billing concern, not Account. |
| `c.vat_registered` | Boolean; SiteTracker Account has no obvious VAT flag. |
| `c.invoice_email` / `c.invoice_reference` / `c.invoice_method` / `c.invoice_due_days` | Invoicing metadata — out of scope for the Account object. |
| `c.note` | Free text — could map to a description/notes field if one exists (verify). |
| `c.ttex_id` | Source-system id — internal, not migrated. |

## Decisions (resolved)

| # | Question | Decision |
|---|---|---|
| Q1 | **Batch scope** | Filter on **`organization.migration_status = 'READY'`** (status is org-level). |
| Q2 | **Customers without org number** | **Migrate all** customers in scope; do not exclude. Lookup-before-create dropped. |
| Q3 | **Org-number collision** (`926935356` → 11 names) | Moot — grain is now **per customer**, so no collapsing by org number. |
| Q4 | **`Type`** | `'Customer'`. ✅ |
| Q5 | **Shipping address** | **Omit** the `Shipping*` block. |
| Q6 | **`Email__c`** | Map `c.email` (custom field). ✅ Verify it exists in the live org. |
| Q7 | **`Phone`** | Include `Account.Phone` from `c.phone`. ✅ |
| Q8 | **Address source** | Use `customer.address / postal_code / city`. ✅ |
