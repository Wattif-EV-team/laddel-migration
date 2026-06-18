-- ============================================================================
-- MAPPING TABLE: billing_partner_mapping (snake_case, key-based)
-- ============================================================================
-- WARNING: NEVER DROP THIS TABLE - stores migration state and target system IDs
-- 
-- This is a PERSISTENT mapping table. Data is preserved across migrations.
-- - Use CREATE TABLE IF NOT EXISTS for initial creation
-- - To add new columns, use ALTER TABLE ... ADD COLUMN IF NOT EXISTS syntax:
--   ALTER TABLE "Mapping"."billing_partner_mapping" ADD COLUMN IF NOT EXISTS "new_column" TYPE;
--
-- Key format: Sleet|BillingPartner|{account_owner_guid}
--
-- Stores target partner IDs for corporate billing partners created from
-- ChargerUserAccounts with invoice_distribution = 'E-invoicing' or 'EHF'.
-- These are separate partners from infrastructure partners even if the
-- underlying organisation is the same.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

CREATE TABLE IF NOT EXISTS "Mapping"."billing_partner_mapping" (
    -- Primary key (format: Sleet|BillingPartner|{account_owner_guid})
    mapping_key TEXT PRIMARY KEY,

    -- Source identifier for convenience
    account_owner_guid TEXT,

    -- Target partner ID (populated after creation in Ampeco)
    target_partner_id INTEGER
);

-- All billing partners own their RFID tags (partnerId set in Target.IdTags).
-- Legacy column kept for backwards compatibility; default changed to TRUE.
ALTER TABLE "Mapping"."billing_partner_mapping"
    ADD COLUMN IF NOT EXISTS partner_owns_rfid_tags BOOLEAN NOT NULL DEFAULT TRUE;

UPDATE "Mapping"."billing_partner_mapping"
   SET partner_owns_rfid_tags = TRUE
 WHERE partner_owns_rfid_tags = FALSE;
