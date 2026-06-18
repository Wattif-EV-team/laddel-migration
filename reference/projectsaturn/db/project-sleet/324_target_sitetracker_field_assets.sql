-- ============================================================================
-- TARGET VIEW: SiteTracker Field Assets
-- ============================================================================
-- UNION of three sub-views: Chargers, Routers, SIMs.
-- Each sub-view produces rows eligible for SiteTracker Field Asset creation.
-- Columns match sitetracker__Field_Asset__c field API names.
--
-- Sub-views:
--   Target._SiteTrackerFieldAssets_Chargers — existing charger logic
--   Target._SiteTrackerFieldAssets_Routers  — routers from ExcelRouters + Teltonika RMS
--   Target._SiteTrackerFieldAssets_SIMs     — SIM cards (children of routers)
--
-- All sub-views share the same column set. NULL for irrelevant columns.
-- The script uses payload column names 1:1 for the SF API call.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- ============================================================================
-- SUB-VIEW 1: CHARGERS
-- ============================================================================
DROP VIEW IF EXISTS "Target"."_SiteTrackerFieldAssets_Chargers" CASCADE;
CREATE VIEW "Target"."_SiteTrackerFieldAssets_Chargers" AS

WITH chargers_in_scope AS (
    -- CTE 1: All chargers at locations marked for SiteTracker migration
    SELECT
        c."Id" AS charger_guid,
        c.charger_id,
        c.charger_name,
        c.charger_product,
        c.parking_spot,
        c.controller_serial_number_2,
        c.charger_commissioned_on,
        c.description,
        c.warranty_end_date,
        c.status AS charger_status,
        c.location_nr,
        loc.city,
        loc.address,
        lm.mapping_key AS location_mapping_key,
        lm.project_code,
        lm.partner_model,
        lm.migration_date,
        sm.target_sf_site_id
    FROM "Source"."Chargers" c
    JOIN "Source"."Locations" loc ON loc.location_nr = c.location_nr
    JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    JOIN "Mapping"."sitetracker_site_mapping" sm ON sm.mapping_key = lm.mapping_key
    WHERE lm.load_to_sitetracker = TRUE
      AND sm.target_sf_site_id IS NOT NULL
),

with_sequence AS (
    -- CTE 2: Add sequence number per project_code for ChargePoint naming
    -- Must match the same ordering as Target.ChargePoints for name consistency
    SELECT
        cis.*,
        ROW_NUMBER() OVER (
            PARTITION BY cis.project_code
            ORDER BY cis.location_nr, cis.charger_name, cis.charger_id
        ) AS seq_num
    FROM chargers_in_scope cis
),

with_codes AS (
    -- CTE 3: Derive city_code and address_code (same logic as Target.ChargePoints)
    SELECT
        ws.*,
        LEFT(
            regexp_replace(
                translate(UPPER(COALESCE(ws.city, '')), 'ÉÈÍÓÚÛÅÄÆÖØ', 'EEIOUUAAAOO'),
                '[^A-Z]', '', 'g'
            ) || 'XXX',
            3
        ) AS city_code,
        LEFT(
            regexp_replace(
                translate(UPPER(COALESCE(ws.address, '')), 'ÉÈÍÓÚÛÅÄÆÖØ', 'EEIOUUAAAOO'),
                '[^A-Z]', '', 'g'
            ) || 'XXX',
            3
        ) AS address_code
    FROM with_sequence ws
),

with_product AS (
    -- CTE 4: Join ChargerProductLookup for SiteTracker Item Name
    SELECT
        wc.*,
        cpl.sitetracker_item_name
    FROM with_codes wc
    LEFT JOIN "Mapping"."ChargerProductLookup" cpl
        ON cpl.charger_product_lower = LOWER(wc.charger_product)
)

