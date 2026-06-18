-- ============================================================================
-- View: target.users
-- Depends on: (source tables only)
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`users`;

CREATE OR REPLACE VIEW `target`.`users` AS
SELECT
    `laddel`.`email_identity`.`id` AS `id`,
    NULL AS `password`,
    `laddel`.`email_identity`.`provider_id` AS `provider_id`,
    `laddel`.`email_identity`.`rfid_tag` AS `rfid_tag`,
    `laddel`.`email_identity`.`email` AS `email`,
    `laddel`.`email_identity`.`firstname` AS `first_name`,
    `laddel`.`email_identity`.`lastname` AS `last_name`
FROM `laddel`.`email_identity`;
