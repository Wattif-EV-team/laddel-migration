-- ============================================================================
-- TARGET VIEW: PartnerInvites (Step 314)
-- ============================================================================
-- Distinct (TargetUserID, TargetPartnerID) pairs for partner invite creation.
--
-- Two invite sources combined via UNION ALL + flag aggregation:
--
-- 1. Station-based invites (existing):
--    ChargerUserGroupMemberships → ChargerUsers → user_mapping  (TargetUserID)
--    ChargerUserGroupMemberships → eligible groups via 4-hop    (grouping_key)
--    grouping_key → MasterPartnerResolution                     (master partner)
--    → options_allowAccessToPrivateChargePoints = TRUE
--
-- 2. Corporate billing invites (new):
--    BillingAccounts → billing_partner_mapping                  (billing partner)
--    BillingAccounts → user_mapping                             (TargetUserID)
--    → options_allowCorporateAccountBilling = TRUE
--
-- A user that appears via BOTH paths gets ONE row with both flags TRUE.
--
-- No mapping columns — partner invites are not individually tracked entities.
-- The consuming script (CreatePartnerInvites.py) filters:
--   WHERE TargetPartnerID IS NOT NULL AND TargetUserID IS NOT NULL
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Target"."PartnerInvites";

CREATE OR REPLACE VIEW "Target"."PartnerInvites" AS

-- CTE 1: All locations where migrate=TRUE, with grouping_key
WITH migrating_locations AS (
    SELECT
        loc.location_nr,
        mpr.grouping_key,
        mpr.master_target_partner_id
    FROM "Source"."Locations" loc
    JOIN "Mapping"."location_mapping" lm
        ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    JOIN "Mapping"."MasterPartnerResolution" mpr
        ON mpr.mapping_key = lm.mapping_key
    WHERE lm.migrate = TRUE
),

-- CTE 2: Eligible user groups linked to migrating locations (4-hop join)
--   Produces (charger_user_group_guid, grouping_key, master_target_partner_id)
eligible_group_locations AS (
    SELECT DISTINCT
        cug.charger_user_group_guid,
        ml.grouping_key,
        ml.master_target_partner_id
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

-- CTE 3: Master partner per group — all locations in a group share the same
-- org's master partner; pick the master_target_partner_id from the best-owner
-- grouping_key (most linked locations, tie-break ASC) — same logic as 307
group_master_partner AS (
    SELECT DISTINCT ON (charger_user_group_guid)
        charger_user_group_guid,
        master_target_partner_id
    FROM (
        SELECT
            charger_user_group_guid,
            grouping_key,
            master_target_partner_id,
            COUNT(*) AS loc_count
        FROM eligible_group_locations
        GROUP BY charger_user_group_guid, grouping_key, master_target_partner_id
        ORDER BY charger_user_group_guid, COUNT(*) DESC, grouping_key ASC
    ) sub
),

-- CTE 4: Station-based (user, master_partner) pairs from group memberships
station_invites AS (
    SELECT DISTINCT
        um.target_user_id   AS "TargetUserID",
        gmp.master_target_partner_id AS "TargetPartnerID"
    FROM "Source"."ChargerUserGroupMemberships" m
    JOIN "Source"."ChargerUsers" cu
        ON cu.charger_user_guid = m.charger_user_guid
    JOIN group_master_partner gmp
        ON gmp.charger_user_group_guid = m.charger_user_group_guid
    JOIN "Mapping"."user_mapping" um
        ON um.mapping_key = 'Sleet|Account|' || cu.account_owner_guid
),

-- CTE 5: Corporate billing (user, billing_partner) pairs
corporate_invites AS (
    SELECT DISTINCT
        um.target_user_id     AS "TargetUserID",
        bpm.target_partner_id AS "TargetPartnerID"
    FROM "Source"."BillingAccounts" ba
    JOIN "Mapping"."user_mapping" um
        ON um.mapping_key = 'Sleet|Account|' || ba.account_owner_guid
    JOIN "Mapping"."billing_partner_mapping" bpm
        ON bpm.mapping_key = 'Sleet|BillingPartner|' || ba.account_owner_guid
),

-- CTE 6: Unified pairs with source flags
unified AS (
    SELECT "TargetUserID", "TargetPartnerID", 1 AS is_station, 0 AS is_corp
    FROM station_invites
    UNION ALL
    SELECT "TargetUserID", "TargetPartnerID", 0 AS is_station, 1 AS is_corp
    FROM corporate_invites
),

-- CTE 7: Aggregate flags per (user, partner) pair
pairs AS (
    SELECT
        "TargetUserID",
        "TargetPartnerID",
        (SUM(is_station) > 0)::BOOLEAN AS options_allowAccessToPrivateChargePoints,
        (SUM(is_corp) > 0)::BOOLEAN    AS options_allowCorporateAccountBilling
    FROM unified
    GROUP BY "TargetUserID", "TargetPartnerID"
)

-- Final SELECT: one row per (user, partner) with aggregated invite options
SELECT
    p."TargetUserID"::INTEGER,
    p."TargetPartnerID"::INTEGER,
    p."TargetPartnerID"::INTEGER                  AS "partnerId",
    p.options_allowCorporateAccountBilling         AS "options_allowCorporateAccountBilling",
    FALSE::BOOLEAN                                AS "options_limitCorporateAccountBillingToPartnerChargePoints",
    p.options_allowAccessToPrivateChargePoints     AS "options_allowAccessToPrivateChargePoints",
    TRUE::BOOLEAN                                 AS "sendViaEmail",
    'en'::TEXT                                    AS "language"
FROM pairs p;
