-- ============================================================================
-- View: target.facility_external_id
-- Shared business-logic view (2xx). Drop-and-recreate. Reads only from the
-- read-only `laddel` source database.
--
-- Grain: one row per `laddel.facility`. Centralises the externalId scheme
-- (`W047L` + zero-padded facility_id) so every downstream view uses the exact
-- same derivation instead of re-deriving it independently. The source
-- `facility.migration_project_code` column is deliberately NOT used. Currently
-- consumed by:
--   - 304_target_location.sql                    (Ampeco Location externalId)
--   - 401_report_facility_migration_status.sql    (report project_code)
-- ============================================================================
DROP VIEW IF EXISTS `target`.`facility_external_id`;

CREATE OR REPLACE VIEW `target`.`facility_external_id` AS
SELECT
    f.facility_id                                AS facility_id,
    CONCAT('W047L', LPAD(f.facility_id, 4, '0'))  AS external_id
FROM `laddel`.`facility` f;
