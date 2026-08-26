# Facility exclusion — staging SQL (one-time, run manually)

> **Prerequisite:** `target.facility_migration_eligibility` (`sql/201_facility_migration_eligibility.sql`)
> must exist before running anything below — build it with:
> ```powershell
> uv run ladmig build --file 201_facility_migration_eligibility.sql
> ```
>
> ⚠️ **The `laddel` source is read-only to the migration tooling.** Section 1 below
> updates `laddel.organization` directly and is **not** wired into `ladmig build` — it is
> provided for a DBA / privileged session to run once, the same pattern as
> [partner_staging_sql.md](partner_staging_sql.md). Review before running on production.
>
> Section 2 only touches the writable `target` mapping tables, but it is also a one-time,
> manually-run cleanup (not part of `ladmig build`).

## Background

`target.facility_migration_eligibility` flags a facility `should_not_migrate = 1` when
**any** of these hold:

1. `no_chargers` — zero `charger` rows.
2. `all_chargers_inactive` — has chargers, but none `active = 1` (also true when
   `no_chargers` is true).
3. `no_sessions_ever` — zero `archived_session` rows across all of its chargers.
4. `no_recent_sessions` — most recent session (`MAX(archived_session.start_time)`) is
   more than 6 months old, or there is none.

Counts at time of writing: **129** facilities with 0 chargers, **646** more with chargers
but all inactive (**775** total "only inactive"); **138** organizations are all-inactive
org-wide (every facility `should_not_migrate`), of which **2** have a facility created in
the last 60 days.

---

## 1. Set `migration_status` on organizations with only inactive facilities

Run against `laddel` (DBA / privileged session). Organizations where **every** facility is
`should_not_migrate` are set to `DO_NOT_MIGRATE`, **except** those with a facility created
in the last 60 days (possibly a new install not yet in use) — those get `INVESTIGATE`
instead. `DONE` organizations are never touched. Organizations with zero facilities are
left alone (nothing to decide).

```sql
-- 1a. Organizations with a facility younger than 60 days -> INVESTIGATE.
UPDATE laddel.organization o
SET o.migration_status = 'INVESTIGATE'
WHERE o.migration_status <> 'DONE'
  AND EXISTS (
        SELECT 1 FROM laddel.facility f WHERE f.organization_id = o.organization_id
      )
  AND NOT EXISTS (
        SELECT 1
        FROM laddel.facility f
        JOIN target.facility_migration_eligibility fme ON fme.facility_id = f.facility_id
        WHERE f.organization_id = o.organization_id
          AND fme.should_not_migrate = 0
      )
  AND EXISTS (
        SELECT 1
        FROM laddel.facility f2
        WHERE f2.organization_id = o.organization_id
          AND f2.creation_date >= NOW() - INTERVAL 60 DAY
      );

-- 1b. Remaining all-inactive organizations (no recent facility) -> DO_NOT_MIGRATE.
UPDATE laddel.organization o
SET o.migration_status = 'DO_NOT_MIGRATE'
WHERE o.migration_status <> 'DONE'
  AND EXISTS (
        SELECT 1 FROM laddel.facility f WHERE f.organization_id = o.organization_id
      )
  AND NOT EXISTS (
        SELECT 1
        FROM laddel.facility f
        JOIN target.facility_migration_eligibility fme ON fme.facility_id = f.facility_id
        WHERE f.organization_id = o.organization_id
          AND fme.should_not_migrate = 0
      )
  AND NOT EXISTS (
        SELECT 1
        FROM laddel.facility f2
        WHERE f2.organization_id = o.organization_id
          AND f2.creation_date >= NOW() - INTERVAL 60 DAY
      );
```

Run 1a **before** 1b — `1b`'s `NOT EXISTS (... 60 DAY ...)` predicate is what keeps the
two updates mutually exclusive, so order between them does not actually matter, but this
is the natural reading order.

### Verification (read-only)

```sql
SELECT migration_status, COUNT(*) AS orgs
FROM laddel.organization
GROUP BY migration_status
ORDER BY migration_status;
```

---

## 2. Clean up already-created SiteTracker records for facilities/orgs without chargers

This is narrower than the eligibility rule above — it only targets facilities with
**zero chargers** (`fme.no_chargers = 1`), i.e. things that should never have been created
in the first place, not the broader "inactive" set.

**Preferred: run the script.** `scratch/cleanup_zero_charger_sitetracker.py` runs the same
identify queries below, deletes each record in Salesforce via the API (child-first order:
Site_Relation__c, then sitetracker__Site__c, then Account), and removes the matching local
mapping row only after a confirmed Salesforce delete (a `404` also counts as
already-deleted). Preview first, then execute:

