-- ============================================================================
-- View: Source.ChargerGroups
-- Description: Charger groups for pricing assignment. Combined from 
--              ChargerToChargerGroup and PriceToUsersAndChargers.
--              1 group exists without pricing rules ("Skistar Hemsedal - Gratislading").
-- Grain: One row per charger_group_guid (157 expected)
-- Source: Source.RawChargerToChargerGroup, Source.PriceToUsersAndChargers
-- ============================================================================
SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Source"."ChargerGroups" CASCADE;
CREATE OR REPLACE VIEW "Source"."ChargerGroups" AS
WITH groups_from_bridge AS (
    -- ChargerGroups from RawChargerToChargerGroup (157 groups)
    SELECT DISTINCT ON (charger_group_guid)
        charger_group_guid,
        charger_group_name,
        account_owner_charger_group,
        account_number_owner_charger_group
    FROM "Source"."RawChargerToChargerGroup"
    WHERE charger_group_guid IS NOT NULL
    ORDER BY charger_group_guid
),
groups_from_pricing AS (
    -- ChargerGroups with pricing rules (156 groups)
    SELECT DISTINCT ON (charger_group_guid)
        charger_group_guid,
        charger_group_name
    FROM "Source"."PriceToUsersAndChargers"
    WHERE charger_group_guid IS NOT NULL
    ORDER BY charger_group_guid
)
SELECT
    COALESCE(b.charger_group_guid, p.charger_group_guid) AS charger_group_guid,
    COALESCE(b.charger_group_name, p.charger_group_name) AS charger_group_name,
    b.account_owner_charger_group,
    b.account_number_owner_charger_group,
    (p.charger_group_guid IS NOT NULL) AS has_pricing_rule
FROM groups_from_bridge b
FULL OUTER JOIN groups_from_pricing p 
    ON b.charger_group_guid = p.charger_group_guid;
