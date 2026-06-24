# Field mapping — Partner (create)

> **Status:** Mapping **locked** for the **first batch (MDU)** — all review questions
> (Q1–Q7) resolved (Q7 deferred). Maps the **`laddel`** source onto the Ampeco **create
> partner** payload. Companion staging SQL lives in
> [partner_staging_sql.md](partner_staging_sql.md).

## Endpoint & payload source

- **API (source of truth):** `POST /public-api/resources/partners/v2.0`
  (`operationId: partnerCreate`, schema `Partner-create`).
- The older `POST .../v1.0` (`partnerCreateDeprecated`, schema `Partner_write`) is
  **deprecated** — do **not** target it.
- **Notes** are a **separate resource** —
  `POST /public-api/resources/partners/v2.0/{partner}/notes` — and need their own target
  view + migration script (see [Deferred](#deferred--separate-work) below).

## Scope — first batch (MDU)

- **Grain: one partner per `facility`.** `mapping_key = 'Laddel|Partner|' || f.facility_id`.
- First batch = **MDU** (housing cooperatives): facilities with
  `priceModel = 'SUBSCRIPTION'`, in organizations flagged `migration_status = 'READY'`
  and carrying a `migration_project_code`.
- **Deferred (not first batch):**
  - Organizations with **multiple customers** → modelled as **sub-operator** resources.
  - **Corporate-billing** customers (linked via `laddel.ev_fleet_contact`, not
    `facility_contact`) → corporate-billing partners.

See [partner_staging_sql.md](partner_staging_sql.md) for the `migration_status` and
`migration_project_code` staging updates.

## Source query

```sql
FROM       laddel.facility            f
JOIN       laddel.organization        o  ON o.organization_id = f.organization_id
JOIN       laddel.facility_contact    fc ON fc.facility_id    = f.facility_id
JOIN       laddel.customer            c  ON c.customer_id     = fc.customer_id
LEFT JOIN  laddel.facility_information fi ON fi.facility_id    = f.facility_id
LEFT JOIN  laddel.price_information    pi ON pi.price_id       = fi.price_id
WHERE  o.migration_status       = 'READY'
  AND  f.migration_project_code IS NOT NULL
  AND  pi.priceModel            = 'SUBSCRIPTION'
```

- Every facility in `facility_contact` has **exactly one** customer (verified), so the
  grain resolves cleanly to a single contact per partner.
- `f.migration_project_code` (format `W047L####`) is the **project code** used for
  `externalId`, `receiptsPrefix`, and `businessName`.

## Field mapping

Legend — **Default** = constant we emit; **`c./f./o./pi.`** = source column;
*(omit)* = field deliberately left out of the payload.

### Top-level / identity

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `name` | string | **yes** | `c.name` | Company name (NOT NULL in source). |
| `businessName` | string | no | `o.organization_name || ' [' || f.migration_project_code || ']'` | Name shown to EV drivers/admins, e.g. `Org Name [W047L0001]`. |
| `externalId` | string | no | `f.migration_project_code` | Project code, e.g. `W047L0001`. |
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
| `receiptsPrefix` | string | no | `f.migration_project_code` | Project code, effective because `options.supplierOnReceipts = true`. |
| `receiptsStartingNumber` | string | no | **(omit — never send)** | ⚠️ **Do not include** in create **or** update payloads — would reset the receipt counter if receipts already generated. |
| `invoiceNumberPrefix` | string | no | *(omit)* | Invoices not in use yet. |
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
| Q1 | **`businessName` format** — `organization_name + ' [' + project_code + ']'`, e.g. `Org Name [W047L0001]`. | ✅ Resolved |
| Q2 | **Project-code scope** — assign codes to *all* facilities in a READY org (including the 22 mixed orgs' non-`SUBSCRIPTION` facilities). | ✅ Resolved (no restriction) |
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
