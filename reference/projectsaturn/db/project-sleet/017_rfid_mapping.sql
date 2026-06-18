-- ============================================================================
-- MAPPING TABLE: rfid_mapping (snake_case, key-based)
-- ============================================================================
-- WARNING: NEVER DROP THIS TABLE - stores migration state and target system IDs
-- 
-- This is a PERSISTENT mapping table. Data is preserved across migrations.
-- - Use CREATE TABLE IF NOT EXISTS for initial creation
-- - To add new columns, use ALTER TABLE ... ADD COLUMN IF NOT EXISTS syntax:
--   ALTER TABLE "Mapping"."rfid_mapping" ADD COLUMN IF NOT EXISTS "new_column" TYPE;
--
-- Key format: Sleet|RFID|{rfid_guid}
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Create rfid_mapping table for Project Sleet
-- Maps Source.RFIDs to Ampeco IdTags
CREATE TABLE IF NOT EXISTS "Mapping"."rfid_mapping" (
    -- Primary key (format: Sleet|RFID|{rfid_guid})
    mapping_key TEXT PRIMARY KEY,
    
    -- Target ID (populated after creation in target system)
    target_idtag_id INTEGER,
    
    -- UID used when creating the IdTag (for audit/debugging)
    created_with_uid TEXT
);

-- Add provenance and user-ID match columns (idempotent)
ALTER TABLE "Mapping"."rfid_mapping" ADD COLUMN IF NOT EXISTS idtag_created_in_ampeco BOOLEAN;
ALTER TABLE "Mapping"."rfid_mapping" ADD COLUMN IF NOT EXISTS user_id_match BOOLEAN;
