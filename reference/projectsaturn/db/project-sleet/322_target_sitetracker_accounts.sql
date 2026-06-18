-- ============================================================================
-- TARGET VIEW: SiteTracker Accounts
-- ============================================================================
-- One row per organization (grouped by MasterPartnerResolution.grouping_key).
-- Only for locations eligible for SiteTracker (load_to_sitetracker = TRUE).
-- Produces columns matching Salesforce Account field API names.
--
-- Includes mapping_table + mapping_key + TargetSfAccountId for the generic
-- create-or-update pattern (NULL TargetSfAccountId = needs creation).
-- Also includes installer companies that need Account creation.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Target"."SiteTrackerAccounts" CASCADE;
CREATE VIEW "Target"."SiteTrackerAccounts" AS

WITH eligible_locations AS (
    SELECT
        lm.mapping_key,
        lm.project_code,
        mpr.grouping_key,
        loc.location_owner,
        loc.location_name,
        loc.org_number,
        loc.ehf_org_number,
        -- Postal/business address (address_1 from Mer), fallback to location address
        COALESCE(NULLIF(btrim(loc.address_1_street_1), ''), loc.address) AS postal_street,
        COALESCE(NULLIF(btrim(loc.address_1_city), ''), loc.city)        AS postal_city,
        COALESCE(NULLIF(btrim(loc.address_1_zip_postal_code), ''), loc.postal_code::TEXT) AS postal_code,
        loc.email
    FROM "Mapping"."location_mapping" lm
    JOIN "Source"."Locations" loc ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    JOIN "Mapping"."MasterPartnerResolution" mpr ON mpr.mapping_key = lm.mapping_key
    WHERE lm.load_to_sitetracker = TRUE
      AND (lm.merge_with_mapping_key IS NULL
           OR lm.mapping_key = lm.merge_with_mapping_key)
),

grouped_accounts AS (
    -- One row per grouping_key (organization)
    -- Use MIN/FIRST-value strategy for fields that should be identical within a group
    SELECT DISTINCT ON (el.grouping_key)
        el.grouping_key,
        COALESCE(el.location_owner, el.location_name) AS account_name,
        -- Prefer ehf_org_number (validated for invoicing), fallback to org_number
        COALESCE(
            NULLIF(btrim(el.ehf_org_number), ''),
            NULLIF(btrim(el.org_number), '')
        ) AS raw_org_number,
        el.postal_street,
        el.postal_city,
        el.postal_code,
        el.email
    FROM eligible_locations el
    -- Prefer rows that have an org_number
    ORDER BY el.grouping_key,
             COALESCE(NULLIF(btrim(el.ehf_org_number), ''), NULLIF(btrim(el.org_number), '')) NULLS LAST,
             el.mapping_key
)

