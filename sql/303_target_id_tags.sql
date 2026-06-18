-- ============================================================================
-- View: target.id_tags
-- Depends on: (source tables only)
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`id_tags`;

CREATE OR REPLACE VIEW `target`.`id_tags` AS
SELECT
    `laddel`.`rfid`.`rfid_id` AS `rfid_id`,
    `laddel`.`rfid`.`rfid_name` AS `rfid_name`,
    `laddel`.`rfid`.`identity_id` AS `identity_id`,
    `laddel`.`rfid`.`rfid_payment_class` AS `rfid_payment_class`,
    `laddel`.`rfid`.`payment_id` AS `payment_id`,
    `laddel`.`rfid`.`registered_ev_id` AS `registered_ev_id`,
    `laddel`.`rfid`.`request_private_session` AS `request_private_session`
FROM `laddel`.`rfid`;
