-- ============================================================================
-- View: target.partners
-- Depends on: (source tables only)
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`partners`;

CREATE OR REPLACE VIEW `target`.`partners` AS
SELECT
    `laddel`.`organization`.`organization_id` AS `organization_id`,
    `laddel`.`organization`.`organization_name` AS `organization_name`,
    `laddel`.`organization`.`organization_reference` AS `organization_reference`,
    `laddel`.`organization`.`enable_ev_fleet` AS `enable_ev_fleet`,
    `laddel`.`organization`.`parent_organization_id` AS `parent_organization_id`
FROM `laddel`.`organization`;
