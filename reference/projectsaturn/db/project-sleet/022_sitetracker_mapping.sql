-- ============================================================================
-- MAPPING TABLES: SiteTracker Integration
-- ============================================================================
-- WARNING: NEVER DROP THESE TABLES - they store SiteTracker Salesforce record IDs
-- 
-- These are PERSISTENT mapping tables. Data is preserved across migrations.
-- - Use CREATE TABLE IF NOT EXISTS for initial creation
-- - To add new columns, use ALTER TABLE ... ADD COLUMN IF NOT EXISTS syntax
--
-- Three tables for the three SiteTracker entity types:
--   1. sitetracker_site_mapping — Sites (keyed by location)
--   2. sitetracker_account_mapping — Accounts/Companies (keyed by org grouping)
--   3. sitetracker_site_relation_mapping — Site Relations (keyed by site+role)
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- ── Table 1: Site Mapping ────────────────────────────────────────────────────
-- Maps Source locations to SiteTracker Site records
-- Key format: Sleet|Location|{location_guid}
CREATE TABLE IF NOT EXISTS "Mapping"."sitetracker_site_mapping" (
    mapping_key TEXT PRIMARY KEY,
    project_code TEXT,
    target_sf_site_id TEXT,
    site_existed_before_migration BOOLEAN,
    previous_record_snapshot JSONB
);

-- ── Table 2: Account Mapping ─────────────────────────────────────────────────
-- Maps organizations to SiteTracker Account records (one per org, not per project_code)
-- Key format: Sleet|SiteTrackerAccount|{grouping_key}
CREATE TABLE IF NOT EXISTS "Mapping"."sitetracker_account_mapping" (
    mapping_key TEXT PRIMARY KEY,
    grouping_key TEXT,
    org_number_normalized TEXT,
    target_sf_account_id TEXT,
    account_existed_before_migration BOOLEAN,
    matched_by TEXT,
    previous_record_snapshot JSONB
);

-- ── Table 3: Site Relation Mapping ───────────────────────────────────────────
-- Maps site+role combinations to SiteTracker Site_Relation__c records
-- Key format: Sleet|SiteRelation|{project_code}|{role}
CREATE TABLE IF NOT EXISTS "Mapping"."sitetracker_site_relation_mapping" (
    mapping_key TEXT PRIMARY KEY,
    target_sf_site_relation_id TEXT,
    relation_existed_before_migration BOOLEAN,
    previous_record_snapshot JSONB
);

-- ── Table 4: Field Asset Mapping ─────────────────────────────────────────────
-- Maps chargers to SiteTracker Field Asset records
-- Key format: Sleet|FieldAsset|{charger_guid}
CREATE TABLE IF NOT EXISTS "Mapping"."sitetracker_field_asset_mapping" (
    mapping_key TEXT PRIMARY KEY,
    target_sf_field_asset_id TEXT,
    asset_existed_before_migration BOOLEAN,
    previous_record_snapshot JSONB
);
