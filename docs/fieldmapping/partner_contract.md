# Field mapping — Partner Contract (create)

> **Status:** ✅ **Implemented (2026-09-03).** Mapping decided Q1–Q10; target view
> [sql/306_target_partner_contracts.sql](../../sql/306_target_partner_contracts.sql), mapping
> table [sql/006_partner_contract_mapping.sql](../../sql/006_partner_contract_mapping.sql) and
> the `partner_contracts` step are all in place. Maps the **`laddel`** source onto the Ampeco
> **create partner contract** payload.

## Endpoint & payload source

- **API (source of truth):** `POST /public-api/resources/partner-contracts/v1.0`
  (`operationId: partnerContractCreate`, request schema `PartnerContract_write`).
- **Update:** `PATCH /public-api/resources/partner-contracts/v1.0/{partnerContract}`
  (`operationId: partnerContractPatch`, schema `PartnerContract_patch`) — out of scope here,
  noted for completeness.
- The YAML was parsed directly (`docs/ampeco-public-api.yaml`,
  `components.schemas.PartnerContract_write`) to get the authoritative field list — it is the
  source of truth, **not** `reference/projectsaturn`.

> ℹ️ **Compared with `reference/projectsaturn`'s `304_target_partnercontracts.sql` /
> `CreateOrUpdatePartnerContract.py`:**
> - The current schema has **no separate "payment facilitation" payload shape** — there is
>   still only one `revenueSharing` object, reused for both `contractType` values (exactly
>   as the reference project did). This confirms the reference's pattern still applies:
>   for `paymentFacilitation`, `partnerSharePercentageAcEvse`/`Dc` are forced to `100` and
>   `handlingFee` carries the operator's cut.
> - `accessAndPermissions` gained **two fields not present in the reference project**:
>   `createFromTemplate` and `changeSystemStatus` (🆕 — both confirmed `TRUE`, Q4).
> - There is **no `location`/`locationId` field on the contract itself** — unlike the
>   reference's Sleet model (one contract per location joined via `project_code`), the
>   Ampeco partner-contract resource here is not location-scoped in its payload. Grain is
>   decided at the source/business level (see below), not dictated by the API shape.
> - Field ordering below follows the YAML's declaration order.

## Scope & grain

- **Grain: one partner contract per `laddel.facility`.**
  `mapping_key = 'Laddel|Facility|' || f.facility_id` — same convention as
  [location.md](location.md) and [sitetracker_site.md](sitetracker_site.md).
- **Batch gate (✅ Q8 confirmed):** `organization.migration_status = 'READY'` **AND**
  `target.facility_migration_eligibility.should_not_migrate = 0` — identical gate to
  [304_target_location.sql](../../sql/304_target_location.sql).
  ⚠️ **Note the 201 refactor:** `facility_migration_eligibility` is now a **materialized
  table**, not a view; it absorbed the former `202_facility_external_id.sql`, and the
  column is now called **`project_code`** (was `external_id`). Join it as
  `` JOIN `target`.`facility_migration_eligibility` fme ON fme.facility_id = f.facility_id ``
  and read `fme.project_code` — do **not** re-derive the `W047L####` scheme locally.
