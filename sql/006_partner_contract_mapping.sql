-- ============================================================================
-- MAPPING TABLE: target.partner_contract_mapping
-- ============================================================================
-- ⚠️  NEVER DROP THIS TABLE — it stores migration state and target-system IDs.
--
-- Persistent mapping table (data preserved across migrations):
--   • Create with CREATE TABLE IF NOT EXISTS (never DROP + recreate).
--   • Add columns with a guarded ALTER TABLE — MySQL 8 has NO
--     `ADD COLUMN IF NOT EXISTS`, so check information_schema first (the
--     pattern below) rather than dropping the table.
--
-- Key format: Laddel|Facility|{facility_id}  (one partner contract per
-- facility). The key uses the SOURCE table name (`laddel.facility`), not the
-- target-system entity name — see MigrationPatternGuide.md §5.1. The same
-- segment is used by `target`.`location_mapping` and
-- `target`.`sitetracker_site_mapping`: same grain, different mapping table,
-- no collision.
--
-- Written by the create-or-update partner contract step after each create: one
-- INSERT per contract. The id is joined back into
-- `target`.`partner_contracts`.
--
-- Partner contracts have no reliable natural key in Ampeco, so we never look up
-- / adopt pre-existing records — every write is a fresh create. The adoption
-- columns (existed_before / matched_by / snapshot) are therefore omitted, just
-- as they are for partners (001) and locations (003).
-- ============================================================================

CREATE TABLE IF NOT EXISTS `target`.`partner_contract_mapping` (
    -- Composite key emitted by `target`.`partner_contracts`
    -- (Laddel|Facility|{facility_id}).
    mapping_key                       VARCHAR(255) NOT NULL,

    -- Ampeco partner contract id returned on create (numeric in Ampeco).
    target_partner_contract_id        BIGINT       NULL,

    PRIMARY KEY (mapping_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
