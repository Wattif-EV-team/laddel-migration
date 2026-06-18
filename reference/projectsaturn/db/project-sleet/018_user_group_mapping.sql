-- ============================================================================
-- MAPPING TABLE: user_group_mapping (snake_case, key-based)
-- ============================================================================
-- WARNING: NEVER DROP THIS TABLE - stores migration state and target system IDs
-- 
-- This is a PERSISTENT mapping table. Data is preserved across migrations.
-- - Use CREATE TABLE IF NOT EXISTS for initial creation
-- - To add new columns, use ALTER TABLE ... ADD COLUMN IF NOT EXISTS syntax:
--   ALTER TABLE "Mapping"."user_group_mapping" ADD COLUMN IF NOT EXISTS "new_column" TYPE;
--
-- Key format: Sleet|UserGroup|{charger_user_group_guid}
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Create user_group_mapping table for Project Sleet
-- Maps Source.ChargerUserGroups to Ampeco User Groups
CREATE TABLE IF NOT EXISTS "Mapping"."user_group_mapping" (
    -- Primary key (format: Sleet|UserGroup|{charger_user_group_guid})
    mapping_key TEXT PRIMARY KEY,
    
    -- Target ID (populated after creation in target system)
    target_user_group_id INTEGER
);
