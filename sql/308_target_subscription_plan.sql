-- ============================================================================
-- View: target.subscription_plan
-- Depends on: (source tables only)
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`subscription_plan`;

CREATE OR REPLACE VIEW `target`.`subscription_plan` AS
SELECT
    `laddel`.`facility_subscription`.`id` AS `id`,
    `laddel`.`facility_subscription`.`identity_id` AS `identity_id`,
    `laddel`.`facility_subscription`.`facility_id` AS `facility_id`,
    `laddel`.`facility_subscription`.`monthly_fee` AS `monthly_fee`,
    `laddel`.`facility_subscription`.`monthly_fee_currency` AS `monthly_fee_currency`,
    `laddel`.`facility_subscription`.`activation_date` AS `activation_date`,
    `laddel`.`facility_subscription`.`expiration_date` AS `expiration_date`,
    `laddel`.`facility_subscription`.`status` AS `status`,
    `laddel`.`facility_subscription`.`origin_subscription_id` AS `origin_subscription_id`,
    `laddel`.`facility_subscription`.`receipt_url` AS `receipt_url`
FROM `laddel`.`facility_subscription`;
