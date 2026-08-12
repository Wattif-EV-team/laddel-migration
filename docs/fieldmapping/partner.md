# Field mapping — Partner (create)

> **Status:** Mapping updated — **grain changed to `laddel.customer`** (one partner per
> customer). `migration_project_code` is **no longer used** (it is being removed from the
> source `facility` table). Maps the **`laddel`** source onto the Ampeco **create partner**
> payload. Companion staging SQL lives in
> [partner_staging_sql.md](partner_staging_sql.md).

## Endpoint & payload source

- **API (source of truth):** `POST /public-api/resources/partners/v2.0`
  (`operationId: partnerCreate`, schema `Partner-create`).
- The older `POST .../v1.0` (`partnerCreateDeprecated`, schema `Partner_write`) is
  **deprecated** — do **not** target it.
- **Notes** are a **separate resource** —
  `POST /public-api/resources/partners/v2.0/{partner}/notes` — and need their own target
  view + migration script (see [Deferred](#deferred--separate-work) below).

## Scope

- **Grain: one partner per `laddel.customer`.**
  `mapping_key = 'Laddel|Customer|' || c.customer_id`.
- **In scope:** every customer linked — via `facility_contact` → `facility` →
  `organization` — to **any** organization flagged `migration_status = 'READY'`. No
  `priceModel` / `migration_project_code` filter is applied (this matches the SiteTracker
  Account scope in [sitetracker_account.md](sitetracker_account.md)).
- The organization is joined via a `GROUP BY c.customer_id` derived table, so the view
  yields exactly **one row per customer** (customer → organization is many-to-one in the
  source; no fan-out).
- **Deferred (not first batch):**
  - **Corporate-billing** customers (linked via `laddel.ev_fleet_contact`, not
    `facility_contact`) → corporate-billing partners.

See [partner_staging_sql.md](partner_staging_sql.md) for the `migration_status` staging
updates (the `migration_project_code` staging is obsolete for this view).

## Source query

```sql
FROM  laddel.customer c
JOIN  (
        SELECT fc.customer_id,
               MIN(o.organization_name) AS organization_name
        FROM   laddel.facility_contact fc
        JOIN   laddel.facility     f ON f.facility_id     = fc.facility_id
        JOIN   laddel.organization o ON o.organization_id = f.organization_id
        WHERE  o.migration_status = 'READY'
        GROUP  BY fc.customer_id
      ) org ON org.customer_id = c.customer_id
```

- The derived table both **gates scope** (customer linked to a `READY` org) and supplies
  the single `organization_name` per customer without fan-out.
- `migration_project_code` is **not** referenced anywhere (it is being removed from the
  source `facility` table).

## Field mapping

Legend — **Default** = constant we emit; **`c./f./o./pi.`** = source column;
*(omit)* = field deliberately left out of the payload.

### Top-level / identity

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `name` | string | **yes** | `c.name` | Company name (NOT NULL in source). |
| `businessName` | string | no | `CASE WHEN org_name = c.name THEN org_name ELSE org_name \|\| ' [' \|\| c.name \|\| ']' END` | Organization name, with the customer name appended in brackets when they differ, e.g. `Org Name [Customer Name]`. |
| `externalId` | string | no | `NULL` | Emitted as a static `NULL`; the step prunes it, so no `externalId` is sent. |
| `regNo` | string | no | `COALESCE(REPLACE(TRIM(c.organization_number), ' ', ''), '')` | Strip spaces & trim; **blank `''` when NULL**. |
| `vatNo` | string | no | `CASE WHEN c.vat_registered THEN REPLACE(TRIM(c.organization_number), ' ', '') || 'MVA' ELSE '' END` | Always includes the text `MVA` when `vat_registered = 1`; **blank `''` when `vat_registered = 0`**. |

### Address

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `address` | string | no | `TRIM(c.address)` | Copied as-is, trimmed. |
| `postcode` | string | no | `TRIM(c.postal_code)` | |
| `city` | string | no | `TRIM(c.city)` | |
| `country` | string | no | `'NO'` (Default) | All source data is Norwegian. |
| `region` | string | no | *(omit)* | Not used for country `NO`. |
| `state` | string | no | *(omit)* | Only for US/AU/CA/UM/RO. |

### Contact details

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `contactDetails.administrative.contactPerson` | string | no | `c.name` | |
| `contactDetails.administrative.email` | string | no | `c.email` | |
| `contactDetails.administrative.phone` | string | no | `REPLACE(TRIM(c.phone), ' ', '')` | Strip spaces & trim (source e.g. `69 27 70 50`). |
| `contactDetails.technical.contactPerson` | string | no | *(omit)* | No source data. |
| `contactDetails.technical.email` | string | no | *(omit)* | No source data. |
| `contactDetails.technical.phone` | string | no | *(omit)* | No source data. |
| `contactDetails.billing.contactPerson` | string | no | `COALESCE(NULLIF(TRIM(c.invoice_reference), ''), 'Elbil-lading')` | Fallback to `Elbil-lading` when null/empty. |
| `contactDetails.billing.email` | string | no | `COALESCE(NULLIF(TRIM(c.invoice_email), ''), c.email)` | Fallback to `c.email` when null/empty. |
| `contactDetails.billing.phone` | string | no | *(omit)* | No source data. |

### Fees & numbering

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `monthlyPlatformFee` | number | no | `0` (Default) | Zero for all in first batch. |
| `receiptsPrefix` | string | no | `'L' \|\| LPAD(c.customer_id, 4, '0') \|\| '-'` | Customer-derived prefix, e.g. `L0123-`; effective because `options.supplierOnReceipts = true`. **TODO:** replace placeholder scheme with the final prefix. |
| `receiptsStartingNumber` | string | no | **(omit — never send)** | ⚠️ **Do not include** in create **or** update payloads — would reset the receipt counter if receipts already generated. |
| `invoiceNumberPrefix` | string | no | `'L' \|\| LPAD(c.customer_id, 4, '0') \|\| '-'` | Customer-derived prefix, e.g. `L0123-`; non-blank + globally unique (API requires `minLength: 1` and uniqueness). **TODO:** replace placeholder scheme with the final prefix. |
| `startingInvoiceNumber` | string | no | *(omit)* | Invoices not in use yet. |

### Options

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `options.userVisibility` | enum | no | `'all'` (Default) | |
| `options.allowViewingUsersWhoAcceptedInvite` | boolean | no | *(omit)* | **Deprecated** — superseded by `userVisibility`. |
| `options.allowViewingAllSessionsOfInvitedUsers` | boolean | no | `false` (Default) | |
| `options.createUsers` | boolean | no | `false` (Default) | |
| `options.addUserBalance` | boolean | no | `false` (Default) | |
| `options.supplierOnReceipts` | boolean | no | `true` (Default) | MDU business model is always customer-owned. |
| `options.supplierOnInvoices` | boolean | no | `true` (Default) | |
| `options.allowToControlTariffs` | boolean | no | `true` (Default) | |
| `options.allowToControlTariffGroups` | boolean | no | `true` (Default) | |
| `options.allowToControlCpConfigurations` | boolean | no | `false` (Default) | |
| `options.settlementReportBreakdown` | enum | no | `'by_location_and_partner_contract'` (Default) | ⚠️ Confirm this is a valid enum value in the API. |

### Corporate billing

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `corporateBilling.enabled` | boolean | no | `false` (Default) | Disabled in first batch (corporate billing deferred). |
| `corporateBilling.monthlyLimit` | number | no | *(omit)* | Deprecated. |
| `corporateBilling.frequency` | enum | no | *(omit)* | |
| `corporateBilling.limit` | number | no | *(omit)* | |
| `corporateBilling.discount` | number | no | *(omit)* | |

### Notifications

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `notifications.technical.chargePointFaults` | boolean | no | `false` (Default) | |
| `notifications.billing.settlementReports` | boolean | no | `false` (Default) | |
| `notifications.billing.settlementReportLanguage` | string (IETF tag) | no | `'nb-NO'` (Default) | |

### Bank details

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `bankDetails.bankIban` | string | no | *(omit)* | No source. |
| `bankDetails.bankName` | string | no | *(omit)* | No source. |
| `bankDetails.bankAddress` | string | no | *(omit)* | No source. |
| `bankDetails.bankCode` | string | no | `REGEXP_REPLACE(TRIM(c.KID), '[^0-9]', '')` | **Repurposed field (sic):** holds the KID. Normalise — trim, remove spaces, digits only. |
| `bankDetails.bankAccountNumber` | string | no | `REGEXP_REPLACE(TRIM(c.account_number), '[^0-9]', '')` | Normalise — trim, remove spaces, digits only. |

## Deferred / separate work

- **Partner notes** — `c.note` maps to the **notes** endpoint
  `POST /public-api/resources/partners/v2.0/{partner}/notes` (fields: `summary`,
  `details`, `pinned`). Needs its own target view + migration script (depends on the
  partner's target id). Out of scope for the partner create view.
- **Sub-operators** — organizations with multiple customers.
- **Corporate-billing partners** — customers linked via `laddel.ev_fleet_contact`.

## Open questions / decisions

| # | Question | Status |
|---|---|---|
| Q1 | **`businessName` format** — `organization_name`, with the customer name appended in brackets when different, e.g. `Org Name [Customer Name]` (superseded: no longer uses project code). | ✅ Resolved |
| Q2 | **Project-code scope** — obsolete: `migration_project_code` is no longer used by this view (grain moved to `customer`). | ⚪ Obsolete |
| Q3 | **`settlementReportBreakdown`** = `by_location_and_partner_contract`. | ✅ Resolved (confirmed valid) |
| Q4 | **`regNo` / `vatNo`** — `regNo` blank `''` when NULL; `vatNo` = `<orgno>MVA` when `vat_registered = 1`, blank `''` when `0`. | ✅ Resolved |
| Q5 | **Phone normalisation** — strip spaces & trim. | ✅ Resolved |
| Q6 | **`bankCode` = KID** — repurposed mapping confirmed. | ✅ Resolved |
| Q7 | **Notes endpoint** — separate notes view/script. | ⏸️ Deferred |

### Resolved (this revision)

- Grain changed **customer → facility**.
- First batch restricted to **MDU / `SUBSCRIPTION`** facilities in READY orgs.
- `externalId` = `migration_project_code`; `receiptsPrefix` = `migration_project_code`.
- `businessName` = `organization_name [project_code]`.
- `regNo` blank `''` when NULL; `vatNo` = `<orgno>MVA` when `vat_registered = 1`, blank when `0`.
- `address` trimmed as-is; `phone` stripped of spaces & trimmed.
- `contactDetails` mappings + fallbacks (`Elbil-lading`, `c.email`).
- `monthlyPlatformFee` = 0; `receiptsStartingNumber` omitted always.
- All `options`, `notifications`, `corporateBilling.enabled = false`,
  `settlementReportLanguage = nb-NO` fixed; `settlementReportBreakdown =
  by_location_and_partner_contract`.
- `bankCode` = KID, `bankAccountNumber` = `account_number` (digits-only).
