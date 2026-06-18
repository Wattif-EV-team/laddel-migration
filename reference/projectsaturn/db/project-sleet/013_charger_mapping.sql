-- ============================================================================
-- MAPPING TABLE: charger_mapping (snake_case, key-based)
-- ============================================================================
-- WARNING: NEVER DROP THIS TABLE - stores migration state and target system IDs
-- 
-- This is a PERSISTENT mapping table. Data is preserved across migrations.
-- - Use CREATE TABLE IF NOT EXISTS for initial creation
-- - To add new columns, use ALTER TABLE ... ADD COLUMN IF NOT EXISTS syntax:
--   ALTER TABLE "Mapping"."charger_mapping" ADD COLUMN IF NOT EXISTS "new_column" TYPE;
--
-- Key format: Sleet|Charger|{charger_guid}
-- Note: Uses GUID-based key (not integer charger_id from old table)
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Create charger_mapping table for Project Sleet
-- Maps Source.Chargers to Target ChargePoints (1:1 relationship)
CREATE TABLE IF NOT EXISTS "Mapping"."charger_mapping" (
    -- Primary key (format: Sleet|Charger|{charger_guid})
    mapping_key TEXT PRIMARY KEY,
    
    -- Target ID (populated after creation in target system)
    target_charge_point_id INTEGER
);