-- ==========================================================================
-- Final SELECT — Chargers
-- ==========================================================================
SELECT
    -- ── SOURCE ──────────────────────────────────────────────────────────────
    'sitetracker_field_asset_mapping'::TEXT      AS mapping_table,
    'Sleet|FieldAsset|' || wp.charger_guid::TEXT AS mapping_key,
    wp.project_code,
    wp.project_code || '/' || wp.charger_name    AS source_label,

    -- ── TARGET ID(S) ────────────────────────────────────────────────────────
    fam.target_sf_field_asset_id                 AS "TargetSfFieldAssetId",

    -- ── PAYLOAD (SF API field names — used 1:1 by script) ───────────────────
    ('NOR' || wp.city_code || wp.address_code || to_char(wp.seq_num, 'FM000'))::TEXT
                                                 AS "Name",
    wp.sitetracker_item_name                     AS "sitetracker__Item__c",
    wp.target_sf_site_id                         AS "sitetracker__Site__c",
    CASE
        WHEN wp.charger_status = 'Active' THEN 'Installed'
        ELSE 'Decommissioned'
    END                                          AS "sitetracker__Status__c",
    wp.controller_serial_number_2                AS "sitetracker__Serial__c",
    wp.migration_date                            AS "sitetracker__Install_Date__c",
    wp.charger_commissioned_on::DATE             AS "sitetracker__Original_Install_Date__c",
    CASE
        WHEN UPPER(wp.partner_model) LIKE 'WO%' THEN 'W-WattifEV Owned'
        WHEN UPPER(wp.partner_model) LIKE 'CO%' THEN 'CU-Customer Owned'
    END                                          AS "Ownership__c",
    COALESCE(NULLIF(wp.parking_spot, ''), '')    AS "Location__c",
    LEFT(
        REPLACE(
            REPLACE(
                CONCAT_WS(E'\n',
                    NULLIF(wp.description, ''),
                    CASE
                        WHEN wp.charger_product ILIKE '%NON WARRANTY%'
                            THEN '[Mer B2B: Non Warranty]'
                        WHEN wp.warranty_end_date IS NOT NULL
                            THEN '[Mer B2B: Warranty until ' || to_char(wp.warranty_end_date, 'YYYY-MM-DD') || ']'
                    END
                ),
                E'\r\n', E'\n'
            ),
            E'\n', E'\r\n'
        ), 255
    )                                            AS "sitetracker__Notes__c",

    -- ── Router/SIM-only columns (NULL for chargers) ─────────────────────────
    NULL::TEXT                                    AS "MAC__c",
    NULL::TEXT                                    AS "IMEI__c",
    NULL::TEXT                                    AS "Password__c",
    NULL::TEXT                                    AS "Factory_Default_Password__c",
    NULL::TEXT                                    AS "iccID__c",
    NULL::TEXT                                    AS "IP_Address__c",
    NULL::TEXT                                    AS "URL_Management__c",
    NULL::TEXT                                    AS "sitetracker__Parent__c"

FROM with_product wp
LEFT JOIN "Mapping"."sitetracker_field_asset_mapping" fam
    ON fam.mapping_key = 'Sleet|FieldAsset|' || wp.charger_guid::TEXT;


-- ============================================================================
-- SUB-VIEW 2: ROUTERS
-- ============================================================================
DROP VIEW IF EXISTS "Target"."_SiteTrackerFieldAssets_Routers" CASCADE;
CREATE VIEW "Target"."_SiteTrackerFieldAssets_Routers" AS
SELECT
    -- ── SOURCE ──────────────────────────────────────────────────────────────
    'sitetracker_field_asset_mapping'::TEXT       AS mapping_table,
    'Sleet|FieldAsset|Router|' || er."Id"        AS mapping_key,
    lm.project_code,
    lm.project_code || '/' || er."Id"            AS source_label,

    -- ── TARGET ID(S) ────────────────────────────────────────────────────────
    fam.target_sf_field_asset_id                  AS "TargetSfFieldAssetId",

    -- ── PAYLOAD ─────────────────────────────────────────────────────────────
    er."Id"                                       AS "Name",
    'Teltonika RUT956 Industriell LTE-ruter'::TEXT AS "sitetracker__Item__c",
    sm.target_sf_site_id                          AS "sitetracker__Site__c",
    'Installed'::TEXT                             AS "sitetracker__Status__c",
    td.serial                                     AS "sitetracker__Serial__c",
    lm.migration_date                             AS "sitetracker__Install_Date__c",
    NULL::DATE                                    AS "sitetracker__Original_Install_Date__c",
    'W-WattifEV Owned'::TEXT                     AS "Ownership__c",
    NULL::TEXT                                    AS "Location__c",
    NULL::TEXT                                    AS "sitetracker__Notes__c",

    -- ── Router-specific columns ─────────────────────────────────────────────
    td.mac                                        AS "MAC__c",
    td.imei                                       AS "IMEI__c",
    er.original_password                          AS "Password__c",
    COALESCE(tpl.device_password, eqr.device_password, er.original_password)
                                                  AS "Factory_Default_Password__c",
    NULL::TEXT                                    AS "iccID__c",
    NULL::TEXT                                    AS "IP_Address__c",
    'https://rms.teltonika-networks.com/devices/' || td."Id"::TEXT
                                                  AS "URL_Management__c",
    NULL::TEXT                                    AS "sitetracker__Parent__c"

