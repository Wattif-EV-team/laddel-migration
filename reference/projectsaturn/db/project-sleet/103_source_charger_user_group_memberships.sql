-- ============================================================================
-- View: Source.ChargerUserGroupMemberships
-- Description: Bridge table linking ChargerUsers to ChargerUserGroups.
--              Composite PK - no explicit GUID in source system.
-- Grain: One row per (charger_user_guid, charger_user_group_guid) (2,634 expected)
-- Source: Source.RawChargerUsers
-- ============================================================================
SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Source"."ChargerUserGroupMemberships" CASCADE;
CREATE OR REPLACE VIEW "Source"."ChargerUserGroupMemberships" AS
SELECT DISTINCT
    charger_user_guid,
    charger_user_group_guid
FROM "Source"."AllRawChargerUsers"
WHERE charger_user_guid IS NOT NULL
  AND charger_user_group_guid IS NOT NULL;
