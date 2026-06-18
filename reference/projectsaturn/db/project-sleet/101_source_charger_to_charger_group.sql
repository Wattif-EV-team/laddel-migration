-- ============================================================================
-- View: Source.ChargerToChargerGroup
-- Description: Bridge table linking Chargers to ChargerGroups. Deduplicated 
--              from the 4,248-row RawChargerToChargerGroup export 
--              (which pre-joins with pricing data).
-- Grain: One row per charger_to_charger_group_guid (2,310 expected)
-- Source: Source.RawChargerToChargerGroup
-- ============================================================================
SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Source"."ChargerToChargerGroupBridge" CASCADE;
DROP VIEW IF EXISTS "Source"."ChargerToChargerGroupNormalized" CASCADE;
DROP VIEW IF EXISTS "Source"."ChargerToChargerGroup" CASCADE;
CREATE OR REPLACE VIEW "Source"."ChargerToChargerGroup" AS
SELECT DISTINCT ON (charger_to_charger_group_guid)
    charger_to_charger_group_guid,
    charger_to_charger_group_name,
    charger_guid,
    charger_group_guid,
    created_on
FROM "Source"."RawChargerToChargerGroup"
WHERE charger_to_charger_group_guid IS NOT NULL
ORDER BY charger_to_charger_group_guid, created_on;
