-- ============================================================================
-- MAPPING TABLE: target.sitetracker_site_relation_mapping
-- ============================================================================
-- ⚠️  NEVER DROP THIS TABLE — it stores migration state and target-system IDs.
--
-- Persistent mapping table (data preserved across migrations):
--   • Create with CREATE TABLE IF NOT EXISTS (never DROP + recreate).
--   • Add columns with a guarded ALTER TABLE — MySQL 8 has NO
--     `ADD COLUMN IF NOT EXISTS`, so check information_schema first rather than
--     dropping the table.
--
-- Key format: Laddel|Facility|{facility_id}  (one relation per facility — see
-- docs/fieldmapping/sitetracker_site_relation.md for the verified 1:1
-- facility_contact business rule that makes this grain safe). The key uses
-- the SOURCE table name (`laddel.facility`), not the target-system entity
-- name — see MigrationPatternGuide.md §5.1.
--
-- Written by the create-or-update SiteTracker site-relation script after each
-- create: one INSERT per Site Relation.
--
-- Idempotency is by `mapping_key` only — same simple pattern as
-- `sitetracker_account_mapping` (002) and `sitetracker_site_mapping` (004): no
-- SOQL lookup/adopt, so the adoption columns (existed_before / matched_by /
-- snapshot) are omitted. See docs/fieldmapping/sitetracker_site_relation.md for
-- the accepted risk (a lost mapping write could create a duplicate on re-run).
-- ============================================================================

CREATE TABLE IF NOT EXISTS `target`.`sitetracker_site_relation_mapping` (
    -- Composite key emitted by `target`.`sitetracker_site_relations`
    -- (Laddel|Facility|{facility_id}).
    mapping_key                       VARCHAR(255) NOT NULL,

    -- Salesforce Site_Relation__c id returned on create. Salesforce ids are
    -- 15/18-char case-sensitive strings (NOT numeric), so this is a
    -- CHAR/VARCHAR, not BIGINT.
    target_sf_site_relation_id        VARCHAR(18)  NULL,

    PRIMARY KEY (mapping_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
