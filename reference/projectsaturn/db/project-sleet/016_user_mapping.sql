-- ============================================================================
-- MAPPING TABLE: user_mapping (snake_case, key-based)
-- ============================================================================
-- WARNING: NEVER DROP THIS TABLE - stores migration state and target system IDs
-- 
-- This is a PERSISTENT mapping table. Data is preserved across migrations.
-- - Use CREATE TABLE IF NOT EXISTS for initial creation
-- - To add new columns, use ALTER TABLE ... ADD COLUMN IF NOT EXISTS syntax:
--   ALTER TABLE "Mapping"."user_mapping" ADD COLUMN IF NOT EXISTS "new_column" TYPE;
--
-- Key format: Sleet|Account|{account_owner_guid}
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Create user_mapping table for Project Sleet
-- Maps Source.ChargerUserAccounts to Ampeco Users
CREATE TABLE IF NOT EXISTS "Mapping"."user_mapping" (
    -- Primary key (format: Sleet|Account|{account_owner_guid})
    mapping_key TEXT PRIMARY KEY,
    
    -- Target ID (populated after creation in target system)
    target_user_id INTEGER,
    
    -- Email used when creating the user (for audit/debugging)
    created_with_email TEXT,
    
    -- Whether the account was newly created in Ampeco (vs found existing)
    account_created_in_ampeco BOOLEAN,
    
    -- One-time password generated for newly created accounts
    one_time_password TEXT
);
