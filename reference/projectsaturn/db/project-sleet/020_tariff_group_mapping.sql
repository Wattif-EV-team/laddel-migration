-- ============================================================================
-- MAPPING TABLE: tariff_group_mapping and tariff_mapping (snake_case, key-based)
-- ============================================================================
-- WARNING: NEVER DROP THIS TABLE - stores migration state and target system IDs
-- 
-- This is a PERSISTENT mapping table. Data is preserved across migrations.
-- - Use CREATE TABLE IF NOT EXISTS for initial creation
-- - To add new columns, use ALTER TABLE ... ADD COLUMN IF NOT EXISTS syntax:
--   ALTER TABLE "Mapping"."tariff_group_mapping" ADD COLUMN IF NOT EXISTS "new_column" TYPE;
--
-- tariff_group_mapping key format: Sleet|TariffGroup|{grouping_key}:{tariff_group_hash}
-- tariff_mapping key format:      Sleet|Tariff|{grouping_key}:{tariff_group_hash}|{tariff_order}
--
-- Tariff groups are scoped per organisation (grouping_key from MasterPartnerResolution)
-- crossed with pricing profile (MD5 hash). Locations in the same org with
-- identical pricing share a single tariff group.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- ============================================================================
-- Table 1: tariff_group_mapping
-- Maps pricing-derived tariff groups to target CSMS tariff group + base tariff IDs
-- ============================================================================
CREATE TABLE IF NOT EXISTS "Mapping"."tariff_group_mapping" (
    -- Primary key (format: Sleet|TariffGroup|{grouping_key}:{tariff_group_hash})
    mapping_key TEXT PRIMARY KEY,

    -- Target IDs (populated after creation in target CSMS)
    target_tariff_group_id INTEGER,
    target_tariff_base_id INTEGER,

    -- Derivation metadata (informational, not used for lookups)
    tariff_group_hash TEXT,                 -- UPPER MD5 of canonical pricing signature
    grouping_key TEXT,                      -- Organisation scoping key (from MasterPartnerResolution)
    location_guid TEXT,                     -- Representative location GUID (for naming)
    product TEXT                            -- Product type (Flexi Lading, etc.)
);

-- ============================================================================
-- Table 2: tariff_mapping
-- Maps individual tariffs (within a tariff group) to target CSMS tariff IDs
-- ============================================================================
CREATE TABLE IF NOT EXISTS "Mapping"."tariff_mapping" (
    -- Primary key (format: Sleet|Tariff|{grouping_key}:{tariff_group_hash}|{tariff_order})
    mapping_key TEXT PRIMARY KEY,

    -- Target ID (populated after creation in target CSMS)
    target_tariff_id INTEGER,

    -- Derivation metadata (informational)
    tariff_order INTEGER,                   -- 1=Standard, 2=Ad-hoc, 3..N=UserGroup
    tariff_kind TEXT                         -- 'standard', 'adhoc', 'usergroup'
);
