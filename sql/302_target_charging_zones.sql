-- ============================================================================
-- View: target.charging_zones
-- Depends on: (source tables only)
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`charging_zones`;

CREATE OR REPLACE VIEW `target`.`charging_zones` AS
SELECT
    `laddel`.`facility`.`facility_id` AS `facility_id`,
    `laddel`.`facility`.`facility_name` AS `facility_name`,
    `laddel`.`facility`.`organization_id` AS `organization_id`,
    `laddel`.`facility`.`creation_date` AS `creation_date`,
    `laddel`.`facility`.`emabler_id` AS `emabler_id`,
    `laddel`.`facility`.`monthly_fee_per_active_charger_excl_vat` AS `monthly_fee_per_active_charger_excl_vat`
FROM `laddel`.`facility`;
