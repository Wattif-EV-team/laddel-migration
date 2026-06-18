-- ============================================================================
-- MAPPING TABLE: location_mapping (snake_case, key-based)
-- ============================================================================
-- WARNING: NEVER DROP THIS TABLE - stores migration state and target system IDs
-- 
-- This is a PERSISTENT mapping table. Data is preserved across migrations.
-- - Use CREATE TABLE IF NOT EXISTS for initial creation
-- - To add new columns, use ALTER TABLE ... ADD COLUMN IF NOT EXISTS syntax:
--   ALTER TABLE "Mapping"."location_mapping" ADD COLUMN IF NOT EXISTS "new_column" TYPE;
--
-- Key format: Sleet|Location|{location_guid}
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Create location_mapping table for Project Sleet
CREATE TABLE IF NOT EXISTS "Mapping"."location_mapping" (
    -- Primary key (format: Sleet|Location|{guid})
    mapping_key TEXT PRIMARY KEY,
    
    -- Merge support (format: Sleet|Location|{guid} or NULL)
    merge_with_mapping_key TEXT,
    
    -- Planning data (from ExcelPlanningData via sharepoint_sync)
    project_code TEXT,
    status TEXT,
    location_name TEXT,
    charging_zone_name TEXT,
    
    -- Partner settings
    partner_model TEXT,  -- WO-Resell, CO-Resell, CO-MDU
    partner_monthly_fee NUMERIC,
    partner_monthly_fee_description TEXT,
    
    -- Partner contract settings
    partner_contract_price_per_evse NUMERIC,
    partner_contract_operator_share_pct NUMERIC,
    partner_contract_partner_share_pct NUMERIC,  -- Calculated: 100 - operator_share_pct
    
    -- Migration flags
    migrate BOOLEAN DEFAULT FALSE,
    migrate_block_reason TEXT,
    exclude BOOLEAN DEFAULT TRUE,  -- Exclude from reports; set by post_import_sql based on status
    
    -- Target IDs (snake_case)
    target_location_id INTEGER,
    target_charge_zone_id INTEGER,
    target_partner_contract_id INTEGER,
    -- DEPRECATED: target_tariff_group_id and target_tariff_base_id are retained for
    -- backward compatibility but are no longer the source of truth for EVSE tariff group
    -- assignment. Tariff groups are now derived via Mapping.tariff_group_mapping, keyed by
    -- pricing-derived tariff_group_key. See 204_source_evse_pricing.sql and
    -- 310_target_tariffgroupsandbasetariff.sql.
    target_tariff_group_id INTEGER,
    target_tariff_base_id INTEGER,
    target_report_user_group_id INTEGER
);

-- Remove deprecated source_account_number column (was never populated; BlockConflictingAccountNumbers
-- now joins Source.Locations directly)
ALTER TABLE "Mapping"."location_mapping" DROP COLUMN IF EXISTS source_account_number;

-- Add exclude column if table already exists (incremental schema update)
ALTER TABLE "Mapping"."location_mapping" ADD COLUMN IF NOT EXISTS exclude BOOLEAN DEFAULT TRUE;

-- Add partner_admin_emails column for Partner Admin feature
ALTER TABLE "Mapping"."location_mapping" ADD COLUMN IF NOT EXISTS partner_admin_emails TEXT;

-- Add wattif_installer column for tracking which installer Wattif uses for migration
ALTER TABLE "Mapping"."location_mapping" ADD COLUMN IF NOT EXISTS wattif_installer TEXT;

-- Add migration_date column for planned migration date
ALTER TABLE "Mapping"."location_mapping" ADD COLUMN IF NOT EXISTS migration_date DATE;

-- Add load_to_sitetracker flag: TRUE when status IN ('Done','Ready') — controls SiteTracker target views
ALTER TABLE "Mapping"."location_mapping" ADD COLUMN IF NOT EXISTS load_to_sitetracker BOOLEAN DEFAULT FALSE;
