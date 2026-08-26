-- ============================================================================
-- View: target.sitetracker_sites
-- Depends on: target.sitetracker_site_mapping (004), read-only `laddel` source.
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
--
-- Grain: one Site per `laddel.facility`. Batch gate:
-- `organization.migration_status IN ('READY', 'MIGRATE')` — same gate as
-- `target`.`sitetracker_accounts` (314) and `target`.`sitetracker_site_relations`
-- (316) — AND the facility must be migration-eligible per
-- `target`.`facility_migration_eligibility` (201): excludes facilities with no
-- chargers, all chargers inactive, no sessions ever, or no sessions in the
-- last 6 months.
--
-- Maps `laddel` onto the SiteTracker (Salesforce) "create Site" payload
-- (POST /services/data/vXX.0/sobjects/sitetracker__Site__c/). See
-- docs/fieldmapping/sitetracker_site.md.
--
-- Layout: SOURCE -> TARGET ID -> PAYLOAD (Salesforce field names, 1:1, flat).
-- Site fields are flat — the `__c` underscores are part of the API name, NOT
-- nesting, so the step builds a flat payload (no underscore re-nesting), same
-- as `target`.`sitetracker_accounts`.
--
-- Robust trim: source free-text carries stray Unicode separators/control chars
-- (e.g. U+2028 LINE SEPARATOR) that plain TRIM() does not remove. We strip any
-- leading/trailing run of separator (\p{Z}) or control/format (\p{C}) chars with
-- REGEXP_REPLACE while preserving internal spaces. `sitetracker__City__c` is
-- migrated as-is (trimmed only, no INITCAP — confirmed 2026-08-13).
--
-- `price_information` is joined with a plain JOIN: confirmed 2026-08-13 that no
-- in-scope facility has a missing `price_id`, so no LEFT JOIN / IFNULL is
-- needed around `pi.kw_effect`.
--
-- ⚠️ Hard-coded overrides/constants below (Owner_Type__c facility list,
-- Operator__c/Operator_ID__c/previous_CPO__c) come from
-- docs/fieldmapping/sitetracker_site.md — re-verify against a live `describe`
-- of the laddel SiteTracker org before a full run. `Operator__c` was
-- re-verified 2026-08-20 (`ladmig sitetracker describe sitetracker__Site__c
-- --diff`): the picklist's VALUES changed from label strings to numeric
-- codes on the live org, so this must be `'6'`, not `'Laddel NO'`.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`sitetracker_sites`;

CREATE OR REPLACE VIEW `target`.`sitetracker_sites` AS
SELECT
    -- -- SOURCE ----------------------------------------------------------------
    CONCAT('Laddel|Facility|', f.facility_id)                           AS mapping_key,
    CONCAT(
        REGEXP_REPLACE(f.facility_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''),
        ' (fac=', f.facility_id, ')'
    )                                                                   AS source_label,

    -- -- TARGET ID(S) -----------------------------------------------------------
    ssm.target_sf_site_id                                               AS target_sf_site_id,

    -- -- PAYLOAD (Salesforce sitetracker__Site__c field names, 1:1) -----------
    -- Identity
    CONCAT('W047L', LPAD(f.facility_id, 4, '0'))                        AS `Site_ID__c`,
    REGEXP_REPLACE(f.facility_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '') AS `Name`,

    -- Status / type / ownership
    'Under Migration'                                                   AS `sitetracker__Site_Status__c`,
    CASE
        WHEN EXISTS (
            SELECT 1
            FROM `laddel`.`facility`             f2
            JOIN `laddel`.`facility_information` fi2 ON fi2.facility_id = f2.facility_id
            JOIN `laddel`.`price_information`    pi2 ON pi2.price_id    = fi2.price_id
            WHERE f2.organization_id = f.organization_id
              AND pi2.priceModel     = 'SUBSCRIPTION'
        ) THEN 'HOUSING_ASSOCIATION'
        WHEN EXISTS (
            SELECT 1
            FROM `laddel`.`charger` ch
            WHERE ch.facility_id       = f.facility_id
              AND ch.charger_reference LIKE 'LDB%'
        ) THEN 'HOME CHARGER'
        ELSE 'OTHER'
    END                                                                  AS `sitetracker__Site_Type__c`,
    CASE
        WHEN f.facility_id IN (4, 5, 45, 70) THEN 'W-WattifEV'
        ELSE 'C-ClientOwned'
    END                                                                  AS `Owner_Type__c`,
    'NONE'                                                               AS `Load_Management__c`,

    -- Address
    REGEXP_REPLACE(a.address, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')     AS `sitetracker__Street_Address__c`,
    REGEXP_REPLACE(a.city, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')        AS `sitetracker__City__c`,
    REGEXP_REPLACE(a.postal_code, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '') AS `sitetracker__Zip_Code__c`,
    'Norway(NOR)'                                                        AS `Country__c`,

    -- Geolocation (0,0 placeholders sent as-is, same interim decision as Location)
    a.latitude                                                           AS `sitetracker__Location__Latitude__s`,
    a.longitude                                                          AS `sitetracker__Location__Longitude__s`,

    -- Classification (AC default; DC bucket once kw_effect >= 50)
    CASE WHEN pi.kw_effect >= 50 THEN 'CCS2' ELSE 'Type 2' END           AS `EV_Connector_Type__c`,
    CASE
        WHEN pi.kw_effect >= 50 THEN
            CASE WHEN pi.kw_effect > 60 THEN 'DC_above60' ELSE 'DC type (below 60 KWh)' END
        ELSE 'Level 2 AC 22kWh'
    END                                                                  AS `EV_Charging_Level__c`,

    -- Dates (truncated to DATE in SQL so the Python step needs no date coercion)
    DATE(f.creation_date)                                                AS `Open_Date__c`,
    DATE(f.creation_date)                                                AS `Installed_Date__c`,

    -- Operator / previous CPO
    -- ⚠️ 2026-08-20: the `Operator__c` picklist's underlying VALUES changed from
    -- label strings (e.g. 'Laddel NO') to numeric codes ('1'..'6') on the live
    -- org — confirmed via `ladmig sitetracker describe sitetracker__Site__c --diff`.
    -- '6' is the code whose label is "Laddel NO" (see
    -- docs/sitetracker-describes/sitetracker_describe_sitetracker__Site__c.json).
    '6'                                                                  AS `Operator__c`,
    '6'                                                                  AS `Operator_ID__c`,
    'Laddel (eMabler)'                                                   AS `previous_CPO__c`

FROM `laddel`.`facility` f
JOIN `laddel`.`facility_information` fi ON fi.facility_id = f.facility_id
JOIN `laddel`.`address` a               ON a.address_id   = fi.address_id
JOIN `laddel`.`price_information` pi    ON pi.price_id     = fi.price_id
JOIN `laddel`.`organization` o          ON o.organization_id = f.organization_id
JOIN `target`.`facility_migration_eligibility` fme ON fme.facility_id = f.facility_id
LEFT JOIN `target`.`sitetracker_site_mapping` ssm
    ON ssm.mapping_key = CONCAT('Laddel|Facility|', f.facility_id)
WHERE o.migration_status IN ('READY', 'MIGRATE')
  AND fme.should_not_migrate = 0;
