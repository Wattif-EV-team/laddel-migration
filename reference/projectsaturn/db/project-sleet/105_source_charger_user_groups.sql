-- ============================================================================
-- View: Source.ChargerUserGroups
-- Description: User groups for access control and pricing. Combined from 
--              RawChargerUsers (groups with members) and PriceToUsersAndChargers 
--              (all groups with pricing rules).
-- Grain: One row per charger_user_group_guid (186 as of 2026-02-06)
-- Source: Source.RawChargerUsers, Source.PriceToUsersAndChargers
-- ============================================================================
SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Source"."ChargerUserGroups" CASCADE;
CREATE OR REPLACE VIEW "Source"."ChargerUserGroups" AS
WITH groups_from_charger_users AS (
    -- Groups that have members (47 groups)
    SELECT DISTINCT ON (charger_user_group_guid)
        charger_user_group_guid,
        charger_user_group_name,
        charger_user_group_owner,
        charger_user_group_owner_account_number,
        TRUE AS has_members
    FROM "Source"."AllRawChargerUsers"
    WHERE charger_user_group_guid IS NOT NULL
    ORDER BY charger_user_group_guid
),
groups_from_pricing AS (
    -- All groups with pricing rules (187 groups, includes groups without members)
    -- Note: charger_user_group_guid is text type here, cast to uuid for join
    -- Filter out empty strings (61 rows have empty string instead of NULL)
    SELECT DISTINCT ON (charger_user_group_guid::uuid)
        charger_user_group_guid::uuid AS charger_user_group_guid,
        charger_user_group_name
    FROM "Source"."PriceToUsersAndChargers"
    WHERE charger_user_group_guid IS NOT NULL 
      AND charger_user_group_guid != ''
    ORDER BY charger_user_group_guid::uuid
)
SELECT
    COALESCE(p.charger_user_group_guid, cu.charger_user_group_guid) AS charger_user_group_guid,
    COALESCE(p.charger_user_group_name, cu.charger_user_group_name::text) AS charger_user_group_name,
    cu.charger_user_group_owner,
    cu.charger_user_group_owner_account_number,
    COALESCE(cu.has_members, FALSE) AS has_members
FROM groups_from_pricing p
FULL OUTER JOIN groups_from_charger_users cu 
    ON p.charger_user_group_guid = cu.charger_user_group_guid;
