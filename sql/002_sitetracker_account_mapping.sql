-- ============================================================================
-- MAPPING TABLE: target.sitetracker_account_mapping
-- ============================================================================
-- ⚠️  NEVER DROP THIS TABLE — it stores migration state and target-system IDs.
--
-- Persistent mapping table (data preserved across migrations):
--   • Create with CREATE TABLE IF NOT EXISTS (never DROP + recreate).
--   • Add columns with a guarded ALTER TABLE — MySQL 8 has NO
--     `ADD COLUMN IF NOT EXISTS`, so check information_schema first rather than
--     dropping the table.
--
-- Key format: Laddel|Customer|{customer_id}  (one Account per customer). The
-- key uses the SOURCE table name (`laddel.customer`), not the target-system
-- entity name — see MigrationPatternGuide.md §5.1.
--
-- Written by the create-or-update SiteTracker account script after each create:
-- one INSERT per Account. The id is joined back into
-- `target`.`sitetracker_accounts`.
--
-- Idempotency is by `mapping_key` only — we do NOT SOQL-match / adopt
-- pre-existing Salesforce Accounts (see docs/fieldmapping/sitetracker_account.md),
-- so the adoption columns (existed_before / matched_by / snapshot) are omitted.
-- ============================================================================

CREATE TABLE IF NOT EXISTS `target`.`sitetracker_account_mapping` (
    -- Composite key emitted by `target`.`sitetracker_accounts`
    -- (Laddel|Customer|{customer_id}).
    mapping_key                       VARCHAR(255) NOT NULL,

    -- Salesforce Account id returned on create. Salesforce ids are 15/18-char
    -- case-sensitive strings (NOT numeric), so this is a CHAR/VARCHAR, not BIGINT.
    target_sf_account_id              VARCHAR(18)  NULL,

    PRIMARY KEY (mapping_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