SELECT
    -- Mapping columns (generic pattern)
    'sitetracker_account_mapping'                AS mapping_table,
    'Sleet|SiteTrackerAccount|' || ga.grouping_key AS mapping_key,
    ga.grouping_key,
    am.target_sf_account_id                      AS "TargetSfAccountId",

    -- SiteTracker Account fields
    ga.account_name                              AS "Name",
    -- Normalized org number: strip spaces, dashes, dots
    REPLACE(REPLACE(REPLACE(btrim(COALESCE(ga.raw_org_number, '')), ' ', ''), '-', ''), '.', '')
        AS "Business_Registration_Number__c",
    'Customer'                                   AS "Type",
    ga.postal_street                             AS "BillingStreet",
    ga.postal_city                               AS "BillingCity",
    ga.postal_code                               AS "BillingPostalCode",
    'Norway'                                     AS "BillingCountry",
    -- Shipping address (visible in Wattif's SiteTracker UI) — same as billing
    ga.postal_street                             AS "ShippingStreet",
    ga.postal_city                               AS "ShippingCity",
    ga.postal_code                               AS "ShippingPostalCode",
    'Norway'                                     AS "ShippingCountry",
    -- Contact email
    LOWER(NULLIF(btrim(ga.email), ''))            AS "Email__c"

FROM grouped_accounts ga
LEFT JOIN "Mapping"."sitetracker_account_mapping" am
    ON am.mapping_key = 'Sleet|SiteTrackerAccount|' || ga.grouping_key

UNION ALL

-- ── Installer companies (from lookup table) ──────────────────────────────────
-- These companies need Accounts in SiteTracker so Site Relations can reference them.
-- Resolved at runtime via org_number → sitetracker_account_mapping.
SELECT
    -- Mapping columns
    'sitetracker_account_mapping'                AS mapping_table,
    'Sleet|SiteTrackerAccount|installer|' || il.org_number AS mapping_key,
    'installer|' || il.org_number                AS grouping_key,
    am.target_sf_account_id                      AS "TargetSfAccountId",

    -- SiteTracker Account fields
    il.sf_account_name                           AS "Name",
    il.org_number                                AS "Business_Registration_Number__c",
    'Contractor'                                 AS "Type",
    NULL                                         AS "BillingStreet",
    NULL                                         AS "BillingCity",
    NULL                                         AS "BillingPostalCode",
    'Norway'                                     AS "BillingCountry",
    NULL                                         AS "ShippingStreet",
    NULL                                         AS "ShippingCity",
    NULL                                         AS "ShippingPostalCode",
    'Norway'                                     AS "ShippingCountry",
    NULL                                         AS "Email__c"
FROM (
    SELECT DISTINCT ON (il2.org_number)
        il2.org_number,
        il2.sf_account_name
    FROM "Mapping"."SiteTrackerInstallerLookup" il2
    WHERE il2.skip_reason IS NULL
      AND il2.org_number IS NOT NULL
    ORDER BY il2.org_number, il2.installer_name_lower
) il
LEFT JOIN "Mapping"."sitetracker_account_mapping" am
    ON am.mapping_key = 'Sleet|SiteTrackerAccount|installer|' || il.org_number

UNION ALL

-- ── Historical installer companies (from Mer source) ────────────────────────
-- Same pattern: resolved at runtime via org_number → sitetracker_account_mapping.
SELECT
    -- Mapping columns
    'sitetracker_account_mapping'                AS mapping_table,
    'Sleet|SiteTrackerAccount|installer|' || hil.org_number AS mapping_key,
    'installer|' || hil.org_number               AS grouping_key,
    am.target_sf_account_id                      AS "TargetSfAccountId",

    -- SiteTracker Account fields
    hil.sf_account_name                          AS "Name",
    hil.org_number                               AS "Business_Registration_Number__c",
    'Contractor'                                 AS "Type",
    NULL                                         AS "BillingStreet",
    NULL                                         AS "BillingCity",
    NULL                                         AS "BillingPostalCode",
    'Norway'                                     AS "BillingCountry",
    NULL                                         AS "ShippingStreet",
    NULL                                         AS "ShippingCity",
    NULL                                         AS "ShippingPostalCode",
    'Norway'                                     AS "ShippingCountry",
    NULL                                         AS "Email__c"
FROM (
    SELECT DISTINCT ON (hil2.org_number)
        hil2.org_number,
        hil2.sf_account_name
    FROM "Mapping"."SiteTrackerHistoricalInstallerLookup" hil2
    WHERE hil2.skip_reason IS NULL
      AND hil2.org_number IS NOT NULL
      -- Exclude orgs already covered by main installer lookup
      AND hil2.org_number NOT IN (
          SELECT il3.org_number FROM "Mapping"."SiteTrackerInstallerLookup" il3
          WHERE il3.org_number IS NOT NULL
      )
    ORDER BY hil2.org_number, hil2.mer_installer_name_lower
) hil
LEFT JOIN "Mapping"."sitetracker_account_mapping" am
    ON am.mapping_key = 'Sleet|SiteTrackerAccount|installer|' || hil.org_number;
