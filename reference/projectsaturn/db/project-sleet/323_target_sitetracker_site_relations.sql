-- ============================================================================
-- TARGET VIEW: SiteTracker Site Relations
-- ============================================================================
-- Three potential rows per location (UNION ALL):
--   1. OWNER of SITE — links site to its owner Account
--   2. INSTALLER — links site to current/migration installer (from planning)
--   3. INSTALLER — links site to historical installer (from Mer source data)
--
-- Requires Phase 2 (Sites) and Phase 3 (Accounts) mapping tables to be
-- populated with SF IDs before this view returns rows (filters out NULLs).
--
-- Includes mapping_table + mapping_key + TargetSfSiteRelationId for the
-- generic create-or-update pattern.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Target"."SiteTrackerSiteRelations" CASCADE;
CREATE VIEW "Target"."SiteTrackerSiteRelations" AS

-- OWNER of SITE relations (only where both site and account are resolved)
SELECT
    'sitetracker_site_relation_mapping'          AS mapping_table,
    'Sleet|SiteRelation|' || lm.project_code || '|OWNER of SITE' AS mapping_key,
    rm.target_sf_site_relation_id                AS "TargetSfSiteRelationId",
    lm.project_code,
    'OWNER of SITE'                              AS role,
    sm.target_sf_site_id                         AS site_sf_id,
    am.target_sf_account_id                      AS company_sf_id,
    CASE UPPER(btrim(lm.status))
        WHEN 'DONE'  THEN COALESCE(lm.migration_date, CURRENT_DATE)
        WHEN 'READY' THEN lm.migration_date
    END                                          AS relation_start_date,
    'MerB2B (Sleet)'                             AS previous_cpo
FROM "Mapping"."location_mapping" lm
JOIN "Mapping"."MasterPartnerResolution" mpr ON mpr.mapping_key = lm.mapping_key
JOIN "Mapping"."sitetracker_site_mapping" sm ON sm.mapping_key = lm.mapping_key
JOIN "Mapping"."sitetracker_account_mapping" am ON am.grouping_key = mpr.grouping_key
LEFT JOIN "Mapping"."sitetracker_site_relation_mapping" rm
    ON rm.mapping_key = 'Sleet|SiteRelation|' || lm.project_code || '|OWNER of SITE'
WHERE lm.load_to_sitetracker = TRUE
  AND (lm.merge_with_mapping_key IS NULL
       OR lm.mapping_key = lm.merge_with_mapping_key)
  AND sm.target_sf_site_id IS NOT NULL
  AND am.target_sf_account_id IS NOT NULL

UNION ALL

-- INSTALLER relations (only where site is resolved; company resolved at runtime or from mapping)
SELECT
    'sitetracker_site_relation_mapping'          AS mapping_table,
    'Sleet|SiteRelation|' || lm.project_code || '|INSTALLER' AS mapping_key,
    rm.target_sf_site_relation_id                AS "TargetSfSiteRelationId",
    lm.project_code,
    'INSTALLER'                                  AS role,
    sm.target_sf_site_id                         AS site_sf_id,
    am_inst.target_sf_account_id                 AS company_sf_id,
    CASE UPPER(btrim(lm.status))
        WHEN 'DONE'  THEN COALESCE(lm.migration_date, CURRENT_DATE)
        WHEN 'READY' THEN lm.migration_date
    END                                          AS relation_start_date,
    'MerB2B (Sleet)'                             AS previous_cpo
FROM "Mapping"."location_mapping" lm
JOIN "Mapping"."sitetracker_site_mapping" sm ON sm.mapping_key = lm.mapping_key
JOIN "Mapping"."SiteTrackerInstallerLookup" il
    ON il.installer_name_lower = LOWER(btrim(lm.wattif_installer))
JOIN "Mapping"."sitetracker_account_mapping" am_inst
    ON am_inst.mapping_key = 'Sleet|SiteTrackerAccount|installer|' || il.org_number
LEFT JOIN "Mapping"."sitetracker_site_relation_mapping" rm
    ON rm.mapping_key = 'Sleet|SiteRelation|' || lm.project_code || '|INSTALLER'
WHERE lm.load_to_sitetracker = TRUE
  AND (lm.merge_with_mapping_key IS NULL
       OR lm.mapping_key = lm.merge_with_mapping_key)
  AND lm.wattif_installer IS NOT NULL
  AND btrim(lm.wattif_installer) <> ''
  AND il.skip_reason IS NULL
  AND sm.target_sf_site_id IS NOT NULL

UNION ALL

-- HISTORICAL INSTALLER relations (from Mer source data — original installer before migration)
-- Uses location_commissioned as start date (when the charger was originally installed)
SELECT
    'sitetracker_site_relation_mapping'          AS mapping_table,
    'Sleet|SiteRelation|' || lm.project_code || '|HISTORICAL_INSTALLER' AS mapping_key,
    rm.target_sf_site_relation_id                AS "TargetSfSiteRelationId",
    lm.project_code,
    'INSTALLER'                                  AS role,
    sm.target_sf_site_id                         AS site_sf_id,
    am_hist.target_sf_account_id                 AS company_sf_id,
    loc.location_commissioned::DATE              AS relation_start_date,
    'MerB2B (Sleet)'                             AS previous_cpo
FROM "Mapping"."location_mapping" lm
JOIN "Source"."Locations" loc ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
JOIN "Mapping"."sitetracker_site_mapping" sm ON sm.mapping_key = lm.mapping_key
JOIN "Mapping"."SiteTrackerHistoricalInstallerLookup" hil
    ON hil.mer_installer_name_lower = LOWER(btrim(loc.installer_company))
JOIN "Mapping"."sitetracker_account_mapping" am_hist
    ON am_hist.mapping_key = 'Sleet|SiteTrackerAccount|installer|' || hil.org_number
LEFT JOIN "Mapping"."sitetracker_site_relation_mapping" rm
    ON rm.mapping_key = 'Sleet|SiteRelation|' || lm.project_code || '|HISTORICAL_INSTALLER'
WHERE lm.load_to_sitetracker = TRUE
  AND (lm.merge_with_mapping_key IS NULL
       OR lm.mapping_key = lm.merge_with_mapping_key)
  AND NULLIF(btrim(loc.installer_company), '') IS NOT NULL
  AND hil.skip_reason IS NULL
  AND sm.target_sf_site_id IS NOT NULL;
