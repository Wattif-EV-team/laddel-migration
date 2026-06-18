-- ============================================================================
-- View: target.partner_contracts
-- Depends on: (source tables only)
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`partner_contracts`;

CREATE OR REPLACE VIEW `target`.`partner_contracts` AS
SELECT
    `laddel`.`organization_ev_fleet_information`.`organization_id` AS `organization_id`,
    `laddel`.`organization_ev_fleet_information`.`admin_fee_charging_percentage` AS `admin_fee_charging_percentage`,
    `laddel`.`organization_ev_fleet_information`.`admin_fee_homecharging_fixed` AS `admin_fee_homecharging_fixed`,
    `laddel`.`organization_ev_fleet_information`.`use_collective_invoice` AS `use_collective_invoice`,
    `laddel`.`organization_ev_fleet_information`.`default_capacity_level_excl_vat` AS `default_capacity_level_excl_vat`
FROM `laddel`.`organization_ev_fleet_information`;
