# Project Sleet DDL Files

<!-- TOC -->
- [File Numbering Schema](#file-numbering-schema)
- [Source Tables](#source-tables)
- [Entity Normalization (1xx Views)](#entity-normalization-1xx-views)
- [File Inventory](#file-inventory)
- [Building the Database](#building-the-database)
- [Ad-Hoc SQL Queries](#ad-hoc-sql-queries)
- [Guidelines](#guidelines)
<!-- /TOC -->

## File Numbering Schema

Files are numbered to control execution order:

| Range | Type | Description | Drop Behavior |
|-------|------|-------------|---------------|
| `0xx` | Source tables & Mapping tables | Persistent tables for imported data and migration state. | **NEVER DROP** tables. |
| `1xx` | Entity normalization views | Views that extract distinct entities from denormalized raw export tables. | Dropped and recreated. |
| `2xx` | Shared business logic views | Intermediate views centralizing transform/derivation logic used by both Target and Report views (DRY). | Dropped and recreated. |
| `3xx` | Target views | Views that generate API payloads for Ampeco. | Dropped and recreated. |
| `4xx` | Report views | Views for analysis, export, and quality checks. | Dropped and recreated. |

## Source Tables

Source data is imported from SharePoint Excel files via `FetchFromSharePoint.py`, configured in `sharepoint_sync.json`. Column definitions are in `sharepoint_sync.json` under each file's `columns` array.

### Operational Data (`Source.*` schema)

Imported by `--import-sleet-data` (list defined in `SLEET_DATA_FILES` in `BuildProjectSleetDatabase.py`):

| DB Table | Source File | PK | Rows (approx) |
|----------|------------|----|---:|
| `Source.Locations` | Sleet Active Locations NO_PriceList_03.02 1.xlsx | `location_guid` | 179 |
| `Source.Controllers` | Sleet - Main Active Controllers 04_02_2026 12_12_25.xlsx | `controller_guid` | 159 |
| `Source.Clusters` | Sleet Active Clusters 04_02_2026 11_07_21.xlsx | `cluster_guid` | 403 |
| `Source.Chargers` | Sleet Active Chargers NO 13_02_2026 13_05_20.xlsx | `charger_guid` | 2,436 |
| `Source.Connectors` | Sleet Main - Connectors 13_02_2026 13_06_38.xlsx | `connector_guid` | 2,607 |
| `Source.EvseId` | Project Sleet Planning - Master.xlsx [EVSE_ID] | `evse_id` | — |
| `Source.RawChargerToChargerGroup` | ChargerToChargerGroup_Data_29_01.xlsx | — | 4,248 |
| `Source.RawChargerUsers` | Charger_User_All_Details_14-04-2026 - Signert pdd.xlsx | — | 122,785 |
| `Source.RawChargerUsers_Glencore` | Charger_User_Glencore 27.04.xlsx | — | — |
| `Source.PriceListItems` | Sleet Tariffs_03_02.xlsx [PriceListItems] | — | 166 |
| `Source.PriceList` | Sleet Tariffs_03_02.xlsx [PriceList] | `price_list_guid` | 89 |
| `Source.PriceToUsersAndChargers` | Sleet Tariffs_03_02.xlsx [PriceToUsersAndChargers] | — | 308 |
| `Source.RFIDTags_Gardermoen` | Sleet Active RFID Tags_Gardermoen Leiebilservice AS _19.02.2026.xlsx | `rfid_tag_guid` | 182 |
| `Source.RFIDTags_Geomatikk` | Active RFID Tags 24_02_2026 10_29_09.xlsx | `rfid_tag_guid` | 27 |
| `Source.RFIDTags_AutoleieOslo` | Sleet Active RFID Tags 04_03_2026 12_36_29.xlsx | `rfid_tag_guid` | 14 |
| `Source.ExcelRouters` | Project Sleet Planning - Master.xlsx [Routers] | `router_id` | 172 |
| `Source.ExcelMeters` | Project Sleet Planning - Master.xlsx [Meters] | `meter_id` | 172 |

Imported by `--import-teltonika` (via `FetchFromTeltonika.py`):

| DB Table | Source | PK | Rows (approx) |
|----------|--------|----|---:|
| `Source.TeltonikaDevices` | Teltonika RMS API (`GET /api/devices`) | `id` | 1,042 |

### Static / Derived Data (`Source.*` schema)

Loaded via DDL files (not SharePoint imports):

| DB Table | Source | PK | Rows (approx) |
|----------|--------|----|---:|
| `Source.EvseId` | Project Sleet Planning - Master.xlsx [EVSE_ID] | `evse_id` | — |
| `Source.GeocodedLocations` | Geoapify batch geocoding (2026-02-18) | `location_guid` | 179 |

### Master Planning Data (`Mapping.*` schema)

Imported by `--import-master` (file name set in `.env` as `sharepoint_file_name`):

| DB Table | Source File | PK |
|----------|------------|----|
| `Mapping.ExcelPlanningData` | Project Sleet Planning - Master.xlsx | `location_guid` |

Post-import SQL in `sharepoint_sync.json` populates `Mapping.location_mapping` from this data.

## Entity Normalization (1xx Views)

The raw export tables (`RawChargerToChargerGroup`, `RawChargerUsers`) are denormalized access matrices with heavily duplicated dimension data. The 1xx views extract distinct entities so downstream views can JOIN cleanly.

| View | Source Table(s) | Entity | Approx Rows |
|------|----------------|--------|---:|
| `Source.ChargerToChargerGroup` | `RawChargerToChargerGroup` | Bridge: Charger → ChargerGroup (deduplicated) | 2,310 |
| `Source.ChargerGroups` | `RawChargerToChargerGroup`, `PriceToUsersAndChargers` | Charger groups for pricing | 157 |
| `Source.ChargerUserGroupMemberships` | `RawChargerUsers` | Bridge: ChargerUser → ChargerUserGroup | 2,634 |
| `Source.RFIDs` | `RawChargerUsers`, `CorporateRFIDTags` | Physical RFID tags from both sources (118 shared across multiple users) | ~2,637 |
| `Source.ChargerUserGroups` | `RawChargerUsers`, `PriceToUsersAndChargers` | User groups for access control & pricing | 186 |
| `Source.ChargerUsers` | `RawChargerUsers` | Charging identities (EV drivers) | 2,582 |
| `Source.ChargerUserAccounts` | `RawChargerUsers` | Accounts owning charger users | 1,384 |
| `Source.CorporateRFIDTags` | `RFIDTags_*` + `ChargerUserAccounts` | Merged corporate RFID tags with account GUID resolution and hard-coded location link | ~209 |

### Entity Relationship Paths

Direction of arrow indicates where the FK points to.

**Infrastructure → Pricing:**
```
Location ← Charger ← ChargerToChargerGroup → ChargerGroup ← PriceToUsersAndChargers → PriceList ← PriceListItem
```

**Users → Pricing:**
```
Account (Charger User Account) ← ChargerUser → ChargerUserGroup ← PriceToUsersAndChargers → PriceList ← PriceListItem
```

**Users → RFID:**
```
Account (Charger User Account) ← ChargerUser → RFID
```

See `doc/project-sleet/report_v3.md` for full entity model diagrams and data dictionary.

## File Inventory

### 0xx — Source Tables & Mapping Tables

| File | Description |
|------|-------------|
| `005_source_evseid.sql` | EVSE ID lookup table |
| `006_mapping_chargerproductlookup.sql` | Charger product mapping |
| `007_source_geocoded_locations.sql` | Geocoded location coordinates (Geoapify fallback) |
| `011_location_mapping.sql` | Location migration mapping |
| `012_project_code_mapping.sql` | Project code mapping |
| `013_charger_mapping.sql` | Charger migration mapping |
| `014_connector_mapping.sql` | Connector migration mapping |
| `015_circuit_mapping.sql` | Circuit and electricity meter mapping |
| `016_user_mapping.sql` | User migration mapping |
| `017_rfid_mapping.sql` | RFID migration mapping |
| `018_user_group_mapping.sql` | User group migration mapping |
| `019_partner_admin_mapping.sql` | Partner admin mapping |
| `020_tariff_group_mapping.sql` | Tariff group and tariff mapping tables for pricing-derived tariff groups |
| `021_billing_partner_mapping.sql` | Billing partner mapping for corporate billing accounts (EHF/E-invoicing) |
| `022_sitetracker_mapping.sql` | SiteTracker mapping tables (sites, accounts, site relations, field assets) |
| `090_source_data_patches.sql` | Patches for missing/incorrect values in imported source tables |

### 1xx — Entity Normalization Views

| File | View Created | Description |
|------|-------------|-------------|
| `101_source_charger_to_charger_group.sql` | `Source.ChargerToChargerGroup` | Deduplicated bridge Charger → ChargerGroup |
| `102_source_charger_groups.sql` | `Source.ChargerGroups` | Distinct charger groups from multiple sources |
| `103_source_charger_user_group_memberships.sql` | `Source.ChargerUserGroupMemberships` | Bridge ChargerUser → ChargerUserGroup |
| `105_source_charger_user_groups.sql` | `Source.ChargerUserGroups` | User groups from RawChargerUsers + PriceToUsersAndChargers |
| `106_source_charger_users.sql` | `Source.ChargerUsers` | Deduplicated charging identities |
| `107_source_charger_user_accounts.sql` | `Source.ChargerUserAccounts` | Deduplicated accounts from RawChargerUsers |
| `108_source_corporate_rfid_tags.sql` | `Source.CorporateRFIDTags` | Merged corporate RFID tag files with account GUID resolution and location link |
| `109_source_rfids.sql` | `Source.RFIDs` | Distinct RFID tags from RawChargerUsers + CorporateRFIDTags (dual-source UNION ALL) |

### 2xx — Shared Business Logic Views

| File | View Created | Description |
|------|-------------|-------------|
| `201_source_electrical_settings.sql` | `Source.ElectricalSettingsNormalized` | Electrical settings (phase rotation, voltage, configuration) at connector grain. Single source of truth for ChargePoints, EvseAndConnectors, and CircuitQualityIssues. |
| `202_master_partner_resolution.sql` | `Mapping.MasterPartnerResolution` | Master partner resolution at location grain. One row per source location with per-project-code `target_partner_id`, org-level `master_target_partner_id`, `grouping_key`, and `partner_count`. Centralises the pcm JOIN and grouping-key logic so consumer views (303, 310, etc.) use a single simple JOIN. |
| `203_source_evse_product.sql` | `Source.EvseProduct` | EVSE-level product derivation (AC/DC → product name). One row per connector. |
| `204_source_evse_pricing.sql` | `Source.EvseTariffGroupAssignment`, `Source.EvseTariffRows` | Full pricing transformation: tariff group hash, pricing rules, spot/fixed detection, user group tariffs, quality flags. Materialized tables. |
| `205_source_billing_accounts.sql` | `Source.BillingAccounts` | Billing-eligible accounts (ChargerUserAccounts + CorporateRFIDTags) with EHF/E-invoicing, enriched with org data. Filtered to migrating locations. **Temporary:** excludes Private Persons. |

### 3xx — Target Views (API Payloads)

| File | View Created |
|------|-------------|
| `301_target_users.sql` | `Target.Users` |
| `302_target_idtags.sql` | `Target.IdTags` |
| `303_target_partners.sql` | `Target.Partners` |
| `304_target_partnercontracts.sql` | `Target.PartnerContracts` |
| `305_target_locations.sql` | `Target.Locations` |
| `306_target_chargingzones.sql` | `Target.ChargingZones` |
| `307_target_usergroups.sql` | `Target.UserGroups` |
| `308_target_usergroupmembers.sql` | `Target.UserGroupMembers` |
| `309_target_subscriptionplan.sql` | `Target.SubscriptionPlan` |
| `310_target_tariffgroupsandbasetariff.sql` | `Target.TariffGroupsAndBaseTariff` |
| `311_target_tariff_simple.sql` | `Target.Tariff_Simple` |
| `312_target_chargepoints.sql` | `Target.ChargePoints` |
| `313_target_evseandconnectors.sql` | `Target.EvseAndConnectors` |
| `314_target_partnerinvites.sql` | `Target.PartnerInvites` |
| `315_target_electricity_meters.sql` | `Target.ElectricityMeters` |
| `318_target_circuits.sql` | `Target.Circuits` |
| `319_target_chargepoint_circuit.sql` | `Target.ChargePointCircuitAttachment` |
| `320_target_partner_admins.sql` | `Target.PartnerAdmins` |
| `321_target_sitetracker_sites.sql` | `Target.SiteTrackerSites` |
| `322_target_sitetracker_accounts.sql` | `Target.SiteTrackerAccounts` |
| `323_target_sitetracker_site_relations.sql` | `Target.SiteTrackerSiteRelations` |
| `324_target_sitetracker_field_assets.sql` | `Target._SiteTrackerFieldAssets_Chargers`, `Target._SiteTrackerFieldAssets_Routers`, `Target._SiteTrackerFieldAssets_SIMs`, `Target.SiteTrackerFieldAssets` (UNION ALL) |

### 4xx — Report Views

| File | View Created |
|------|-------------|
| `401_reports_evse_physical_ref.sql` | `Reports.AllEvseWithPhysicalReference` |
| `402_reports_circuit_quality.sql` | `Reports.CircuitQualityIssues` |
| `403_reports_perific_organisations.sql` | `Reports.PerificOrganisationsMasked`, `Reports.PerificOrganisationsUnmasked` |
| `404_reports_teltonika.sql` | `Reports.TeltonikaRouters` |
| `405_reports_electricity_meters.sql` | `Reports.ElectricityMeters` |
| `406_reports_idtag_quality.sql` | `Reports.IdTagQualityIssues` |
| `407_reports_location_migration_status.sql` | `Reports.LocationMigrationStatus` |
| `408_reports_partner_admins.sql` | `Reports.PartnerAdmins` |
| `409_reports_tariff_quality.sql` | `Reports.TariffQuality` |
| `410_reports_billing_quality.sql` | `Reports.CorporateBillingAccounts` |

## Building the Database

Use `BuildProjectSleetDatabase.py` in the project root.

### Full Pipeline (default)

```bash
python BuildProjectSleetDatabase.py
```

1. Import Sleet operational data (17 source files)
2. Execute all DDL files in numbered order
3. Import Master planning file
4. Output row counts for all views
5. Check quality reports

### Options

| Option | Description |
|--------|-------------|
| `--ddl-only` | Only run DDL files, skip data imports. |
| `--ddl-file <name>` | Run a specific DDL file (e.g., `--ddl-file 315_target_electricity_meters.sql`). |
| `--import-master` | Only import Master planning file. |
| `--import-sleet-data` | Only import operational data files. |
| `--import-teltonika` | Only import devices from Teltonika RMS API. |
| `--export-view <Schema.View>` | Export a view to CSV (requires `--export-file`). |
| `--export-file <path>` | Output CSV file path. |

### Examples

```bash
# Rebuild views only (fast)
python BuildProjectSleetDatabase.py --ddl-only

# Test a specific DDL file
python BuildProjectSleetDatabase.py --ddl-only --ddl-file 303_target_partners.sql

# Export a report
python BuildProjectSleetDatabase.py --export-view Reports.ElectricityMeters --export-file data/meters.csv
```

## Ad-Hoc SQL Queries

Use the Pylance MCP `pylanceRunCodeSnippet` tool to avoid shell quoting issues:

```python
from utils.config_utils import get_db_connection_string
from utils.dbutils import get_db_connection

conn = get_db_connection(get_db_connection_string())
cursor = conn.cursor()
cursor.execute('SELECT * FROM "Target"."ElectricityMeters" LIMIT 5')
for row in cursor.fetchall():
    print(row)
cursor.close()
conn.close()
```

## Guidelines

### SET ROLE

All DDL files must include `SET ROLE db_sleetmigration_owner;` at the top. The build script prepends it automatically if missing.

### Mapping Tables (0xx)

- **NEVER DROP** — these store migration state and target system IDs.
- Use `CREATE TABLE IF NOT EXISTS` for initial creation.
- Add columns with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

### Views (1xx–4xx)

- Always `DROP VIEW IF EXISTS` before `CREATE OR REPLACE VIEW`.
- Target views (3xx): include `mapping_table` and `mapping_key` columns for Python scripts.
- Report views (4xx): for human consumption and exports, not API calls.

### Execution Order

Files execute in numerical order: 0xx → 1xx → 2xx → 3xx → 4xx. Mapping tables preserve data; all views are dropped and recreated.
