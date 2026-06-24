# Field mapping — Location (create)

> **Status:** � **Mapping decided (first iteration).** Maps the **`laddel`** source onto
> the Ampeco **create location** payload (`locationV2Create`). All review questions
> (Q1–Q10) resolved. Companion target view lives in
> [304_target_location.sql](../../sql/304_target_location.sql) (currently a stub — only
> raw `facility` columns; **not yet aligned** with this mapping).

## Endpoint & payload source

- **API (source of truth):** `POST /public-api/resources/locations/v2.0`
  (`operationId: locationCreate`, request schema `locationV2Create` =
  `locationV2WriteBase` with `required: [name, geoposition, address, country]`).
- **Update:** `PATCH /public-api/resources/locations/v2.0/{location}`
  (`operationId: locationUpdate`, schema `locationV2Patch` = `locationV2PatchBase`).
- The older `POST .../locations/v2.0` deprecated variant (`locationCreateDeprecated`,
  schema `Location-with-required-attr`) is **deprecated** — do **not** target it.
- **Notes** are a **separate resource** —
  `POST /public-api/resources/locations/v2.0/{location}/notes` — out of scope here.

> ℹ️ **New fields vs. the reference project.** Compared with `reference/projectsaturn`'s
> `CreateOrUpdateLocation.py` (which sent `status`, `geoposition`, `country`, the
> translation arrays, `city`, `region`, `postCode`, `externalId`, `tags`), the current
> v2.0 schema adds: `streetAddress`, `timezone`, `parkingType`, `accessMethods`,
> `facilities`, `paymentOptions`, `acceptedPaymentBrands`, `workingHours`,
> `additionalDescription`, `externalAppData`, and `state`. The YAML is authoritative.

## Scope & grain

- **Grain: one location per `facility`** (same grain as Partner).
  `mapping_key = 'Laddel|Location|' || f.facility_id`.
- **Batch gate (decided):** the **same** first-batch gate as Partner —
  `migration_status = 'READY'` orgs with a `migration_project_code` — **but the
  `priceModel = 'SUBSCRIPTION'` filter is dropped.** Instead the scope is gated on the
  facility already having a **target partner**, i.e. an existing row in
  `target.partner_mapping` (key `Laddel|Facility|{facility_id}`). A location is only
  emitted once its partner has been created, so any facility that was removed from the
  partner batch is automatically excluded.

## Source tables

```
laddel.facility               f   -- facility_id (PK), facility_name, organization_id,
                                  --   migration_project_code
   └─ laddel.facility_information fi ON fi.facility_id = f.facility_id   -- 1:1
         └─ laddel.address      a  ON a.address_id = fi.address_id       -- address, postal_code,
                                  --   city, county, country, latitude, longitude
```

Proposed source query:

```sql
FROM       laddel.facility             f
JOIN       laddel.facility_information fi ON fi.facility_id = f.facility_id
JOIN       laddel.address              a  ON a.address_id   = fi.address_id
-- batch gate: only facilities that already have a target partner
JOIN       target.partner_mapping      pm ON pm.mapping_key = CONCAT('Laddel|Facility|', f.facility_id)
```

- Verified (4 902 facilities): **every** facility has exactly one `facility_information`
  row and one `address` row (no NULLs), so `JOIN` (not `LEFT JOIN`) is safe.
- The `target.partner_mapping` join is what restricts locations to the partner batch.

## Source column reference

| Table | Column | Type | Notes |
|---|---|---|---|
| `facility` | `facility_id` | int PK | grain / mapping key |
| `facility` | `facility_name` | varchar(255) | → `name` |
| `facility` | `organization_id` | int | partner linkage (not in payload) |
| `facility` | `migration_project_code` | varchar(10) | → `externalId` (only 469/4902 set) |
| `facility_information` | `information` | varchar(128) | free text, **mostly NULL** — not used (first iteration) |
| `facility_information` | `is_hidden` | tinyint(1) | not used (`status` is deprecated) |
| `address` | `address` | varchar(255) | street line, e.g. `Andøyfaret 31` |
| `address` | `postal_code` | varchar(32) | → `postCode` |
| `address` | `city` | varchar(32) | → `city` |
| `address` | `county` | varchar(32) | **not used** — too dirty; `region` hard-coded blank |
| `address` | `country` | enum('Norway') | → `country` = `'NO'` |
| `address` | `latitude` | double | → `geoposition.latitude` (3 213 are `0`) |
| `address` | `longitude` | double | → `geoposition.longitude` (3 213 are `0`) |

## Field mapping

Legend — **Default** = constant we emit; **`f./fi./a.`** = source column;
*(omit)* = field deliberately left out; ⚠️ = needs a decision; 🟡 = best-effort guess.

> **Translation convention.** Translated fields are **not** emitted as JSON arrays.
> Following the reference pattern (and MigrationPatternGuide §5.3), the view emits **one
> column per language** named `<field>_<locale>`, and the step folds them into the API's
> `[{locale, translation}]` array. We emit two locales: **`en`** and **`nb-NO`**.

