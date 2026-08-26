-- ============================================================================
-- MAPPING TABLE: target.sitetracker_site_mapping
-- ============================================================================
-- ⚠️  NEVER DROP THIS TABLE — it stores migration state and target-system IDs.
--
-- Persistent mapping table (data preserved across migrations):
--   • Create with CREATE TABLE IF NOT EXISTS (never DROP + recreate).
--   • Add columns with a guarded ALTER TABLE — MySQL 8 has NO
--     `ADD COLUMN IF NOT EXISTS`, so check information_schema first rather than
--     dropping the table.
--
-- Key format: Laddel|Facility|{facility_id}  (one Site per facility). The key
-- uses the SOURCE table name (`laddel.facility`), not the target-system entity
-- name — see MigrationPatternGuide.md §5.1.
--
-- Written by the create-or-update SiteTracker site script after each create:
-- one INSERT per Site. The id is joined back into `target`.`sitetracker_sites`
-- and, downstream, into `target`.`sitetracker_site_relations`.
--
-- Idempotency is by `mapping_key`, but Site `Name` must be unique in the target
-- org: an unmapped row is SOQL-looked-up by `Name` before create and, on a
-- match with the same project code, adopted (mapped to the existing Site)
-- instead of re-created — see docs/fieldmapping/sitetracker_site.md.
-- ============================================================================

CREATE TABLE IF NOT EXISTS `target`.`sitetracker_site_mapping` (
    -- Composite key emitted by `target`.`sitetracker_sites`
    -- (Laddel|Facility|{facility_id}).
    mapping_key                       VARCHAR(255) NOT NULL,

    -- Salesforce Site id returned on create. Salesforce ids are 15/18-char
    -- case-sensitive strings (NOT numeric), so this is a CHAR/VARCHAR, not BIGINT.
    target_sf_site_id                 VARCHAR(18)  NULL,

    PRIMARY KEY (mapping_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
