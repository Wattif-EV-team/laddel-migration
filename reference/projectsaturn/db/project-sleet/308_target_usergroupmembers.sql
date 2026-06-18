-- ============================================================================
-- TARGET VIEW: UserGroupMembers (Phase C, Step 5)
-- ============================================================================
-- Grain: DISTINCT (TargetUserGroupID, TargetUserID) — account level.
--
-- Join path (no hop through 107 — ChargerUsers has direct FK account_owner_guid):
--   ChargerUserGroupMemberships → ChargerUsers → user_group_mapping + user_mapping
--
-- Self-gating: the consuming script filters
--   WHERE TargetUserGroupID IS NOT NULL AND TargetUserID IS NOT NULL
-- so NULL rows from unmapped users/groups are harmless.
--
-- Expected ~1,537 distinct rows (after user + group creation).
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Target"."UserGroupMembers";

CREATE OR REPLACE VIEW "Target"."UserGroupMembers" AS
SELECT DISTINCT
    ugm.target_user_group_id::INTEGER AS "TargetUserGroupID",
    um.target_user_id::INTEGER        AS "TargetUserID"

FROM "Source"."ChargerUserGroupMemberships" m

-- Resolve charger_user_guid → account_owner_guid (direct FK, no 107 hop)
JOIN "Source"."ChargerUsers" cu
    ON cu.charger_user_guid = m.charger_user_guid

-- Resolve user group mapping
LEFT JOIN "Mapping"."user_group_mapping" ugm
    ON ugm.mapping_key = 'Sleet|UserGroup|' || m.charger_user_group_guid

-- Resolve user mapping (account level)
LEFT JOIN "Mapping"."user_mapping" um
    ON um.mapping_key = 'Sleet|Account|' || cu.account_owner_guid;
