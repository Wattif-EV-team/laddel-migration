-- ============================================================================
-- MAPPING TABLE: target.partner_mapping
-- ============================================================================
-- ⚠️  NEVER DROP THIS TABLE — it stores migration state and target-system IDs.
--
-- Persistent mapping table (data preserved across migrations):
--   • Create with CREATE TABLE IF NOT EXISTS (never DROP + recreate).
--   • Add columns with a guarded ALTER TABLE — MySQL 8 has NO
--     `ADD COLUMN IF NOT EXISTS`, so check information_schema first (the
--     pattern below) rather than dropping the table.
--
-- Key format: Laddel|Facility|{facility_id}  (one partner per facility).
--
-- Written by the create-or-update partner script after each create: one INSERT
-- per partner. The id is joined back into `target`.`partners`.
--
-- Partners have no reliable natural key in Ampeco, so we never look up / adopt
-- pre-existing records — every write is a fresh create. The adoption columns
-- (existed_before / matched_by / snapshot) are therefore omitted.
-- ============================================================================

CREATE TABLE IF NOT EXISTS `target`.`partner_mapping` (
    -- Composite key emitted by `target`.`partners` (Laddel|Facility|{facility_id}).
    mapping_key                       VARCHAR(255) NOT NULL,

    -- Ampeco partner id returned on create (numeric in Ampeco).
    target_partner_id                 BIGINT       NULL,

    PRIMARY KEY (mapping_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
