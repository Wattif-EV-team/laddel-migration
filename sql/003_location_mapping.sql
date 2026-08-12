-- ============================================================================
-- MAPPING TABLE: target.location_mapping
-- ============================================================================
-- ⚠️  NEVER DROP THIS TABLE — it stores migration state and target-system IDs.
--
-- Persistent mapping table (data preserved across migrations):
--   • Create with CREATE TABLE IF NOT EXISTS (never DROP + recreate).
--   • Add columns with a guarded ALTER TABLE — MySQL 8 has NO
--     `ADD COLUMN IF NOT EXISTS`, so check information_schema first (the
--     pattern below) rather than dropping the table.
--
-- Key format: Laddel|Location|{facility_id}  (one location per facility).
--
-- Written by the create-or-update location script after each create: one INSERT
-- per location. The id is joined back into `target`.`location`.
--
-- Locations have no reliable natural key in Ampeco, so we never look up / adopt
-- pre-existing records — every write is a fresh create. The adoption columns
-- (existed_before / matched_by / snapshot) are therefore omitted.
-- ============================================================================

CREATE TABLE IF NOT EXISTS `target`.`location_mapping` (
    -- Composite key emitted by `target`.`location` (Laddel|Location|{facility_id}).
    mapping_key                       VARCHAR(255) NOT NULL,

    -- Ampeco location id returned on create (numeric in Ampeco).
    target_location_id                BIGINT       NULL,

    PRIMARY KEY (mapping_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
