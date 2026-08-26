# Migration DDL (`sql/`)

Numbered SQL files that build the **laddel → Ampeco** migration database. They
transform the read-only `laddel` source database into the shape of the Ampeco
target system by creating **views** in the writable `target` database.

Build them with `ladmig build` (see [Building](#building-the-database)).

## File Numbering Schema

Files execute in ascending numeric order. The prefix encodes both order and
drop behaviour:

| Range | Type | Description | Drop behaviour |
|-------|------|-------------|----------------|
| `0xx` | Mapping tables | Persistent tables holding old→new ID mappings and migration state. | **NEVER DROP** — `CREATE TABLE IF NOT EXISTS`. |
| `1xx` | Source normalization views | Views that extract/clean distinct entities from raw source tables. | Dropped & recreated. |
| `2xx` | Shared business-logic views | Intermediate views centralising transform logic reused by target/report views. | Dropped & recreated. |
| `3xx` | Target views | Views producing the Ampeco-shaped payloads (one file per view). | Dropped & recreated. |
| `4xx` | Report / quality views | Views for analysis, export and data-quality checks. | Dropped & recreated. |

> **Current state:** both `0xx` mapping tables and `3xx` target views exist —
> see the [File Inventory](#file-inventory) below.

## ⚠️ Never Drop Source or Mapping Tables

This is the single most important rule.

- **Source tables** live in the `laddel` database, which is **read-only**. The
  views reference them as `` `laddel`.`<table>` ``. Never write to or drop them.
- **Mapping tables** (`0xx`, when they exist) store target-system IDs and
  migration state. Dropping one loses the link between old and new records.
  - Create with `CREATE TABLE IF NOT EXISTS`.
  - Add columns with a guarded `ALTER TABLE` (MySQL 8 has **no**
    `ADD COLUMN IF NOT EXISTS`, so check `information_schema` first or accept
    a re-run error — never `DROP` + recreate).

`ladmig build` never drops anything itself. The drop/recreate behaviour lives
entirely in each individual SQL file, so write `0xx` files defensively.

## Conventions

### Views (`1xx`–`4xx`)

Each view file is self-contained and idempotent:

```sql
DROP VIEW IF EXISTS `target`.`<name>`;

CREATE OR REPLACE VIEW `target`.`<name>` AS
SELECT ...
FROM `laddel`.`<source_table>` ...;
```

- One file per view: `3NN_target_<view_name>.sql`.
- Schema-qualify names: `` `target`.`view` `` for outputs, `` `laddel`.`table` ``
  for source reads. `build` connects with `target` as the default database, so
  cross-database reads from `laddel` must be explicit.
- No PostgreSQL-isms (no `SET ROLE`, no `::cast`, no schemas-within-schema).
- **`mapping_key` uses the SOURCE table name, never the target-system name.**
  The composite key is `Laddel|<SourceTable>|<id>` where `<SourceTable>` is the
  literal `laddel` table the row's grain is one-per (e.g. `Customer`,
  `Facility`) — not the target view name, target API resource, or target
  system (`SiteTrackerAccount`, `Location`, ...). See
  `docs/MigrationPatternGuide.md` §5.1. `target.partners` (customer-grained,
  keyed `Laddel|Customer|{id}`) is the reference example; the same
  `laddel.customer` row also feeds `target.sitetracker_accounts`, which is
  keyed `Laddel|Customer|{id}` too — same segment, different mapping table, no
  collision.

### File ordering

If a view depends on another view, give it a higher number so it is created
later. (All current `3xx` views read only source tables, so order is not yet
significant.)

## File Inventory

### 0xx — Mapping Tables

| File | Table | Written by |
|------|-------|------------|
| `001_partner_mapping.sql` | `target.partner_mapping` | `partners` step |
| `002_sitetracker_account_mapping.sql` | `target.sitetracker_account_mapping` | `sitetracker_accounts` step |
| `003_location_mapping.sql` | `target.location_mapping` | `locations` step |
| `004_sitetracker_site_mapping.sql` | `target.sitetracker_site_mapping` | `sitetracker_sites` step |
| `005_sitetracker_site_relation_mapping.sql` | `target.sitetracker_site_relation_mapping` | `sitetracker_site_relations` step |

### 2xx — Shared Business-Logic Views

| File | View | Reads | Used by |
|------|------|-------|---------|
| `201_facility_migration_eligibility.sql` | `target.facility_migration_eligibility` | `laddel.facility`, `laddel.charger`, `laddel.archived_session` | `304`, `314`, `315`, `316` |
| `202_facility_external_id.sql` | `target.facility_external_id` | `laddel.facility` | `304`, `401` |

`facility_migration_eligibility` centralises the "should this facility be
migrated?" rule (no chargers / all chargers inactive / no sessions ever / no
sessions in the last 6 months → `should_not_migrate`) so target views don't
re-derive it independently.

`facility_external_id` centralises the externalId/project-code scheme
(`W047L` + zero-padded facility_id) so it is derived in exactly one place
instead of being duplicated across the Location target view and the
migration status report.

### 3xx — Target Views

| File | View | Reads |
|------|------|-------|
| `301_target_charge_points.sql` | `target.charge_points` | `laddel.charger` |
| `302_target_charging_zones.sql` | `target.charging_zones` | source tables |
| `303_target_id_tags.sql` | `target.id_tags` | `laddel.rfid` |
| `304_target_location.sql` | `target.location` | source tables + `003`, `201`, `202` |
| `305_target_partner_admins.sql` | `target.partner_admins` | source tables |
| `306_target_partner_contracts.sql` | `target.partner_contracts` | source tables |
| `307_target_partners.sql` | `target.partners` | `laddel.facility` (+ org/contact/customer/price), `target.partner_mapping` |
| `308_target_subscription_plan.sql` | `target.subscription_plan` | source tables |
| `309_target_tariff.sql` | `target.tariff` | source tables |
| `310_target_tariff_groups_and_base_tariff.sql` | `target.tariff_groups_and_base_tariff` | source tables |
| `311_target_user_group_members.sql` | `target.user_group_members` | source tables |
| `312_target_user_groups.sql` | `target.user_groups` | source tables |
| `313_target_users.sql` | `target.users` | source tables |
| `314_target_sitetracker_accounts.sql` | `target.sitetracker_accounts` | source tables + `002`, `201` |
| `315_target_sitetracker_sites.sql` | `target.sitetracker_sites` | source tables + `004`, `201` |
| `316_target_sitetracker_site_relations.sql` | `target.sitetracker_site_relations` | source tables + `002`, `004`, `005`, `201` |

### 4xx — Report / Quality Views

| File | View | Reads |
|------|------|-------|
| `401_report_facility_migration_status.sql` | `target.report_facility_migration_status` | `laddel.facility`, `laddel.organization` (+ self-join for the org tree/EV-fleet inheritance), `laddel.charger`, `laddel.archived_session`, `laddel.facility_subscription`, `laddel.facility_contact`, `laddel.customer`, `laddel.facility_information`, `laddel.price_information`, `laddel.organization_ev_fleet_information`, `target.facility_migration_eligibility` (201), `target.facility_external_id` (202) |

`report_facility_migration_status` is a one-row-per-facility migration status
report: charger/subscription counts, organization migration status/date, a
4-level organization tree (root leftmost, the facility's own org rightmost),
last-3-months session/kWh activity, facility contact details, price model and
EV fleet info (inherited from the nearest ancestor organization that defines
it, with the source organization named). It has no `mapping_key` — it is not
an Ampeco payload view, purely for reporting.

## Building the Database

`ladmig build` executes the DDL files against the `target` database.

```powershell
uv run ladmig build                              # run every *.sql file, in order
uv run ladmig build --file 301_target_charge_points.sql   # run one file
uv run ladmig build --sql-dir sql                # explicit directory (default: sql/)
```

After a build, sanity-check the key views:

```powershell
uv run ladmig verify    # SELECT COUNT(*) on each key target view
```

`verify` fails (non-zero exit) if any key view cannot be queried. The list of
key views lives in `KEY_VIEWS` in `src/laddel_migration/cli.py`; update it when
adding or removing target views.

## Editing a View

A coding agent making a targeted change should:

1. Inspect the live schema/data with `ladmig sql`:
   ```powershell
   uv run ladmig sql "SHOW CREATE VIEW `charge_points`" --database target
   uv run ladmig sql "DESCRIBE `charger`" --database source
   ```
2. Edit the relevant `3NN_target_<view>.sql` file.
3. Apply just that file: `uv run ladmig build --file 3NN_target_<view>.sql`.
4. Re-check with `uv run ladmig verify`.

For larger exploration, write a throwaway script under `scratch/` (see `scratch/README.md`).