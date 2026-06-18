-- ============================================================================
-- View: target.tariff_groups_and_base_tariff
-- Depends on: (source tables only)
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`tariff_groups_and_base_tariff`;

CREATE OR REPLACE VIEW `target`.`tariff_groups_and_base_tariff` AS
SELECT
    `laddel`.`price_information`.`price_id` AS `price_id`,
    `laddel`.`price_information`.`surCharge` AS `surCharge`,
    `laddel`.`price_information`.`deliveryFee` AS `deliveryFee`,
    `laddel`.`price_information`.`fixed_price` AS `fixed_price`,
    `laddel`.`price_information`.`bidding_zone_id` AS `bidding_zone_id`,
    `laddel`.`price_information`.`kw_effect` AS `kw_effect`,
    `laddel`.`price_information`.`surChargeKeepModifier` AS `surChargeKeepModifier`,
    `laddel`.`price_information`.`charging_fee` AS `charging_fee`,
    `laddel`.`price_information`.`markup` AS `markup`,
    `laddel`.`price_information`.`priceModel` AS `priceModel`,
    `laddel`.`price_information`.`rebate_threshold` AS `rebate_threshold`,
    `laddel`.`price_information`.`is_spot_price_with_vat` AS `is_spot_price_with_vat`,
    `laddel`.`price_information`.`dropInFeePercentage` AS `dropInFeePercentage`,
    `laddel`.`price_information`.`useMinutePrice` AS `useMinutePrice`,
    `laddel`.`price_information`.`minutePrice` AS `minutePrice`,
    `laddel`.`price_information`.`minutePriceDisabledStartTime` AS `minutePriceDisabledStartTime`,
    `laddel`.`price_information`.`minutePriceDisabledEndTime` AS `minutePriceDisabledEndTime`,
    `laddel`.`price_information`.`dropInFee` AS `dropInFee`,
    `laddel`.`price_information`.`subscription_monthly_fee_incl_vat` AS `subscription_monthly_fee_incl_vat`,
    `laddel`.`price_information`.`minutePriceGracePeriodMinutes` AS `minutePriceGracePeriodMinutes`
FROM `laddel`.`price_information`;
