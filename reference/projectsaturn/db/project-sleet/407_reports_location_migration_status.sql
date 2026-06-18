-- ============================================================================
-- REPORT VIEW: LocationMigrationStatus
-- ============================================================================
-- One row per Source Location showing migration progress:
--   - Location identification (project code, location nr, name)
--   - Migration status from location_mapping
--   - Charger counts (total vs loaded into Ampeco)
--   - User counts (total, missing email, loaded into Ampeco)
--   - RFID counts (total, missing email on account, loaded into Ampeco)
--
-- Join paths for Users & RFIDs:
--   Location -> Charger (location_nr)
--     -> ChargerToChargerGroup (charger_guid)
--       -> PriceToUsersAndChargers (charger_group_guid)
--         -> ChargerUserGroupMemberships (charger_user_group_guid)
--           -> ChargerUsers (charger_user_guid)
--             -> ChargerUserAccounts (account_owner_guid)  [Users]
--             -> RFIDs (rfid_guid)                         [RFIDs]
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Reports"."LocationMigrationStatus";

CREATE OR REPLACE VIEW "Reports"."LocationMigrationStatus" AS
WITH
-- Valid RFIDs: same filtering as Target.IdTags (hex validation + byte-length check)
valid_rfids AS (
    SELECT r.rfid_guid
    FROM "Source"."RFIDs" r
    WHERE r.hex IS NOT NULL
      AND BTRIM(r.hex) != ''
      AND BTRIM(r.hex) ~* '^[0-9a-f]+$'
      AND BTRIM(r.hex) !~ '^0+$'
      AND LENGTH(BTRIM(r.hex)) IN (8, 14, 16, 20)
),
-- RFIDs linked to multiple different accounts (excluded from Target.IdTags)
rfid_multi_account AS (
    SELECT cu.rfid_guid
    FROM "Source"."ChargerUsers" cu
    WHERE cu.rfid_guid IS NOT NULL
    GROUP BY cu.rfid_guid
    HAVING COUNT(DISTINCT cu.account_owner_guid) > 1
),
-- Resolve distinct accounts reachable from each location via the pricing bridge
location_accounts AS (
    SELECT DISTINCT
        loc."Id" AS location_guid,
        a.account_owner_guid,
        a.email
    FROM "Source"."Locations" loc
    JOIN "Source"."Chargers" c ON c.location_nr = loc.location_nr
    JOIN "Source"."ChargerToChargerGroup" ctcg ON ctcg.charger_guid = c."Id"
    JOIN "Source"."PriceToUsersAndChargers" ptuc ON ptuc.charger_group_guid = ctcg.charger_group_guid
    JOIN "Source"."ChargerUserGroupMemberships" cugm
        ON cugm.charger_user_group_guid = NULLIF(ptuc.charger_user_group_guid, '')::uuid
    JOIN "Source"."ChargerUsers" cu ON cu.charger_user_guid = cugm.charger_user_guid
    JOIN "Source"."ChargerUserAccounts" a ON a.account_owner_guid = cu.account_owner_guid
),
-- Resolve distinct RFIDs reachable from each location via the pricing bridge
-- Applies same RFID validity filters as Target.IdTags (hex format, byte-length,
-- single-account ownership)
location_rfids AS (
    SELECT DISTINCT
        loc."Id" AS location_guid,
        cu.rfid_guid,
        a.email AS account_email
    FROM "Source"."Locations" loc
    JOIN "Source"."Chargers" c ON c.location_nr = loc.location_nr
    JOIN "Source"."ChargerToChargerGroup" ctcg ON ctcg.charger_guid = c."Id"
    JOIN "Source"."PriceToUsersAndChargers" ptuc ON ptuc.charger_group_guid = ctcg.charger_group_guid
    JOIN "Source"."ChargerUserGroupMemberships" cugm
        ON cugm.charger_user_group_guid = NULLIF(ptuc.charger_user_group_guid, '')::uuid
    JOIN "Source"."ChargerUsers" cu ON cu.charger_user_guid = cugm.charger_user_guid
    JOIN "Source"."ChargerUserAccounts" a ON a.account_owner_guid = cu.account_owner_guid
    -- Only include RFIDs that pass Target.IdTags validation
    JOIN valid_rfids vr ON vr.rfid_guid = cu.rfid_guid
    WHERE cu.rfid_guid NOT IN (SELECT rfid_guid FROM rfid_multi_account)
),
-- Aggregate user counts per location
user_stats AS (
    SELECT
        la.location_guid,
        COUNT(DISTINCT la.account_owner_guid) AS users_total,
        COUNT(DISTINCT CASE
            WHEN la.email IS NULL OR la.email = '' THEN la.account_owner_guid
        END) AS users_missing_email,
        COUNT(DISTINCT CASE
            WHEN um.target_user_id IS NOT NULL THEN la.account_owner_guid
        END) AS users_loaded
    FROM location_accounts la
    LEFT JOIN "Mapping"."user_mapping" um
        ON um.mapping_key = 'Sleet|Account|' || la.account_owner_guid::text
    GROUP BY la.location_guid
),
-- Aggregate RFID counts per location
rfid_stats AS (
    SELECT
        lr.location_guid,
        COUNT(DISTINCT lr.rfid_guid) AS rfid_total,
        COUNT(DISTINCT CASE
            WHEN lr.account_email IS NULL OR lr.account_email = '' THEN lr.rfid_guid
        END) AS rfid_missing_email,
        COUNT(DISTINCT CASE
            WHEN rm.target_idtag_id IS NOT NULL THEN lr.rfid_guid
        END) AS rfid_loaded
    FROM location_rfids lr
    LEFT JOIN "Mapping"."rfid_mapping" rm
        ON rm.mapping_key = 'Sleet|RFID|' || lr.rfid_guid::text
    GROUP BY lr.location_guid
),
-- Aggregate charger counts per location
charger_stats AS (
    SELECT
        loc."Id" AS location_guid,
        COUNT(DISTINCT c."Id") AS chargers_total,
        COUNT(DISTINCT CASE
            WHEN cm.target_charge_point_id IS NOT NULL THEN c."Id"
        END) AS chargers_loaded
    FROM "Source"."Locations" loc
    JOIN "Source"."Chargers" c ON c.location_nr = loc.location_nr
    LEFT JOIN "Mapping"."charger_mapping" cm
        ON cm.mapping_key = 'Sleet|Charger|' || c."Id"::text
    GROUP BY loc."Id"
)
SELECT
    -- Location identification
    lm.project_code                                              AS "ProjectCode",
    loc.location_nr                                              AS "LocationNr",
    COALESCE(NULLIF(lm.location_name, ''), loc.location_name)   AS "LocationName",

    -- Migration status
    lm.status                                                    AS "MigrationStatus",
    lm.migrate                                                   AS "Migrate",
    lm.exclude                                                   AS "Exclude",

    -- Chargers
    COALESCE(cs.chargers_total, 0)                               AS "Chargers",
    COALESCE(cs.chargers_loaded, 0)                              AS "ChargersLoaded",

    -- Users
    COALESCE(us.users_total, 0)                                  AS "Users",
    COALESCE(us.users_missing_email, 0)                          AS "UsersMissingEmail",
    COALESCE(us.users_loaded, 0)                                 AS "UsersLoaded",

    -- RFIDs
    COALESCE(rs.rfid_total, 0)                                   AS "RFID",
    COALESCE(rs.rfid_missing_email, 0)                           AS "RFIDMissingEmail",
    COALESCE(rs.rfid_loaded, 0)                                  AS "RFIDLoaded"

FROM "Source"."Locations" loc
JOIN "Mapping"."location_mapping" lm
    ON lm.mapping_key = 'Sleet|Location|' || loc."Id"::text
LEFT JOIN charger_stats cs ON cs.location_guid = loc."Id"
LEFT JOIN user_stats us    ON us.location_guid = loc."Id"
LEFT JOIN rfid_stats rs    ON rs.location_guid = loc."Id"

WHERE lm.exclude = FALSE

ORDER BY lm.project_code, loc.location_nr;
