-- ============================================================================
-- View: target.charge_points
-- Depends on: (source tables only)
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`charge_points`;

CREATE OR REPLACE VIEW `target`.`charge_points` AS
SELECT
    `laddel`.`charger`.`charger_id` AS `charger_id`,
    `laddel`.`charger`.`charger_name` AS `charger_name`,
    `laddel`.`charger`.`active` AS `active`,
    `laddel`.`charger`.`brand` AS `brand`,
    `laddel`.`charger`.`facility_id` AS `facility_id`,
    `laddel`.`charger`.`charger_reference` AS `charger_reference`,
    `laddel`.`charger`.`ocpp_id` AS `ocpp_id`,
    `laddel`.`charger`.`socket_id` AS `socket_id`,
    `laddel`.`charger`.`installation_id` AS `installation_id`,
    `laddel`.`charger`.`creation_date` AS `creation_date`,
    `laddel`.`charger`.`use_ocpi_integration` AS `use_ocpi_integration`,
    `laddel`.`charger`.`is_whitelist_enabled` AS `is_whitelist_enabled`
FROM `laddel`.`charger`;
