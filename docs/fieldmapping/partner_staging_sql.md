# Partner migration — staging SQL (`laddel` source)

> **Run order:** (1) set `migration_status` on organizations, then (2) assign
> `migration_project_code` to facilities in those organizations.
>
> ⚠️ **The `laddel` source is read-only to the migration tooling.** These statements are
> provided for a DBA / privileged session to run **directly against `laddel`**. They are
> **not** wired into `ladmig build`. Review before running on production.

## First batch scope (MDU)

The first batch is **MDU** (Multi-Dwelling Unit / housing cooperatives): facilities whose
price model is `SUBSCRIPTION`. Selection rules used below:

- Organization has **exactly one distinct customer** (orgs with several customers are
  deferred — they become **sub-operator** resources in Ampeco).
- Organization has at least one facility with `priceModel = 'SUBSCRIPTION'`.
- Corporate-billing customers (linked via `ev_fleet_contact`, not `facility_contact`) are
  **deferred** — not part of the first batch.

Counts at time of writing: **425** organizations qualify; **22** of them also contain
non-`SUBSCRIPTION` facilities (see decision Q2 below).

## Price-model lookup path

`priceModel` lives in `price_information`, reached from a facility via
`facility_information`:

```
facility → facility_information (PK facility_id) → price_information (PK price_id) → priceModel
```

---

## 1. Set `migration_status = 'READY'` on qualifying organizations

```sql
UPDATE organization o
SET o.migration_status = 'READY'
WHERE o.migration_status IN ('UNDEFINED', 'MIGRATE')   -- never touch DONE / DO_NOT_MIGRATE
  -- exactly one distinct customer across all of the org's facilities
  AND (
        SELECT COUNT(DISTINCT fc.customer_id)
        FROM facility f
        JOIN facility_contact fc ON fc.facility_id = f.facility_id
        WHERE f.organization_id = o.organization_id
      ) = 1
  -- at least one SUBSCRIPTION (MDU) facility
  AND EXISTS (
        SELECT 1
        FROM facility f2
        JOIN facility_information fi ON fi.facility_id = f2.facility_id
        JOIN price_information   pi ON pi.price_id     = fi.price_id
        WHERE f2.organization_id = o.organization_id
          AND pi.priceModel = 'SUBSCRIPTION'
      );
```

---

## 2. Assign `migration_project_code` to facilities in READY organizations

Format: `W047L####`, where `W047L` is a fixed prefix and `####` is a zero-padded
sequence starting at `0001`, incremented once per facility.

The sequence is **resumable**: it seeds `@seq` from the current maximum already assigned,
so re-running only numbers facilities that don't yet have a code (no renumbering, no
collisions).

```sql
-- Seed the counter from the highest existing W047L#### code (0 if none yet).
SET @seq := (
    SELECT COALESCE(MAX(CAST(SUBSTRING(migration_project_code, 6) AS UNSIGNED)), 0)
    FROM facility
    WHERE migration_project_code LIKE 'W047L%'
);

-- Single-table UPDATE so ORDER BY is allowed (deterministic numbering order).
UPDATE facility f
SET f.migration_project_code = CONCAT('W047L', LPAD(@seq := @seq + 1, 4, '0'))
WHERE f.migration_project_code IS NULL
  AND f.organization_id IN (
        SELECT organization_id FROM organization WHERE migration_status = 'READY'
      )
ORDER BY f.organization_id, f.facility_id;
```

> **Note (decision Q2):** this assigns a code to **every** facility in a READY org,
> including the non-`SUBSCRIPTION` facilities in the 22 mixed orgs. To restrict codes to
> MDU facilities only, add to the `WHERE`:
>
> ```sql
>   AND EXISTS (
>         SELECT 1
>         FROM facility_information fi
>         JOIN price_information pi ON pi.price_id = fi.price_id
>         WHERE fi.facility_id = f.facility_id
>           AND pi.priceModel = 'SUBSCRIPTION'
>       )
> ```

---

## Verification queries (read-only)

```sql
-- How many orgs are now READY, and how many facilities got a project code.
SELECT
  (SELECT COUNT(*) FROM organization WHERE migration_status = 'READY')            AS ready_orgs,
  (SELECT COUNT(*) FROM facility     WHERE migration_project_code IS NOT NULL)    AS coded_facilities;

-- Spot-check the generated codes.
SELECT f.organization_id, f.facility_id, f.facility_name, f.migration_project_code
FROM facility f
WHERE f.migration_project_code IS NOT NULL
ORDER BY f.migration_project_code
LIMIT 20;

-- Confirm no gaps / duplicates in the sequence.
SELECT migration_project_code, COUNT(*) AS n
FROM facility
WHERE migration_project_code LIKE 'W047L%'
GROUP BY migration_project_code
HAVING n > 1;
```
