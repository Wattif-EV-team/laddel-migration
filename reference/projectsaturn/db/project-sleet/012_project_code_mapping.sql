-- ============================================================================
-- MAPPING TABLE: project_code_mapping (snake_case, key-based)
-- ============================================================================
-- WARNING: NEVER DROP THIS TABLE - stores migration state and target system IDs
-- 
-- This is a PERSISTENT mapping table. Data is preserved across migrations.
-- - Use CREATE TABLE IF NOT EXISTS for initial creation
-- - To add new columns, use ALTER TABLE ... ADD COLUMN IF NOT EXISTS syntax:
--   ALTER TABLE "Mapping"."project_code_mapping" ADD COLUMN IF NOT EXISTS "new_column" TYPE;
--
-- Key format: Sleet|ProjectCode|{project_code}
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Create project_code_mapping table for Project Sleet
CREATE TABLE IF NOT EXISTS "Mapping"."project_code_mapping" (
    -- Primary key (format: Sleet|ProjectCode|{code})
    mapping_key TEXT PRIMARY KEY,
    
    -- Convenience column for easy access
    project_code TEXT,
    
    -- Target IDs
    target_partner_id INTEGER,
    target_subscription_plan_id INTEGER
);