```powershell
uv run python scratch/cleanup_zero_charger_sitetracker.py --dry-run
uv run python scratch/cleanup_zero_charger_sitetracker.py
```

The SQL below is the read-only fallback/reference if you'd rather delete the Salesforce
records by hand (Data Loader / Bulk API) instead of running the script.

### 2a. Identify — export Salesforce IDs (reference only; the script does this itself)

```sql
SELECT 'site' AS object_type, ssm.mapping_key, ssm.target_sf_site_id AS target_id
FROM target.sitetracker_site_mapping ssm
JOIN target.facility_migration_eligibility fme
    ON fme.facility_id = CAST(SUBSTRING_INDEX(ssm.mapping_key, '|', -1) AS UNSIGNED)
WHERE fme.no_chargers = 1
  AND ssm.target_sf_site_id IS NOT NULL

UNION ALL

SELECT 'site_relation', ssrm.mapping_key, ssrm.target_sf_site_relation_id
FROM target.sitetracker_site_relation_mapping ssrm
JOIN target.facility_migration_eligibility fme
    ON fme.facility_id = CAST(SUBSTRING_INDEX(ssrm.mapping_key, '|', -1) AS UNSIGNED)
WHERE fme.no_chargers = 1
  AND ssrm.target_sf_site_relation_id IS NOT NULL

UNION ALL

-- Accounts: a customer's Account is only cleaned up if NONE of its facilities
-- have any chargers (a customer can be linked to several facilities).
SELECT 'account', sam.mapping_key, sam.target_sf_account_id
FROM target.sitetracker_account_mapping sam
WHERE sam.target_sf_account_id IS NOT NULL
  AND EXISTS (
        SELECT 1
        FROM laddel.facility_contact fc
        WHERE fc.customer_id = CAST(SUBSTRING_INDEX(sam.mapping_key, '|', -1) AS UNSIGNED)
      )
  AND NOT EXISTS (
        SELECT 1
        FROM laddel.facility_contact fc
        JOIN target.facility_migration_eligibility fme ON fme.facility_id = fc.facility_id
        WHERE fc.customer_id = CAST(SUBSTRING_INDEX(sam.mapping_key, '|', -1) AS UNSIGNED)
          AND fme.no_chargers = 0
      );
```

### 2b. Delete the local mapping rows (only if NOT using the script)

Only needed if you deleted the Salesforce records by hand instead of running the script
above (the script already does this step itself, per record, right after each confirmed
Salesforce delete). Run **after** the corresponding Salesforce records have been deleted.
Order matters only in the sense that relations are children of sites/accounts — delete
them first, matching the dependency order used elsewhere in this project (see
`MigrationPatternGuide.md` §5.2).

```sql
DELETE ssrm FROM target.sitetracker_site_relation_mapping ssrm
JOIN target.facility_migration_eligibility fme
    ON fme.facility_id = CAST(SUBSTRING_INDEX(ssrm.mapping_key, '|', -1) AS UNSIGNED)
WHERE fme.no_chargers = 1;

DELETE ssm FROM target.sitetracker_site_mapping ssm
JOIN target.facility_migration_eligibility fme
    ON fme.facility_id = CAST(SUBSTRING_INDEX(ssm.mapping_key, '|', -1) AS UNSIGNED)
WHERE fme.no_chargers = 1;

DELETE lm FROM target.location_mapping lm
JOIN target.facility_migration_eligibility fme
    ON fme.facility_id = CAST(SUBSTRING_INDEX(lm.mapping_key, '|', -1) AS UNSIGNED)
WHERE fme.no_chargers = 1;

DELETE sam FROM target.sitetracker_account_mapping sam
WHERE sam.target_sf_account_id IS NOT NULL
  AND EXISTS (
        SELECT 1
        FROM laddel.facility_contact fc
        WHERE fc.customer_id = CAST(SUBSTRING_INDEX(sam.mapping_key, '|', -1) AS UNSIGNED)
      )
  AND NOT EXISTS (
        SELECT 1
        FROM laddel.facility_contact fc
        JOIN target.facility_migration_eligibility fme ON fme.facility_id = fc.facility_id
        WHERE fc.customer_id = CAST(SUBSTRING_INDEX(sam.mapping_key, '|', -1) AS UNSIGNED)
          AND fme.no_chargers = 0
      );
```

### Verification (read-only)

```sql
-- Should all return 0 after step 2b.
SELECT COUNT(*) FROM target.sitetracker_site_mapping ssm
JOIN target.facility_migration_eligibility fme
    ON fme.facility_id = CAST(SUBSTRING_INDEX(ssm.mapping_key, '|', -1) AS UNSIGNED)
WHERE fme.no_chargers = 1;
```