- **Hard dependency: Partners must exist first.** `partnerId` is resolved via
  `facility → facility_contact → customer → target.partner_mapping.target_partner_id`.
  ⚠️ **The dependency is enforced in the STEP, not the view.** The view LEFT JOINs
  `facility_contact` / `customer` / `partner_mapping` so the whole in-scope batch stays
  **previewable** before the partners step has run — an unmigrated partner simply shows up
  as a `NULL` `partnerId`. The `partner_contracts` step's `skip_reason` hook holds those
  rows back (counted as `skipped`, never posted). This deliberately departs from
  [MigrationPatternGuide §5.2](../MigrationPatternGuide.md#52-target-id-section)'s legacy
  `WHERE pm.target_partner_id IS NOT NULL` gating, which makes the view unreadable while
  the dependency is outstanding.
- **Verified (2026-08-27):** of the facilities in scope (`READY` + eligible), **0** are
  missing a `facility_contact` row, so the `facility → facility_contact → customer` hop
  costs nothing *for this scope*. (Across **all** facilities, 105/5047 have no
  `facility_contact` — see repo memory / [partner.md](partner.md).) The joins are LEFT
  anyway, and `vat_registered` is `COALESCE`d to `0` in the handling-fee formula, so a
  missing contact degrades gracefully instead of dropping the row.
- **Current batch composition** (`READY` + eligible, re-verified live 2026-09-03 during
  implementation): **439** facilities — **418 `SUBSCRIPTION`**, **21 `MARKUP`**,
  **0 `COMMISSION`**. (An earlier count of 440/419 was taken against a slightly older
  eligibility snapshot.) `COMMISSION` exists in the source (7 facilities in total, all
  outside today's batch — the 4 hard-coded `W-WattifEV` facilities from
  [sitetracker_site.md](sitetracker_site.md) plus 3 more), and is still mapped below since
  it may enter scope later.

## Source tables

```
laddel.facility                f    -- facility_id (PK), facility_name,
                                    --   monthly_fee_per_active_charger_excl_vat
   └─ laddel.facility_information fi  ON fi.facility_id = f.facility_id     -- 1:1, price_id
         └─ laddel.price_information pi ON pi.price_id = fi.price_id       -- priceModel, markup,
                                                                            --   surChargeKeepModifier,
                                                                            --   subscription_monthly_fee_incl_vat
   └─ laddel.facility_contact    fc  ON fc.facility_id = f.facility_id     -- 1:1 (within scope)
         └─ laddel.customer      c   ON c.customer_id = fc.customer_id     -- vat_registered
               └─ target.partner_mapping pm ON pm.mapping_key
                     = 'Laddel|Customer|' || c.customer_id                 -- target_partner_id
laddel.organization             o    ON o.organization_id = f.organization_id  -- batch gate only
target.facility_migration_eligibility fme ON fme.facility_id = f.facility_id
                                    -- batch gate (should_not_migrate) + project_code
```

## Source column reference

| Table | Column | Type | Notes |
|---|---|---|---|
| `facility` | `facility_id` | int PK | grain / mapping key |
| `facility` | `facility_name` | varchar(255) | → `title` |
| `facility` | `monthly_fee_per_active_charger_excl_vat` | decimal | → `monthlyPlatformFees.perAcEvse`/`perDcEvse` and the `title` suffix. **Mostly `NULL`, not `0`** — verified: `NULL` for all 7 `COMMISSION` and all 511 `SUBSCRIPTION` rows; for `MARKUP`, 3919/4609 `NULL`, 1 zero, 689 positive. `COALESCE(..., 0)` required. |
| `facility_information` | `price_id` | int FK | join to `price_information` |
| `price_information` | `priceModel` | enum(`MARKUP`,`COMMISSION`,`SUBSCRIPTION`) | drives `contractType`, `partnerShare*` and `handlingFee`. (The DB enum value is **`COMMISSION`**, double-M.) |
| `price_information` | `markup` | decimal(5,3) | fraction, e.g. `0.100` = 10%. → `handlingFee` under `MARKUP`. |
| `price_information` | `surChargeKeepModifier` | decimal(5,3) | **the *partner's* keep**, as a fraction (default `1.000`). → `partnerSharePercentage*` under `COMMISSION`; the operator's commission shown in `title` is `1 − surChargeKeepModifier`. |
| `price_information` | `surCharge` | decimal(6,4) | **not used** — superseded by the Q6 decision (partner share comes from `surChargeKeepModifier`, `handlingFee` is `NULL`). |
| `price_information` | `subscription_monthly_fee_incl_vat` | decimal(10,2) | end-user monthly fee (incl. VAT). Used **only in `title`** — not a partner-contract payload value. |
| `customer` | `vat_registered` | tinyint(1) | drives `HostVatPercent` in the `MARKUP` handling-fee formula. Already used in [partner.md](partner.md) for `vatNo`. |
| `target.facility_migration_eligibility` | `project_code` | varchar(20) | `W047L####` — `title` prefix. Was `external_id` in the removed `202` file. |
| `target.partner_mapping` | `target_partner_id` | bigint | resolves `partnerId`; hard dependency. |

## Contract-model matrix

`contractType` is **not** constant — it is driven by `priceModel` (Q6 changed the earlier
"paymentFacilitation for all" instruction):

| `priceModel` | `contractType` | `partnerSharePercentageAcEvse`/`Dc` | `handlingFee` |
|---|---|---|---|
| `SUBSCRIPTION` | `paymentFacilitation` | `100` | `0` (Q7) |
| `MARKUP` | `paymentFacilitation` | `100` | formula below (Q5) |
| `COMMISSION` | **`revenueSharing`** | `pi.surChargeKeepModifier * 100` | `NULL` (Q6) |

## Field mapping

Legend — **Default** = constant we emit; **`f./fi./pi./c./fme./pm.`** = source column;
*(omit)* = field deliberately left out; ✅ = confirmed 2026-09-03; 🆕 = field not present in
`reference/projectsaturn`.

### Identity / top-level

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `title` | string | **yes** | ✅ `'{project_code} - {facility_name} ({price model summary})'` | See [Title format](#title-format) for the per-`priceModel` summary and the number-formatting rule. |
| `partnerId` | integer | **yes** | `pm.target_partner_id` (via `facility → facility_contact → customer → target.partner_mapping`) | Hard dependency — see [Scope & grain](#scope--grain). |
| `contractType` | enum(`revenueSharing`,`paymentFacilitation`) | no | ✅ per-`priceModel`, see [Contract-model matrix](#contract-model-matrix) | `paymentFacilitation` for `SUBSCRIPTION`/`MARKUP`; **`revenueSharing` for `COMMISSION`** (Q6). |
| `startDate` | string (date-time) | **yes** | ✅ `CURDATE()` (today, at build/run time) | Q2. The API notes the contract is effective from the **1st of the selected month**, so day-of-month is not significant. Matches the reference project's `CURRENT_DATE`. |
| `endDate` | string (date-time), nullable | no | `NULL` | Open-ended contracts; no source data. |
| `autoRenewal` | boolean | no | `TRUE` (Default) | Matches reference default. |
| `externalId` | string, nullable, ≤255 | no | ✅ `NULL` | Q3. Deliberately not populated — `project_code` already appears in `title`, and Location/Site carry the `W047L####` identifier. Same treatment as Partner's `externalId` ([partner.md](partner.md)). |

### Access and permissions

All boolean, all optional, all under `accessAndPermissions.*`. No source data — full-access
defaults, ✅ confirmed (Q4) including the two new fields.

| API field | Source / value | Notes |
|---|---|---|
| `accessAndPermissions.sessionsRemoteControl` | `TRUE` (Default) | Matches reference. |
| `accessAndPermissions.startReservation` | `TRUE` (Default) | Matches reference. |
| `accessAndPermissions.stopReservation` | `TRUE` (Default) | Matches reference. |
| `accessAndPermissions.resetChargePoint` | `TRUE` (Default) | Matches reference. |
| `accessAndPermissions.firmwareUpdate` | `TRUE` (Default) | Matches reference. |
| `accessAndPermissions.createFromTemplate` 🆕 | ✅ `TRUE` (Default) | New API field, not in reference. Confirmed Q4. |
| `accessAndPermissions.changeSystemStatus` 🆕 | ✅ `TRUE` (Default) | New API field, not in reference. Confirmed Q4. |

### Revenue sharing

Under `revenueSharing.*`. Used for **both** contract types (the API has no separate
payment-facilitation object).

| API field | Source / value | Notes |
|---|---|---|
| `revenueSharing.partnerSharePercentageAcEvse` | `CASE WHEN pi.priceModel = 'COMMISSION' THEN pi.surChargeKeepModifier * 100 ELSE 100 END` | `100` for `paymentFacilitation` (partner is supplier; operator's cut goes through `handlingFee`). For `COMMISSION`, the partner keeps `surChargeKeepModifier` (Q6). |
| `revenueSharing.partnerSharePercentageDcEvse` | *(same expression as AC)* | No AC/DC distinction in source. |
| `revenueSharing.excludeConnectionFee` | `FALSE` (Default) | No source signal; matches reference default. |
| `revenueSharing.deductElectricityCost` | `FALSE` (Default) | No source signal; matches reference default. |
| `revenueSharing.reimburseForElectricityCost` | `FALSE` (Default) | No source signal; matches reference default. |
| `revenueSharing.fixedFeePerSessionAc` | *(omit)* | No source data. |
| `revenueSharing.fixedFeePerSessionDc` | *(omit)* | No source data. |
| `revenueSharing.feePerKwhAc` | *(omit)* | No source data. |
| `revenueSharing.feePerKwhDc` | *(omit)* | No source data. |
| `revenueSharing.handlingFee` | per-`priceModel`, see [Handling fee](#handling-fee-derivation) | `0` for `SUBSCRIPTION`, formula for `MARKUP`, `NULL` for `COMMISSION`. |

### Monthly platform fees

Under `monthlyPlatformFees.*`.

| API field | Source / value | Notes |
|---|---|---|
| `monthlyPlatformFees.perChargePoint` | `0` (Default) | Not used — the source fee is per **active charger**, applied on the EVSE fields instead (same shape as the reference project). |
| `monthlyPlatformFees.perAcEvse` | `COALESCE(f.monthly_fee_per_active_charger_excl_vat, 0)` | Single source value applied to both AC and DC (no per-type breakdown in `laddel`). **Expected `0` for `SUBSCRIPTION`** — confirmed by data: the column is `NULL` (→ `0`) for **all** `SUBSCRIPTION` and **all** `COMMISSION` rows today. |
| `monthlyPlatformFees.perDcEvse` | `COALESCE(f.monthly_fee_per_active_charger_excl_vat, 0)` | Same value as `perAcEvse`. |

## Title format

✅ **Q1.** `'{project_code} - {facility_name} ({price model summary})'`, where
`project_code` comes from `target.facility_migration_eligibility.project_code` (the shared
`W047L####` scheme — **not** re-derived locally).

| `priceModel` | Summary | Example |
|---|---|---|
| `SUBSCRIPTION` | `Subscription {subscription_monthly_fee_incl_vat} NOK/user` | `W047L1238 - Al Grønnebakken Borettslag (Subscription 89 NOK/user)` |
| `MARKUP` | `Markup {markup × 100}%, {monthly_fee} NOK/evse` | `W047L1016 - Scandic Sunnfjord (Markup 10%, 69 NOK/evse)` |
| `COMMISSION` | `Commission {(1 − surChargeKeepModifier) × 100}%, {monthly_fee} NOK/evse` | `W047L0005 - Rosfjord Strandhotell (Commission 35%, 0 NOK/evse)` |

- `{monthly_fee}` = `COALESCE(f.monthly_fee_per_active_charger_excl_vat, 0)` (excl. VAT).
- The `COMMISSION` percentage is the **operator's** commission — i.e. the complement of
  `surChargeKeepModifier`, which is the *partner's* share. Verified against the examples:
  facility 5 has `surChargeKeepModifier = 0.650` → `Commission 35%`, while
  `partnerSharePercentage*` = `65`.
- All three examples were verified live against the source (facilities 5 / 1016 / 1238) and
  reproduce exactly.

> ⚠️ **Number-formatting trap.** The examples show `89`, `10`, `69` — no trailing decimals —
> but the source columns are `decimal(5,3)` / `decimal(10,2)`, so a naive `CAST(x AS CHAR)`
> yields `89.00` / `10.000`. Do **not** use
> `TRIM(TRAILING '0' FROM CAST(x AS CHAR))`: on `'10.000'` it strips *every* trailing zero
> and produces `'1'`. Use a whole-number check instead:
> ```sql
> IF(x = FLOOR(x),
>    CAST(CAST(x AS SIGNED) AS CHAR),          -- 10.000 -> '10', 89.00 -> '89'
>    TRIM(TRAILING '0' FROM CAST(x AS CHAR)))  -- 12.500 -> '12.5'
> ```

## Handling fee derivation

`revenueSharing.handlingFee` is a **percentage of the total amount paid by the end user for
the session** (API description), i.e. a `0–100` number.

### `priceModel = 'SUBSCRIPTION'` → `0`

✅ **Q7 confirmed.** The end user pays a flat `subscription_monthly_fee_incl_vat`, not a
per-session amount, so there is no session-percentage cut for the operator to take here.

### `priceModel = 'MARKUP'` → formula

✅ **Q5 confirmed.** With $M$ = `pi.markup` (fraction, e.g. `0.10`):

$$
\text{handlingFee} = \frac{M}{1 + V_{host} + M\,(1 + V_{fee})} \times 100
$$

- $V_{fee}$ = **handling-fee VAT — always `0.25`.**
- $V_{host}$ = **host VAT, from `customer.vat_registered`**: `1` → `0.25`, `0` → `0`.

SQL sketch:

```sql
ROUND(
    pi.markup
    / (1
       + CASE WHEN c.vat_registered = 1 THEN 0.25 ELSE 0 END
       + pi.markup * 1.25)
    * 100
, 4) AS `revenueSharing_handlingFee`
```

Worked values:

| `markup` | `vat_registered` | `handlingFee` |
|---|---|---|
| `0.100` | `0` | `8.8889` |
| `0.100` | `1` | `7.2727` |
| `0.150` | `0` | `12.2449` |
| `0.150` | `1` | `10.5263` |
| `0.000` | either | `0` |

**Live check (2026-09-03):** all **21** in-scope `MARKUP` facilities have
`markup = 0.100` and `vat_registered = 0` → **`8.8889%` for every one of them**. Across the
whole source, `MARKUP` splits as `markup = 0.000` (3839 facilities → `handlingFee = 0`),
`0.100` (709), `0.150` (97); 96 of those have no `facility_contact` (so `vat_registered` is
`NULL`) — all currently out of scope, but the view should `COALESCE(c.vat_registered, 0)`
or rely on the inner join / `partnerId IS NOT NULL` gate to exclude them.

### `priceModel = 'COMMISSION'` → `NULL`

✅ **Q6 confirmed.** No handling fee; the operator's cut is expressed as a
`revenueSharing` split instead (`partnerSharePercentage* = surChargeKeepModifier × 100`).
`price_information.surCharge` is therefore **not** used by this view.

## Open questions

| # | Question | Status |
|---|---|---|
| Q1 | **`title`** — `{project_code} - {facility_name} ({price model summary})`. | ✅ Resolved |
| Q2 | **`startDate`** — always today's date (`CURDATE()`). | ✅ Resolved |
| Q3 | **`externalId`** — `NULL`. | ✅ Resolved |
| Q4 | **`accessAndPermissions.createFromTemplate` / `.changeSystemStatus`** — both `TRUE`. | ✅ Resolved |
| Q5 | **`handlingFee` for `MARKUP`** — $M / (1 + V_{host} + M(1 + 0.25)) \times 100$, $V_{host}$ from `vat_registered`. | ✅ Resolved |
| Q6 | **`COMMISSION`** — `contractType = revenueSharing`, `partnerShare = surChargeKeepModifier`, `handlingFee = NULL`. | ✅ Resolved |
| Q7 | **`handlingFee` for `SUBSCRIPTION`** — `0`. | ✅ Resolved |
| Q8 | **Batch gate** — `READY` org + `facility_migration_eligibility.should_not_migrate = 0` (now a materialized table carrying `project_code`). | ✅ Resolved |
| Q9 | **`COMMISSION` with `surChargeKeepModifier = 0.000`** — facility 45 would get `partnerSharePercentage = 0` (operator takes 100%). Source spread of `surChargeKeepModifier` across the 7 `COMMISSION` facilities: `0.650` ×4, `0.350`, `0.100`, `0.000`. | ✅ Resolved |
| Q10 | **`title` for `COMMISSION` when `surChargeKeepModifier = 0.000`** — renders as `Commission 100%, 0 NOK/evse`. Cosmetic follow-on from Q9. | ✅ Resolved |

## Follow-on work (not part of this mapping)

- ⚠️ **`target.partner_mapping` currently holds stale, facility-grained keys.** All 446 rows
  are `Laddel|Facility|…`, left over from a partner run that predates the customer-grained
  [307_target_partners.sql](../../sql/307_target_partners.sql). The view therefore returns
  all **439** in-scope rows with `partnerId IS NULL`, and the step skips every one of them.
  This is pre-existing debt in the partners pipeline; re-running the `partners` step
  resolves it.
- **Lookup-before-create** is not implemented: partner contracts have no natural key
  ([MigrationPatternGuide §6.3](../MigrationPatternGuide.md#63-lookup-before-create)), so the
  mapping write halts hard on failure and the adoption columns
  (`*_existed_before_migration` / `matched_by` / `previous_record_snapshot`) from
  [§7.1](../MigrationPatternGuide.md#7-mapping-tables) are deliberately omitted from
  `target.partner_contract_mapping` — same treatment as `partner_mapping` (001) and
  `location_mapping` (003).
- **`PATCH` reuses the create payload.** The API's `PartnerContract_patch` schema does not
  *declare* `partnerId`, but sending it unchanged is known from experience to be accepted,
  so the update path needs no special-casing.
