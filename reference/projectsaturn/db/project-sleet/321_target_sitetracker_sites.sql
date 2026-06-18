-- ============================================================================
-- TARGET VIEW: SiteTracker Sites
-- ============================================================================
-- One row per location eligible for SiteTracker (load_to_sitetracker = TRUE).
-- Excludes merged duplicates (only primary/non-merged locations).
-- Produces columns matching sitetracker__Site__c field API names.
--
-- Includes mapping_table + mapping_key + TargetSfSiteId for the generic
-- create-or-update pattern (NULL TargetSfSiteId = needs creation).
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Target"."SiteTrackerSites" CASCADE;
CREATE VIEW "Target"."SiteTrackerSites" AS

WITH base_locations AS (
    -- CTE 1: Base location data from mapping + source
    SELECT
        lm.mapping_key,
        lm.project_code,
        lm.status AS planning_status,
        lm.partner_model,
        lm.wattif_installer,
        lm.migration_date,
        COALESCE(lm.location_name, loc.location_name) AS location_name,
        loc."Id" AS source_location_guid,
        loc.location_nr,
        loc.address,
        loc.city,
        loc.postal_code::TEXT AS postal_code,
        loc.latitude::NUMERIC AS latitude,
        loc.longitude::NUMERIC AS longitude,
        loc.location_commissioned
    FROM "Mapping"."location_mapping" lm
    JOIN "Source"."Locations" loc ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    WHERE lm.load_to_sitetracker = TRUE
      AND (lm.merge_with_mapping_key IS NULL
           OR lm.mapping_key = lm.merge_with_mapping_key)
),

with_coordinates AS (
    -- CTE 2: Coordinate validation with swap logic for Norway bounds
    -- Norway bounds: latitude 57–71°, longitude 4–31°
    SELECT
        bl.*,
        CASE
            WHEN bl.latitude BETWEEN 57 AND 71 AND bl.longitude BETWEEN 4 AND 31
                THEN bl.latitude
            WHEN bl.longitude BETWEEN 57 AND 71 AND bl.latitude BETWEEN 4 AND 31
                THEN bl.longitude  -- swapped
            WHEN geo.latitude IS NOT NULL THEN geo.latitude
            ELSE NULL
        END AS validated_latitude,
        CASE
            WHEN bl.latitude BETWEEN 57 AND 71 AND bl.longitude BETWEEN 4 AND 31
                THEN bl.longitude
            WHEN bl.longitude BETWEEN 57 AND 71 AND bl.latitude BETWEEN 4 AND 31
                THEN bl.latitude  -- swapped
            WHEN geo.longitude IS NOT NULL THEN geo.longitude
            ELSE NULL
        END AS validated_longitude
    FROM base_locations bl
    LEFT JOIN "Source"."GeocodedLocations" geo ON geo.location_guid = bl.source_location_guid::TEXT
),

with_connectors AS (
    -- CTE 3: Aggregate connector types and charging levels per location
    -- Excludes Schuko connectors entirely
    SELECT
        wc.mapping_key,
        -- EV_Connector_Type__c: semicolon-separated multi-picklist
        STRING_AGG(DISTINCT
            CASE
                WHEN con.connector_type = 'Type2' THEN 'Type 2'
                WHEN con.connector_type = 'CCS2' THEN 'CCS2'
            END,
            ';' ORDER BY
            CASE
                WHEN con.connector_type = 'Type2' THEN 'Type 2'
                WHEN con.connector_type = 'CCS2' THEN 'CCS2'
            END
        ) AS ev_connector_type,
        -- EV_Charging_Level__c: semicolon-separated multi-picklist
        STRING_AGG(DISTINCT
            CASE
                WHEN con.connector_type IN ('Type2') THEN 'Level 2 AC 22kWh'
                WHEN con.connector_type = 'CCS2' AND esn.target_evse_max_power > 60 THEN 'DC_above60'
                WHEN con.connector_type = 'CCS2' AND esn.target_evse_max_power <= 60 THEN 'DC type (below 60 KWh)'
            END,
            ';' ORDER BY
            CASE
                WHEN con.connector_type IN ('Type2') THEN 'Level 2 AC 22kWh'
                WHEN con.connector_type = 'CCS2' AND esn.target_evse_max_power > 60 THEN 'DC_above60'
                WHEN con.connector_type = 'CCS2' AND esn.target_evse_max_power <= 60 THEN 'DC type (below 60 KWh)'
            END
        ) AS ev_charging_level
    FROM with_coordinates wc
    JOIN "Source"."Chargers" ch ON ch.location_nr = wc.location_nr
    JOIN "Source"."Connectors" con ON con.id = ch.charger_id
        AND con.connector_type IN ('Type2', 'CCS2')  -- Exclude Schuko
    LEFT JOIN "Source"."ElectricalSettingsNormalized" esn
        ON esn.charger_id = ch.charger_id AND esn.connector_level = con.connector_level
    GROUP BY wc.mapping_key
),

with_load_management AS (
    -- CTE 4: Determine load management based on controller existence
    SELECT
        wc.mapping_key,
        CASE
            WHEN EXISTS (
                SELECT 1 FROM "Source"."Controllers" ctrl
                WHERE ctrl.location_nr = wc.location_nr
            ) THEN 'OCPP-WATTIF-METER'
            ELSE 'NONE'
        END AS load_management
    FROM with_coordinates wc
)

-- Final SELECT: produce SiteTracker field names + mapping columns
SELECT
    -- Mapping columns (generic pattern)
    'sitetracker_site_mapping'                  AS mapping_table,
    wc.mapping_key,
    wc.project_code,
    sm.target_sf_site_id                        AS "TargetSfSiteId",

    -- SiteTracker fields
    wc.project_code                             AS "Site_ID__c",
    wc.location_name                            AS "Name",
    CASE UPPER(btrim(wc.planning_status))
        WHEN 'DONE'  THEN 'Operational'
        WHEN 'READY' THEN 'Under Migration'
    END                                         AS "sitetracker__Site_Status__c",
    'BUSINESS'                                  AS "sitetracker__Site_Type__c",
    wc.address                                  AS "sitetracker__Street_Address__c",
    wc.city                                     AS "sitetracker__City__c",
    LPAD(wc.postal_code, 4, '0')                 AS "sitetracker__Zip_Code__c",
    'Norway(NOR)'                               AS "Country__c",
    wc.validated_latitude                       AS "sitetracker__Location__Latitude__s",
    wc.validated_longitude                      AS "sitetracker__Location__Longitude__s",
    CASE
        WHEN UPPER(wc.partner_model) LIKE 'WO%' THEN 'W-WattifEV'
        WHEN UPPER(wc.partner_model) LIKE 'CO%' THEN 'C-ClientOwned'
    END                                         AS "Owner_Type__c",
    conn.ev_connector_type                      AS "EV_Connector_Type__c",
    conn.ev_charging_level                      AS "EV_Charging_Level__c",
    lm.load_management                          AS "Load_Management__c",
    CASE UPPER(btrim(wc.planning_status))
        WHEN 'DONE'  THEN COALESCE(wc.migration_date, CURRENT_DATE)
        WHEN 'READY' THEN wc.migration_date
    END                                         AS "Open_Date__c",
    wc.location_commissioned::DATE              AS "Installed_Date__c"

FROM with_coordinates wc
LEFT JOIN with_connectors conn ON conn.mapping_key = wc.mapping_key
LEFT JOIN with_load_management lm ON lm.mapping_key = wc.mapping_key
LEFT JOIN "Mapping"."sitetracker_site_mapping" sm ON sm.mapping_key = wc.mapping_key;