### Identity

| API column | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `name_en` | string | **yes** | `TRIM(f.facility_name)` | Folded into `name[]`. NOT NULL in source. |
| `name_nb-NO` | string | **yes** | *(same as `name_en`)* | Single source name, emitted for both locales. |
| `externalId` | string | no | `f.migration_project_code` | Project code `W047L####`. Always set within the batch (in-scope facilities have a code). |
| ~~`status`~~ | — | — | *(omit — never send)* | **Deprecated** in the API. |

### Geoposition (required)

| API column | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `geoposition_latitude` | number | **yes** | `a.latitude` | **Decided: send as-is, incl. `0` for the 3 213 placeholder rows.** `geoposition` is API-required; `0,0` accepted for now, to be backfilled/geocoded before go-live. |
| `geoposition_longitude` | number | **yes** | `a.longitude` | Same `0,0`-placeholder caveat. |

### Address

| API column | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `address_en` | string | **yes** | `CONCAT(TRIM(a.address), ', ', TRIM(a.postal_code), ' ', TRIM(a.city))` | Full address composed from trimmed parts. Folded into `address[]`. |
| `address_nb-NO` | string | **yes** | *(same as `address_en`)* | |
| `streetAddress_en` | string | no | `TRIM(a.address)` | Street line only, e.g. `Andøyfaret 31`. Folded into `streetAddress[]`. |
| `streetAddress_nb-NO` | string | no | *(same as `streetAddress_en`)* | |
| `city` | string | no | `TRIM(a.city)` | |
| `postCode` | string | no | `TRIM(a.postal_code)` | Required for NO. |
| `country` | enum | **yes** | `'NO'` (Default) | Source enum is the single literal `'Norway'`; map to ISO `'NO'`. |
| `region` | string | no | `''` (hard-coded blank) | **Decided:** `a.county` is too dirty to trust — emit an empty string. |
| `state` | string | no | *(omit)* | Only for US/AU/CA/UM/RO; N/A for Norway. |

### Descriptions (deferred — first iteration)

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `description` | translated[] | no | *(omit)* | **Decided:** no defaults on first iteration. |
| `shortDescription` | translated[] | no | *(omit)* | Deferred. |
| `additionalDescription` | translated[] | no | *(omit)* | Deferred. |

### Classification / amenities

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `parkingType` | enum | no | *(omit)* | No reliable source. |
| `accessMethods` | enum[] | no | *(omit)* | No source. |
| `facilities` | enum[] | no | *(omit)* | **Decided:** omit. |
| `paymentOptions` | enum[] | no | *(omit)* | No source (AFIR reporting). |
| `acceptedPaymentBrands` | enum[] | no | *(omit)* | No source. |
| `timezone` | string | no | *(omit)* | **Decided:** omit. |

### Working hours / misc

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `workingHours.isAlwaysOpen` | boolean | no | `true` (Default) | **Decided:** all locations always open. Emit `workingHours = { "isAlwaysOpen": true }`. |
| `tags` | string[] | no | *(omit)* | No source. |
| `externalAppData` | object | no | *(omit)* | No source. |

## Data-quality findings (from sampling 4 902 facilities)

| Finding | Count | Impact |
|---|---|---|
| `geoposition` is `(0, 0)` placeholder | 3 213 / 4 902 | **Interim:** sent as-is (`0,0`) for the first iteration; needs backfill/geocoding before go-live. The partner-batch gate reduces how many of these are actually emitted. |
| `migration_project_code` present | 469 / 4 902 | `externalId` NULL for most; aligns with "first batch is a subset". |
| `facility_information` / `address` missing | 0 | Clean — `JOIN` is safe. |
| `county` distinct values | 43, many junk | `region` unsafe to map directly. |
| `country` values | 1 (`Norway`) | Maps cleanly to `'NO'`. |

## Open questions / decisions

| # | Question | Status |
|---|---|---|
| Q1 | **Translations** — one column per language (not a JSON array); emit `en` + `nb-NO` and fold to `[{locale, translation}]` in the step. | ✅ Resolved |
| Q2 | **Zero geoposition** — `geoposition` is API-required; send `0,0` as-is for now (backfill/geocode before go-live). | ✅ Resolved (interim) |
| Q3 | **`address`** — compose full address from trimmed parts: `address, postal_code city`. | ✅ Resolved |
| Q4 | **`status`** — deprecated; **never send**. | ✅ Resolved |
| Q5 | **`region`** — hard-coded blank `''` (`a.county` too dirty). | ✅ Resolved |
| Q6 | **`description`** — no defaults on first iteration; omit. | ✅ Resolved |
| Q7 | **`facilities`** — omit. | ✅ Resolved |
| Q8 | **`timezone`** — omit. | ✅ Resolved |
| Q9 | **`workingHours`** — `isAlwaysOpen: true` for all. | ✅ Resolved |
| Q10 | **Batch gate** — same as Partner (READY + project code) **minus** the `SUBSCRIPTION` filter; gated on an existing `target.partner_mapping` row. | ✅ Resolved |