FROM "Source"."ExcelRouters" er
JOIN "Source"."Locations" loc ON loc."Id" = er.location_guid
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
JOIN "Mapping"."sitetracker_site_mapping" sm ON sm.mapping_key = lm.mapping_key
LEFT JOIN "Source"."TeltonikaDevices" td ON td.serial = er.serial
LEFT JOIN "Source"."TeltonikaPackingList" tpl ON tpl.serial = er.serial
LEFT JOIN "Source"."EquipmentRegistration" eqr ON eqr.serial = er.serial
LEFT JOIN "Mapping"."sitetracker_field_asset_mapping" fam
    ON fam.mapping_key = 'Sleet|FieldAsset|Router|' || er."Id"
WHERE lm.load_to_sitetracker = TRUE
  AND sm.target_sf_site_id IS NOT NULL;


-- ============================================================================
-- SUB-VIEW 3: SIM CARDS
-- ============================================================================
DROP VIEW IF EXISTS "Target"."_SiteTrackerFieldAssets_SIMs" CASCADE;
CREATE VIEW "Target"."_SiteTrackerFieldAssets_SIMs" AS
SELECT
    -- ── SOURCE ──────────────────────────────────────────────────────────────
    'sitetracker_field_asset_mapping'::TEXT       AS mapping_table,
    'Sleet|FieldAsset|SIM|' || er."Id"           AS mapping_key,
    lm.project_code,
    lm.project_code || '/' || er."Id" || ' (SIM)' AS source_label,

    -- ── TARGET ID(S) ────────────────────────────────────────────────────────
    sim_fam.target_sf_field_asset_id              AS "TargetSfFieldAssetId",

    -- ── PAYLOAD ─────────────────────────────────────────────────────────────
    er."Id"                                       AS "Name",
    'Simcard-Monthly-Payment'::TEXT               AS "sitetracker__Item__c",
    sm.target_sf_site_id                          AS "sitetracker__Site__c",
    'Installed'::TEXT                             AS "sitetracker__Status__c",
    NULL::TEXT                                    AS "sitetracker__Serial__c",
    lm.migration_date                             AS "sitetracker__Install_Date__c",
    NULL::DATE                                    AS "sitetracker__Original_Install_Date__c",
    'W-WattifEV Owned'::TEXT                     AS "Ownership__c",
    NULL::TEXT                                    AS "Location__c",
    NULL::TEXT                                    AS "sitetracker__Notes__c",

    -- ── SIM-specific columns ────────────────────────────────────────────────
    NULL::TEXT                                    AS "MAC__c",
    NULL::TEXT                                    AS "IMEI__c",
    NULL::TEXT                                    AS "Password__c",
    NULL::TEXT                                    AS "Factory_Default_Password__c",
    LEFT(td.iccid, 19)                           AS "iccID__c",
    td.wan_ip                                     AS "IP_Address__c",
    NULL::TEXT                                    AS "URL_Management__c",
    router_fam.target_sf_field_asset_id           AS "sitetracker__Parent__c"

FROM "Source"."ExcelRouters" er
JOIN "Source"."Locations" loc ON loc."Id" = er.location_guid
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
JOIN "Mapping"."sitetracker_site_mapping" sm ON sm.mapping_key = lm.mapping_key
JOIN "Source"."TeltonikaDevices" td ON td.serial = er.serial
LEFT JOIN "Mapping"."sitetracker_field_asset_mapping" router_fam
    ON router_fam.mapping_key = 'Sleet|FieldAsset|Router|' || er."Id"
LEFT JOIN "Mapping"."sitetracker_field_asset_mapping" sim_fam
    ON sim_fam.mapping_key = 'Sleet|FieldAsset|SIM|' || er."Id"
WHERE lm.load_to_sitetracker = TRUE
  AND sm.target_sf_site_id IS NOT NULL
  AND td.iccid IS NOT NULL
  AND td.iccid ~ '^\d+$';


-- ============================================================================
-- FINAL VIEW: UNION of all sub-views
-- ============================================================================
DROP VIEW IF EXISTS "Target"."SiteTrackerFieldAssets" CASCADE;
CREATE VIEW "Target"."SiteTrackerFieldAssets" AS
SELECT * FROM "Target"."_SiteTrackerFieldAssets_Chargers"
UNION ALL
SELECT * FROM "Target"."_SiteTrackerFieldAssets_Routers"
UNION ALL
SELECT * FROM "Target"."_SiteTrackerFieldAssets_SIMs";
