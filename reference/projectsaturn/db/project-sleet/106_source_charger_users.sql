-- ============================================================================
-- View: Source.ChargerUsers
-- Description: Charging identity records (EV drivers) extracted from 
--              RawChargerUsers denormalized export.
-- Grain: One row per charger_user_guid (2,582 expected)
-- Source: Source.RawChargerUsers
-- ============================================================================
SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Source"."ChargerUsersNormalized" CASCADE;
DROP VIEW IF EXISTS "Source"."ChargerUsers" CASCADE;
CREATE OR REPLACE VIEW "Source"."ChargerUsers" AS
SELECT DISTINCT ON (charger_user_guid)
    charger_user_guid,
    charger_user_name,
    account_owner_guid,
    rfid_guid,
    created_on
FROM "Source"."AllRawChargerUsers"
WHERE charger_user_guid IS NOT NULL
-- Order to prefer rows with non-empty charger_user_name, then by created_on
ORDER BY charger_user_guid, 
         (CASE WHEN charger_user_name IS NOT NULL AND charger_user_name != '' THEN 0 ELSE 1 END),
         created_on;
