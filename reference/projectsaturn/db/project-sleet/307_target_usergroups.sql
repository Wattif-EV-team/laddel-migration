-- ============================================================================
-- TARGET VIEW: UserGroups (Phase C, Step 4)
-- ============================================================================
-- One row per eligible ChargerUserGroup (1:1, no duplication).
--
-- Eligibility: a user group is eligible if ANY of its linked locations has
-- migrate=TRUE (no merge filter — secondary merged locations count too).
--
-- Owner assignment: each group is assigned to the grouping_key (org) that has
-- the most linked migrating locations. Tie-break: grouping_key ASC.
--
-- Join path (4-hop):
--   ChargerUserGroups → PriceToUsersAndChargers → ChargerToChargerGroup
--   → Chargers → migrating_locations
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Target"."UserGroups";

CREATE OR REPLACE VIEW "Target"."UserGroups" AS

-- CTE 1: All locations where migrate=TRUE, with grouping_key from MasterPartnerResolution
WITH migrating_locations AS (
    SELECT
        loc.location_nr,
        mpr.grouping_key
    FROM "Source"."Locations" loc
    JOIN "Mapping"."location_mapping" lm
        ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    JOIN "Mapping"."MasterPartnerResolution" mpr
        ON mpr.mapping_key = lm.mapping_key
    WHERE lm.migrate = TRUE
),

-- CTE 2: Distinct (charger_user_group_guid, grouping_key) tuples for eligible groups
eligible_groups AS (
    SELECT DISTINCT
        cug.charger_user_group_guid,
        ml.grouping_key,
        ml.location_nr
    FROM "Source"."ChargerUserGroups" cug
    JOIN "Source"."PriceToUsersAndChargers" ptuc
        ON ptuc.charger_user_group_guid != ''
       AND ptuc.charger_user_group_guid::uuid = cug.charger_user_group_guid
    JOIN "Source"."ChargerToChargerGroup" ctcg
        ON ctcg.charger_group_guid = ptuc.charger_group_guid
    JOIN "Source"."Chargers" c
        ON c."Id" = ctcg.charger_guid
    JOIN migrating_locations ml
        ON ml.location_nr = c.location_nr
    WHERE cug.has_members = TRUE
),

-- CTE 3: Per group, pick the grouping_key with most linked locations; tie-break ASC
group_best_owner AS (
    SELECT DISTINCT ON (charger_user_group_guid)
        charger_user_group_guid,
        grouping_key,
        COUNT(DISTINCT location_nr) AS loc_count
    FROM eligible_groups
    GROUP BY charger_user_group_guid, grouping_key
    ORDER BY charger_user_group_guid, COUNT(DISTINCT location_nr) DESC, grouping_key ASC
)

-- Final SELECT: join back to source + mapping tables for output columns
SELECT
    'user_group_mapping'::TEXT                          AS mapping_table,
    'Sleet|UserGroup|' || gbo.charger_user_group_guid  AS mapping_key,

    mpr.master_target_partner_id::INTEGER               AS "TargetPartnerID",
    ugm.target_user_group_id::INTEGER                   AS "TargetUserGroupID",

    cug.charger_user_group_name::TEXT                    AS "name",
    mpr.master_target_partner_id::INTEGER               AS "partnerId",
    (cug.charger_user_group_name || ' (' || cug.charger_user_group_owner || ')')::TEXT AS "description"

FROM group_best_owner gbo

-- Get group name + owner from source
JOIN "Source"."ChargerUserGroups" cug
    ON cug.charger_user_group_guid = gbo.charger_user_group_guid

-- Get master_target_partner_id for the best-owner grouping_key
-- (pick any location in that grouping_key — they all share the same master)
JOIN LATERAL (
    SELECT DISTINCT ON (1) m.master_target_partner_id
    FROM "Mapping"."MasterPartnerResolution" m
    WHERE m.grouping_key = gbo.grouping_key
    LIMIT 1
) mpr ON TRUE

-- Get target_user_group_id from mapping (NULL on first run)
LEFT JOIN "Mapping"."user_group_mapping" ugm
    ON ugm.mapping_key = 'Sleet|UserGroup|' || gbo.charger_user_group_guid;
