-- ============================================================================
-- View: target.user_groups
-- Depends on: (source tables only)
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`user_groups`;

CREATE OR REPLACE VIEW `target`.`user_groups` AS
SELECT
    `laddel`.`dashboard_group`.`id` AS `id`,
    `laddel`.`dashboard_group`.`name` AS `name`,
    `laddel`.`dashboard_group`.`facility_id` AS `facility_id`,
    `laddel`.`dashboard_group`.`price_id` AS `price_id`
FROM `laddel`.`dashboard_group`;
