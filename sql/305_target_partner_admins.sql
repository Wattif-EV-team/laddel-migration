-- ============================================================================
-- View: target.partner_admins
-- Depends on: (source tables only)
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`partner_admins`;

CREATE OR REPLACE VIEW `target`.`partner_admins` AS
SELECT
    `laddel`.`dashboard_user`.`id` AS `id`,
    `laddel`.`dashboard_user`.`name` AS `name`,
    `laddel`.`dashboard_user`.`email` AS `email`,
    `laddel`.`dashboard_user`.`emailVerified` AS `emailVerified`,
    `laddel`.`dashboard_user`.`image` AS `image`,
    `laddel`.`dashboard_user`.`appUserId` AS `appUserId`,
    `laddel`.`dashboard_user`.`superadmin` AS `superadmin`,
    `laddel`.`dashboard_user`.`family_name` AS `family_name`,
    `laddel`.`dashboard_user`.`given_name` AS `given_name`,
    `laddel`.`dashboard_user`.`isStakeholder` AS `isStakeholder`,
    `laddel`.`dashboard_user`.`isOrgCreator` AS `isOrgCreator`
FROM `laddel`.`dashboard_user`;
