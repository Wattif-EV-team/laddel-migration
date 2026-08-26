# Field mapping — SiteTracker Site Relation (create)

> **Status:** ✅ **Mapping confirmed (2026-08-13).** Maps the **`laddel`** source onto the
> SiteTracker (Salesforce) **`Site_Relation__c`** sObject — the bridge between a
> [Site](sitetracker_site.md) and an [Account](sitetracker_account.md). The field list
> still traces back to the **Wattif** sandbox describe (see
> [sitetracker-reference.md](../sitetracker-reference.md) and
> [reference/README.md](../../reference/README.md)) except where noted `🆕 2026-08-13`. All
> mappings below have reviewer sign-off.
>
> ✅ **Live-org refresh (2026-08-13).** Connected to the **laddel** SiteTracker org and
> re-fetched the `Site_Relation__c` describe — **no new/changed/removed fields** vs. the
> Wattif baseline (see [sitetracker-reference.md](../sitetracker-reference.md)). The same
> refresh found a **new, separate** `previous_CPO__c` field on `sitetracker__Site__c`
> itself — see the note under [Role & dates](#role--dates) below to avoid confusing the two.

## Endpoint & payload source

- **sObject:** `Site_Relation__c` — links a Site to a Company (Account) with a role.
  `Name` is an auto-number, read-only.
- **Create:** `POST /services/data/vXX.0/sobjects/Site_Relation__c/` → `201`
  `{ "id": "..." }`.
- **Update:** `PATCH /services/data/vXX.0/sobjects/Site_Relation__c/{id}` → `204`.
- **No lookup-before-create (decided 2026-08-13).** Unlike the reference project's SOQL
  lookup by `(Site__c, Company__c, Site_Relation_Role__c)`, this migration uses
  **mapping_key-only** idempotency — the same simple pattern as
  [sitetracker_account.md](sitetracker_account.md) and [sitetracker_site.md](sitetracker_site.md):
  a populated `target_sf_site_relation_id` ⇒ update, `NULL` ⇒ create directly, no SOQL
  dedup step. **Accepted risk:** if a mapping write is lost after a successful create
  (see [MigrationPatternGuide §7.4](../MigrationPatternGuide.md#74-write-the-mapping-atomically-and-halt-hard-if-it-fails)),
  a re-run cannot detect the orphan and would create a duplicate relation — the same
  accepted risk as Account and Site. Real SOQL lookup-before-create (adoption columns,
  snapshot, `matched_by`) is a possible **future improvement**, not implemented now.
- Authoritative field metadata: ✅ the live-org describe
  [sitetracker_describe_Site_Relation__c.json](../sitetracker-describes/sitetracker_describe_Site_Relation__c.json)
  (fetched from the laddel org 2026-08-13; the older Wattif-sandbox snapshot of the same
  name under
  [reference/projectsaturn/research-test/sitetracker](../../reference/projectsaturn/research-test/sitetracker/)
  is the diff baseline only).
- Reference implementation (re-implement, do **not** import):
  [CreateOrUpdateSiteTrackerSiteRelations.py](../../reference/projectsaturn/CreateOrUpdateSiteTrackerSiteRelations.py)
  and view
  [323_target_sitetracker_site_relations.sql](../../reference/projectsaturn/db/project-sleet/323_target_sitetracker_site_relations.sql)
  — **logic pattern only**: that project's source schema (Wattif/Sleet) is different from
  `laddel` (e.g. it has planning-derived installer roles with no laddel equivalent); do
  not copy its source columns or its multi-role UNION ALL shape without justification.

## Grain & idempotency

- **Grain: one relation per `laddel.facility`** — the bridge is `facility_contact`, which
  was **verified 2026-08-12** to enforce **one customer per facility** (see
  [Scope](#scope--source-tables) below), so there is no fan-out: at most one
  `Site_Relation__c` row per Site.
  `mapping_key = 'Laddel|Facility|' || f.facility_id`.
- **Dependency gating (hard requirement, per [MigrationPatternGuide §5.2](../MigrationPatternGuide.md#52-target-id-section)):**
  a relation can only be created once **both** its Site and its Account exist in
  SiteTracker. The eventual target view must `LEFT JOIN` (or `JOIN`)
  `sitetracker_site_mapping` (on the Site's `mapping_key`) and
  `sitetracker_account_mapping` (on the Account's `mapping_key`,
  `'Laddel|Customer|' || fc.customer_id`) and **filter out rows where either
  target id is NULL** — this is a hard dependency, not a soft/optional reference.
- Facilities with **no** `facility_contact` row (105 / 5,047 workspace-wide, 2026-08-12)
  simply emit **no relation row** — there is no customer to link to.

## Scope & source tables

- **Batch gate: `organization.migration_status IN ('READY', 'MIGRATE')`** — updated
  2026-08-13 (was `'READY'` only), same gate as [Site](sitetracker_site.md) and
  [sitetracker_account.md](sitetracker_account.md) — **630 facilities** in scope as of
  2026-08-13 (was 470).

```
laddel.facility          f    -- facility_id (PK), organization_id
   └─ laddel.facility_contact fc ON fc.facility_id = f.facility_id   -- LEFT JOIN, 0-or-1 row
                                  --   (customer_id, facility_id) — UNIQUE(facility_id)
         └─ laddel.customer   c  ON c.customer_id = fc.customer_id
laddel.organization o ON o.organization_id = f.organization_id       -- batch gate only
```

`facility_contact` DDL (supplied 2026-08-12, confirmed against the live data):

```sql
create table laddel.facility_contact
(
    customer_id int not null,
    facility_id int not null,
    primary key (customer_id, facility_id),
    constraint facility_contact_facility_id_key
        unique (facility_id),
    constraint facility_contact_customer_id_fkey
        foreign key (customer_id) references laddel.customer (customer_id)
            on update cascade,
    constraint facility_contact_facility_id_fkey
        foreign key (facility_id) references laddel.facility (facility_id)
            on update cascade
);
```

**Verified business rule (2026-08-12):** despite the composite primary key technically
allowing many-to-many, `UNIQUE(facility_id)` enforces **one customer per facility**, and
the data has **zero violations** (`GROUP BY facility_id HAVING COUNT(*) > 1` → 0 rows). A
single customer **can** have many facilities (4,942 total rows, 4,942 distinct
`facility_id`, but only 4,655 distinct `customer_id`; the top customer has 35 facilities).
This is why the Site Relation grain is safely **per-facility** — no composite
`(facility_id, customer_id)` key is needed.

### Source query

```sql
FROM      laddel.facility         f
JOIN      laddel.organization     o  ON o.organization_id = f.organization_id
LEFT JOIN laddel.facility_contact fc ON fc.facility_id    = f.facility_id
LEFT JOIN laddel.customer         c  ON c.customer_id     = fc.customer_id
WHERE     o.migration_status IN ('READY', 'MIGRATE')
```

`fc`/`c` are `LEFT JOIN` deliberately — a facility with no `facility_contact` row still
needs to be considered (it simply won't produce a Site Relation payload, since
`Company__c` can't be resolved).

## Field mapping

Legend — **Default** = constant we emit; **`f./fc./c.`** = source column; ✅ = confirmed by
the reviewer 2026-08-13; *(omit)* = deliberately left out.

### Identity / relationship

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `Site__c` | reference → Site | **yes** | Resolved via `sitetracker_site_mapping` on `'Laddel|Facility|' \|\| f.facility_id` | Requires the [Site](sitetracker_site.md) step to have already run and populated its mapping table. |
| `Company__c` | reference → Account | **yes** | Resolved via `sitetracker_account_mapping` on `'Laddel|Customer|' \|\| fc.customer_id` | Requires the [Account](sitetracker_account.md) step to have already run. `NULL` when the facility has no `facility_contact` row (105/5,047) — row is dropped, not sent with a NULL `Company__c`. |

### Role & dates

| API field | Type | Req | Source / value | Notes |
|---|---|:--:|---|---|
| `Site_Relation_Role__c` | picklist | no | ✅ `'OWNER of SITE'` (Default, constant) | Confirmed. `facility_contact` has **only** `customer_id`/`facility_id` — no role/type column exists anywhere on the bridge — so every emitted relation gets the **same** constant role. Other roles the picklist supports (`INSTALLER`, `SUPPLIER`, `PARKING_OPERATOR`, …) have **no laddel source at all** and are out of scope for this mapping. |
| `Site_Relation_Start_Date__c` | date | no | ✅ `f.creation_date` (confirmed) | No dedicated relation-start-date on `facility_contact`. Reuses the same confirmed source as Site's `Open_Date__c`/`Installed_Date__c` ([sitetracker_site.md](sitetracker_site.md)) — i.e. the facility's own (laddel-inherited) creation date. |
| `previous_CPO__c` | string | no | ✅ **`'Laddel (eMabler)'`** (Default, constant) | Confirmed 2026-08-13 — same constant as the **Site-level** `previous_CPO__c` ([sitetracker_site.md](sitetracker_site.md)), since this is an internal CSMS migration (laddel's own prior "eMabler" backend). Note these are two independent fields on two different objects that happen to share a name and now also share a value. |
| `Grid_Supply__c` | reference | no | *(omit)* | Confirmed — no grid-supply record concept in laddel. |

## Data-quality findings (refreshed 2026-08-13)

| Finding | Count | Impact |
|---|---|---|
| `facility_contact` rows total | 4,942 | = distinct `facility_id` count exactly (see below) — confirms 1 row per facility. |
| Facilities with >1 customer in `facility_contact` | **0** | `GROUP BY facility_id HAVING COUNT(*) > 1` returned 0 rows — business rule holds, no violations. |
| Distinct customers in `facility_contact` | 4,655 (< 4,942 rows) | Confirms customers can have multiple facilities (top customer: 35). |
| Facilities with **no** `facility_contact` row (workspace-wide) | 105 / 5,047 | These facilities get a Site but **no** Site Relation row. |
| Facilities with **no** `facility_contact` row (in-scope batch) | 15 / 630 | Within the current `READY`/`MIGRATE` batch specifically. |
| Facilities currently `READY` or `MIGRATE` (in scope) | 630 (470 `READY` + 160 `MIGRATE`) | Same batch gate as Site/Account (updated 2026-08-13). |

## Decisions / open questions

All items below were confirmed by the reviewer on 2026-08-13.

| # | Question | Status |
|---|---|---|
| Q1 | **Grain** — one relation per facility (not per facility+customer pair); confirmed by the `UNIQUE(facility_id)` constraint and zero data violations. | ✅ Confirmed |
| Q2 | **`Site_Relation_Role__c`** — constant `'OWNER of SITE'`. | ✅ Confirmed |
| Q3 | **`Site_Relation_Start_Date__c`** — `facility.creation_date`, same as Site's dates. | ✅ Confirmed |
| Q4 | **`previous_CPO__c`** — constant `'Laddel (eMabler)'`, same value as Site's; **`Grid_Supply__c`** — omit, left blank. | ✅ Confirmed |
| Q5 | **Dependency gating** — filter to rows where both `Site__c` and `Company__c` are resolved (both parent steps must run first). | ✅ Confirmed |
| Q6 | **Batch gate** — `organization.migration_status IN ('READY', 'MIGRATE')`, applies to Site, Site Relation, and Account. | ✅ Confirmed (630 facilities) |
