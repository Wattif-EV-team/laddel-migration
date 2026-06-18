-- ============================================================================
-- MAPPING TABLE: partner_admin_mapping (snake_case, key-based)
-- ============================================================================
-- WARNING: NEVER DROP THIS TABLE - stores migration state and target system IDs
-- 
-- This is a PERSISTENT mapping table. Data is preserved across migrations.
-- - Use CREATE TABLE IF NOT EXISTS for initial creation
-- - To add new columns, use ALTER TABLE ... ADD COLUMN IF NOT EXISTS syntax:
--   ALTER TABLE "Mapping"."partner_admin_mapping" ADD COLUMN IF NOT EXISTS "new_column" TYPE;
--
-- Key format: Sleet|PartnerAdmin|{lower_email}
-- One row per unique email globally (Ampeco requires email uniqueness across all admins)
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Create partner_admin_mapping table for Project Sleet
-- Maps partner admin emails to Ampeco Partner Admins
CREATE TABLE IF NOT EXISTS "Mapping"."partner_admin_mapping" (
    -- Primary key (format: Sleet|PartnerAdmin|{lower_email})
    mapping_key TEXT PRIMARY KEY,
    
    -- The partner this admin was created under
    target_partner_id INTEGER,
    
    -- Target ID returned by API on creation
    target_partner_admin_id INTEGER,
    
    -- Email used when creating the admin (for audit/debugging)
    created_with_email TEXT,
    
    -- One-time password generated for newly created admin accounts
    one_time_password TEXT
);

-- Whether the admin was newly created in Ampeco (TRUE) or found existing (FALSE)
ALTER TABLE "Mapping"."partner_admin_mapping"
    ADD COLUMN IF NOT EXISTS account_created_in_ampeco BOOLEAN;

-- Backfill: mark all previously-created admins as created by us
UPDATE "Mapping"."partner_admin_mapping"
SET account_created_in_ampeco = TRUE
WHERE account_created_in_ampeco IS NULL
  AND target_partner_admin_id IS NOT NULL;
