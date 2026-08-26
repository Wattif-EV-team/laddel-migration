# Field mapping — SiteTracker Site (create)

> **Status:** ✅ **Mapping confirmed (2026-08-13).** Maps the **`laddel`** source onto the
> SiteTracker (Salesforce) **`sitetracker__Site__c`** sObject. The field list below still
> traces back to the **Wattif** sandbox describe (see
> [sitetracker-reference.md](../sitetracker-reference.md) and
> [reference/README.md](../../reference/README.md)) except where noted `🆕 2026-08-13`
> (confirmed live against the **laddel** org). All mappings below have reviewer sign-off;
> a couple of items are explicitly deferred as **future work** (see below), not open
> questions.
>
> ✅ **Live-org refresh (2026-08-13).** Connected to the **laddel** SiteTracker org and
> re-fetched the `sitetracker__Site__c` describe — see
> [sitetracker-reference.md](../sitetracker-reference.md#sitetracker__site__c-site) for the
> full diff. Four fields are new (`Operator__c`, `Operator_ID__c`, `Terminated_Date__c`,
> `previous_CPO__c`) and `sitetracker__Site_Type__c` gained a `HOME CHARGER` picklist value.
> These are folded into the mapping below (marked `🆕 2026-08-13`).
>
> ✅ **Decisions finalized (2026-08-13).** All open questions below are now resolved by
> the reviewer — see the [Decisions / open questions](#decisions--open-questions) table.
> Two out-of-scope-for-now items are flagged for **future work**: transitioning
> `sitetracker__Site_Status__c` to `'Operational'` once `migration_status = 'DONE'` (Q2),
> and excluding terminated customers from the batch gate (Q13).

## Endpoint & payload source

- **sObject:** `sitetracker__Site__c` (a physical site/location).
- **Create:** `POST /services/data/vXX.0/sobjects/sitetracker__Site__c/` → `201`
  `{ "id": "..." }`.
- **Update:** `PATCH /services/data/vXX.0/sobjects/sitetracker__Site__c/{id}` → `204`.
- **Lookup-before-create (updated 2026-08-26).** Site `Name` must be unique in the target
  org, so an unmapped row is not created blindly: before create, a SOQL lookup by exact
  `Name` runs (`find_site_by_name`-equivalent, re-implemented from the Wattif reference).
  No match ⇒ create as-is. A match whose `Site_ID__c` equals ours ⇒ the Site already
  exists (e.g. a prior partial run) — adopt it: update in place and write the mapping
  instead of creating. A match with a *different* `Site_ID__c` ⇒ the name collides with an
  unrelated Site; disambiguate ours by appending the project code in square brackets
  (`"<name> [<project_code>]"`) and create under that name. This supersedes the
  2026-08-13 "no lookup" decision (Q15).
- Authoritative field metadata: ✅ the live-org describe
  [sitetracker_describe_sitetracker__Site__c.json](../sitetracker-describes/sitetracker_describe_sitetracker__Site__c.json)
  (fetched from the laddel org 2026-08-13; the older Wattif-sandbox snapshot of the same
  name under
  [reference/projectsaturn/research-test/sitetracker](../../reference/projectsaturn/research-test/sitetracker/)
  is the diff baseline only).
- Reference implementation (re-implement, do **not** import):
  [CreateOrUpdateSiteTrackerSites.py](../../reference/projectsaturn/CreateOrUpdateSiteTrackerSites.py)
  and view
  [321_target_sitetracker_sites.sql](../../reference/projectsaturn/db/project-sleet/321_target_sitetracker_sites.sql)
  — **logic pattern only**: that project's source schema (Wattif/Sleet) is different from
  `laddel`: do not copy its source columns.

## Grain & idempotency

- **Grain: one Site per `laddel.facility`** — same grain as
  [Location](location.md).
  `mapping_key = 'Laddel|Facility|' || f.facility_id`.
- **Idempotency:** [315_target_sitetracker_sites.sql](../../sql/315_target_sitetracker_sites.sql)
  exposes `target_sf_site_id` by `LEFT JOIN`ing
  [004_sitetracker_site_mapping.sql](../../sql/004_sitetracker_site_mapping.sql); `NULL` ⇒
  the row is unmapped and goes through the SOQL lookup-before-create described in
  [Endpoint & payload source](#endpoint--payload-source) (adopt-by-name, or
  rename-and-create on a name collision); populated ⇒ update directly, same as
  [sitetracker_account.md](sitetracker_account.md).

## Scope

- **Batch gate: `organization.migration_status IN ('READY', 'MIGRATE')`** — updated
  2026-08-13 (was `'READY'` only); this gate now applies uniformly across
  [Site](sitetracker_site.md) (this doc), [Site Relation](sitetracker_site_relation.md),
  and [sitetracker_account.md](sitetracker_account.md). Re-verified 2026-08-13:
  `organization.migration_status` now has `UNDEFINED` (4,424 facilities), `READY` (470),
  and `MIGRATE` (**160 facilities**, new) populated — **630 facilities** total in scope
  (no `DONE`/`DO_NOT_MIGRATE` rows yet).

### Source query

```sql
FROM       laddel.facility             f
JOIN       laddel.facility_information fi ON fi.facility_id = f.facility_id
JOIN       laddel.address              a  ON a.address_id   = fi.address_id
JOIN       laddel.organization         o  ON o.organization_id = f.organization_id
WHERE      o.migration_status IN ('READY', 'MIGRATE')
```

Same shape as [304_target_location.sql](../../sql/304_target_location.sql) — `facility` →
`facility_information` → `address` is verified 1:1 with no NULLs (see repo memory /
[location.md](location.md)).

## Source tables

```
laddel.facility               f   -- facility_id (PK), facility_name, organization_id,
                                  --   creation_date, migration_project_code (NOT used)
   └─ laddel.facility_information fi ON fi.facility_id = f.facility_id   -- 1:1
         └─ laddel.address      a  ON a.address_id = fi.address_id       -- address, postal_code,
                                  --   city, country, latitude, longitude
         └─ laddel.price_information pi ON pi.price_id = fi.price_id     -- priceModel, kw_effect
laddel.organization o ON o.organization_id = f.organization_id           -- batch gate only
laddel.charger      ch ON ch.facility_id = f.facility_id (0..N)          -- charger_reference (LDB prefix)
```

- `price_information.priceModel` and `.kw_effect` are now used for `sitetracker__Site_Type__c`
  and `EV_Connector_Type__c`/`EV_Charging_Level__c` respectively (confirmed 2026-08-13 —
  see [Classification](#classification) below). `laddel.charger.charger_reference` (`LDB%`
  prefix) is used for the `HOME CHARGER` site-type rule.
- `laddel.charger_information` / `charger_coordinates` / `installation` were also
  researched (2026-08-12) — none carry connector-type/power data beyond what
  `price_information.kw_effect` already gives us.

## Field mapping

Legend — **Default** = constant we emit; **`f./fi./a./o./pi./ch.`** = source column; ✅ =
confirmed by the reviewer 2026-08-13; 🆕 = new/changed field found in the 2026-08-13 live
describe; *(omit)* = field deliberately left out.

### Identity

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `Site_ID__c` | string | **yes** | `CONCAT('W047L', LPAD(f.facility_id, 4, '0'))` | Max 9 chars — this scheme is exactly 9 (`W047L` + 4 digits). ⚠️ That leaves **no headroom**: a `facility_id` of 10000+ would produce a 10-char value Salesforce rejects (`MAX(facility_id) = 5448` as of 2026-08-13, so ~4,500 facilities of margin). Reuses the **same prefix/derivation as Location's `externalId`** ([location.md](location.md)) so Location and Site line up 1:1 per facility. **Not** a Salesforce External ID, and — since we do **not** SOQL-lookup before create (Q15) — it is currently a **human-readable identifier only**, with no lookup or dedup role in this migration. |
| `Name` | string | **yes** | `TRIM(f.facility_name)` | Same source as Location's `name`. NOT NULL in source. |

### Status / type / ownership — ✅ confirmed 2026-08-13

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `sitetracker__Site_Status__c` | picklist | no | **`'Under Migration'`** (Default, constant) | ✅ Confirmed. **Future work (out of scope for now):** transition to `'Operational'` once the facility's `organization.migration_status` becomes `'DONE'` — needs an update-step, not just a create-time default; document here but do not implement yet. |
| `sitetracker__Site_Type__c` | picklist | no | ✅ Rule-based (see below) | Priority order, first match wins: **1)** `'HOUSING_ASSOCIATION'` when *any* facility sharing the same `organization_id` has `price_information.priceModel = 'SUBSCRIPTION'` (org-wide `EXISTS` check, not per-facility); **2)** `'HOME CHARGER'` 🆕 when the facility has ≥1 `charger.charger_reference LIKE 'LDB%'`; **3)** default `'OTHER'`. `private_employee_facility` is deliberately **not used** (unregistered users aren't captured there). **Validated 2026-08-13:** of 3,221 `private_employee_facility` rows, 3,196 (99.2%) have ≥1 `LDB%` charger; the 25 exceptions are mostly zero-charger or differently-prefixed (`SLUT`/`SLET`/`INAK`/`LDG1`/`LDP4`/…) facilities — an acceptably small gap, does not invalidate using the `LDB%` rule directly instead of the mapping table. **Current batch impact:** of the 630 in-scope (`READY`/`MIGRATE`) facilities, **630/630 classify as `HOUSING_ASSOCIATION`** (502/630 facility rows are directly `SUBSCRIPTION`, the rest share an organization with one that is) and **0 as `HOME CHARGER`** — the LDB/home-charger population isn't in this batch yet. |
| `Owner_Type__c` | picklist | no | ✅ `'C-ClientOwned'` default; `'W-WattifEV'` override for 4 named facilities | **Hard-coded override list** (confirmed 2026-08-13, verified against `facility_name`): `facility_id` **4** (Lindesnes Havhotell), **5** (Rosfjord Strandhotell), **45** (Maritim Fjordhotel Privat), **70** (Tufte Gård) → `'W-WattifEV'`; every other facility → `'C-ClientOwned'`. |
| `Load_Management__c` | picklist | no | ✅ `'NONE'` (Default, constant) | Confirmed — no DLM protocol in use. |

### Address

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `sitetracker__Street_Address__c` | string | no | `TRIM(a.address)` | Same source as Location's `streetAddress`. |
| `sitetracker__City__c` | string | no | ✅ `TRIM(a.city)` — **as-is, no INITCAP** (confirmed 2026-08-13) | MySQL has no `INITCAP` and hand-rolling one is not worth the risk of mangling Norwegian place names (`Ås`, `Moss i Østfold`, hyphenated and multi-word names). Decision: migrate the source value **as-is, trimmed only** — deliberately *not* matching Location's `city` treatment. |
| `sitetracker__Zip_Code__c` | string | no | `TRIM(a.postal_code)` | |
| `Country__c` | picklist | no | ✅ `'Norway(NOR)'` (Default, confirmed 2026-08-13) | Confirmed distinct from `sitetracker_account.md`'s `Account.BillingCountry`, which uses plain `'Norway'` — these are independent picklists on independent objects; no inconsistency to resolve. |
| `sitetracker__Street_Address_2__c` | string | no | *(omit)* | No second address line in source. |

### Geolocation

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `sitetracker__Location__Latitude__s` | double | no | `a.latitude` | Compound geolocation field (matches the reference implementation's usage, not the free-text `sitetracker__Lat__c`). Within the current READY+MIGRATE batch (630 facilities), **129 (20%)** are the `(0,0)` placeholder — sent as-is, same interim decision as [location.md](location.md) Q2 (confirmed 2026-08-13, no change). |
| `sitetracker__Location__Longitude__s` | double | no | `a.longitude` | Same placeholder caveat. |
| ~~`sitetracker__Lat__c` / `sitetracker__Long__c`~~ | string | — | *(omit)* | Free-text duplicate of the geolocation compound fields — **do not send both**; pick one. Recommend the compound field only. |

### Classification — ✅ confirmed 2026-08-13

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `EV_Connector_Type__c` | multipicklist | no | ✅ `'Type 2'` default; `'CCS2'` for DC facilities | Default is **22kW AC Type 2** for every facility. A facility is **DC** when its `price_information.kw_effect >= 50` (joined `price_information` → `facility_information` → `facility`, per the reviewer-supplied query) → `'CCS2'`. **Confirmed 2026-08-13:** only **10 facilities workspace-wide** meet this (kw_effect range 50–400), and **none are in the current READY/MIGRATE batch** (all 10 are `migration_status = 'UNDEFINED'`) — so the current batch is 100% `'Type 2'`, but the rule is documented for when DC facilities enter scope. |
| `EV_Charging_Level__c` | multipicklist | no | ✅ `'Level 2 AC 22kWh'` default; DC bucket by `kw_effect` | Default **`'Level 2 AC 22kWh'`** for AC facilities. For DC facilities (`kw_effect >= 50`), bucket using the same threshold as the Wattif reference: `kw_effect > 60` → `'DC_above60'`, `kw_effect <= 60` → `'DC type (below 60 KWh)'`. Of the 10 DC facilities found: 6 at exactly 50kW (→ below-60 bucket), 4 at 120/180/180/400kW (→ above-60 bucket) — none in scope today. |

### Dates

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `Open_Date__c` | date | no | ✅ `f.creation_date` (confirmed) | `facility.creation_date` is the **original installation date inherited from laddel** — confirmed 2026-08-13 to use this, and explicitly **not** `organization.migration_date` (that reflects the migration itself, not the original install). Well populated across the batch (2023-11-02 → 2026-08-11). |
| `Installed_Date__c` | date | no | ✅ `f.creation_date` (confirmed, same value as `Open_Date__c`) | Same source as `Open_Date__c` — no column distinctly represents "installed" vs. "created" at the facility grain, and both should reflect the original laddel installation date. |
| `Terminated_Date__c` | date | no | *(omit)* 🆕 | Left empty. The migration's goal is to **not migrate terminated customers** at all (a batch-gate concern, not a field-mapping one) — out of scope for now; no source column to populate this from today regardless. |

### Operator / previous CPO — ✅ confirmed 2026-08-13, Operator__c value re-verified 2026-08-20

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `Operator__c` | picklist | no | **`'6'`** (Default, constant) | ⚠️ **2026-08-20:** the picklist's underlying VALUES changed live on the org from label strings (e.g. `'Laddel NO'`) to numeric codes (`'1'`..`'6'`) — `'6'` is the code whose label is still "Laddel NO". Detected via `ladmig sitetracker describe sitetracker__Site__c --diff`; [315_target_sitetracker_sites.sql](../../sql/315_target_sitetracker_sites.sql) updated to match. |
| `Operator_ID__c` | string | no | **`'6'`** (Default, constant) | Confirmed literal constant (not derived from any laddel column) — the operator's fixed ID in the wider Wattif-group SiteTracker org. Coincidentally the same literal as `Operator__c` now, but this is a separate free-text field, not the picklist. |
| `previous_CPO__c` | string | no | **`'Laddel (eMabler)'`** (Default, constant) | Confirmed. This is an **internal CSMS migration** — laddel's own prior backend ("eMabler") is the previous CPO system being replaced. Distinct from the same-named field on `Site_Relation__c` (see [sitetracker_site_relation.md](sitetracker_site_relation.md)), which gets the **same** constant value. |

## Data-quality findings (READY+MIGRATE batch, 630 facilities, refreshed 2026-08-13)

| Finding | Count | Impact |
|---|---|---|
| `geoposition` is `(0, 0)` placeholder | 129 / 630 (20%) | Worse than the previous READY-only 9% (the added `MIGRATE` facilities skew higher), still much cleaner than the workspace-wide 65% — send as-is for now, same interim approach as Location. |
| Facility rows directly `priceModel = 'SUBSCRIPTION'` | 502 / 630 | Drives most of the `HOUSING_ASSOCIATION` classification directly. |
| Facilities classified `HOUSING_ASSOCIATION` (org-wide rule) | 630 / 630 (100%) | Every in-scope facility shares an organization with \u2265 1 `SUBSCRIPTION` facility \u2014 this batch is entirely housing-association customers today. |
| Facilities classified `HOME CHARGER` (`LDB%` charger) | 0 / 630 | None of the LDB/home-charger population is in scope yet — rule still applies for future batches. |
| DC facilities (`kw_effect >= 50`) workspace-wide | 10 (kw_effect 50–400) | **0 are in the READY/MIGRATE batch** (all 10 are `migration_status = 'UNDEFINED'`) — `EV_Connector_Type__c`/`EV_Charging_Level__c` are 100% AC/Type-2 today. |
| `private_employee_facility` rows missing an `LDB%` charger | 25 / 3,221 (0.8%) | Validated exception rate for the `HOME CHARGER` rule — small enough to not invalidate using `charger_reference LIKE 'LDB%'` directly. |
| `facility.creation_date` populated | 630 / 630 | Clean — confirmed source for `Open_Date__c`/`Installed_Date__c`. |
| Facilities with no `facility_contact` row | 105 / 5,047 (workspace-wide) | Relevant to [sitetracker_site_relation.md](sitetracker_site_relation.md), not Site itself. |

## Decisions / open questions

All items below were confirmed by the reviewer on 2026-08-13.

| # | Question | Status |
|---|---|---|
| Q1 | **`Site_ID__c` scheme** — reuse Location's `W047L` + zero-padded `facility_id` derivation. | ✅ Confirmed |
| Q2 | **`sitetracker__Site_Status__c`** — constant `'Under Migration'`. **Future work:** transition to `'Operational'` when `organization.migration_status = 'DONE'` — out of scope for now, needs an update-step. | ✅ Confirmed (future work noted) |
| Q3 | **`sitetracker__Site_Type__c`** — `'HOUSING_ASSOCIATION'` (org-wide `SUBSCRIPTION` rule) → `'HOME CHARGER'` (`LDB%` charger rule) → default `'OTHER'`. `private_employee_facility` not used (validated: 99.2% overlap with `LDB%`, acceptable gap). | ✅ Confirmed |
| Q4 | **`Owner_Type__c`** — default `'C-ClientOwned'`; override to `'W-WattifEV'` for facility_id 4, 5, 45, 70. | ✅ Confirmed |
| Q5 | **`Load_Management__c`** — constant `'NONE'`. | ✅ Confirmed |
| Q6 | **`Country__c` format** — `'Norway(NOR)'` confirmed (independent of Account's plain `'Norway'`). | ✅ Confirmed |
| Q7 | **`EV_Connector_Type__c` / `EV_Charging_Level__c`** — default 22kW AC Type 2; `CCS2`/DC bucket via `price_information.kw_effect >= 50`. 0 facilities affected in the current batch. | ✅ Confirmed |
| Q8 | **Dates** — `facility.creation_date` for both `Open_Date__c` and `Installed_Date__c` (original laddel install date); `organization.migration_date` explicitly **not used**. | ✅ Confirmed |
| Q9 | **Geoposition** — send `(0,0)` placeholders as-is (20% of the current batch), consistent with Location's interim approach. | ✅ Confirmed |
| Q10 | **Batch gate** — `organization.migration_status IN ('READY', 'MIGRATE')`, applies to Site, Site Relation, and Account. | ✅ Confirmed (630 facilities) |
| Q11 | **`Operator__c`** 🆕 — constant `'6'` (picklist VALUE whose label is "Laddel NO"; the picklist's underlying values changed from labels to numeric codes, re-verified 2026-08-20). | ✅ Confirmed |
| Q12 | **`Operator_ID__c`** 🆕 — constant `'6'`. | ✅ Confirmed |
| Q13 | **`Terminated_Date__c`** 🆕 — leave empty; goal is to not migrate terminated customers (batch-gate concern, out of scope for now). | ✅ Confirmed |
| Q14 | **`previous_CPO__c`** 🆕 (Site-level) — constant `'Laddel (eMabler)'` (internal CSMS migration). | ✅ Confirmed |
| Q15 | **No lookup-before-create** — superseded 2026-08-26: Site `Name` must be unique, so unmapped rows now go through a SOQL lookup-by-name before create (adopt on project-code match, rename-and-create with a `[project_code]` suffix on a collision with a different Site). | ⛔ Superseded (see [Endpoint & payload source](#endpoint--payload-source)) |
| Q16 | **`sitetracker__City__c` casing** — migrate as-is, trimmed only; no `INITCAP` (MySQL has no such function, and case-folding risks mangling Norwegian place names). | ✅ Confirmed |
