-- ============================================================================
-- View: target.user_group_members
-- Depends on: (source tables only)
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`user_group_members`;

CREATE OR REPLACE VIEW `target`.`user_group_members` AS
SELECT
    `laddel`.`group_member`.`group_id` AS `group_id`,
    `laddel`.`group_member`.`identity_id` AS `identity_id`
FROM `laddel`.`group_member`;
