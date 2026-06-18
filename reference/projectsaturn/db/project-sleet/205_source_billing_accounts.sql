-- ============================================================================
-- SHARED VIEW: Source.BillingAccounts
-- ============================================================================
-- Billing-eligible accounts enriched with organisation data.
--
-- Two sources combined via UNION ALL + DISTINCT ON:
--   1. ChargerUserAccounts with invoice_distribution IN ('E-invoicing','EHF')
--      linked to a migrating location via RawChargerUsers
--   2. CorporateRFIDTags accounts with invoice_distribution IN
--      ('E-invoicing','EHF') linked to a migrating location via hard-coded
--      location_guid
--
-- Org data for ChargerUserAccounts comes from Source.Locations (joined via
-- account_number).  For Corporate accounts the org data is carried directly
-- in the RFID file.  When no Location matches, those columns are NULL.
--
-- Grain: one row per account_owner_guid.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Source"."BillingAccounts";

CREATE OR REPLACE VIEW "Source"."BillingAccounts" AS

-- Leg 1: Accounts from ChargerUserAccounts (original path)
WITH charger_eligible AS (
    SELECT
        a.account_owner_guid::TEXT AS account_owner_guid,
        a.account_owner_name,
        a.account_number::TEXT AS account_number,
        a.account_classification,
        a.invoice_distribution,
        a.email,
        a.main_phone,
        a.address_1_street_1,
        a.address_1_city,
        a.address_1_postal_code::TEXT AS address_1_postal_code,
        -- Org data not available at this level — resolved via location_org below
        NULL::TEXT AS own_org_number,
        0 AS source_priority  -- wins on duplicate account_owner_guid
    FROM "Source"."ChargerUserAccounts" a
    WHERE a.invoice_distribution IN ('E-invoicing', 'EHF')
      -- Exclude Private Persons from target views (temporary filter)
      AND a.account_classification != 'Private Person'
      -- Only accounts linked to at least one migrating location
      AND EXISTS (
          SELECT 1
          FROM "Source"."AllRawChargerUsers" r
          JOIN "Mapping"."location_mapping" lm
              ON lm.mapping_key = 'Sleet|Location|' || r.location_guid
          WHERE r.account_owner_guid = a.account_owner_guid
            AND lm.migrate = TRUE
      )
),

-- Leg 2: Corporate RFID accounts (from dedicated RFID files)
corporate_eligible AS (
    SELECT DISTINCT ON (crt.account_owner_guid)
        crt.account_owner_guid,
        crt.account_owner_name,
        crt.account_number,
        crt.account_classification,
        crt.invoice_distribution,
        crt.email,
        crt.main_phone,
        crt.address_1_street_1,
        crt.address_1_city                       AS address_1_city,
        crt.address_1_postal_code,
        -- Org data comes directly from the RFID file
        REPLACE(btrim(COALESCE(
            NULLIF(btrim(crt.ehf_org_number), ''),
            crt.org_number
        )), ' ', '') AS own_org_number,
        1 AS source_priority  -- yields to ChargerUserAccounts on overlap
    FROM "Source"."CorporateRFIDTags" crt
    WHERE crt.invoice_distribution IN ('E-invoicing', 'EHF')
      AND crt.account_classification != 'Private Person'
      -- Linked to a migrating location via hard-coded location_guid
      AND EXISTS (
          SELECT 1
          FROM "Mapping"."location_mapping" lm
          WHERE lm.mapping_key = 'Sleet|Location|' || crt.location_guid
            AND lm.migrate = TRUE
      )
    ORDER BY crt.account_owner_guid
),

-- Merge both legs, prefer ChargerUserAccounts row on overlap
eligible_accounts AS (
    SELECT DISTINCT ON (account_owner_guid)
        account_owner_guid,
        account_owner_name,
        account_number,
        account_classification,
        invoice_distribution,
        email,
        main_phone,
        address_1_street_1,
        address_1_city,
        address_1_postal_code,
        own_org_number
    FROM (
        SELECT * FROM charger_eligible
        UNION ALL
        SELECT * FROM corporate_eligible
    ) combined
    ORDER BY account_owner_guid, source_priority
),

-- Pick the best Location match per account_number (prefer one with org data)
location_org AS (
    SELECT DISTINCT ON (loc.account_number)
        loc.account_number,
        loc.org_number,
        loc.ehf_org_number,
        loc.address       AS loc_address,
        loc.city           AS loc_city,
        loc.postal_code    AS loc_postal_code,
        loc.location_owner AS loc_owner
    FROM "Source"."Locations" loc
    WHERE loc.account_number IS NOT NULL
    ORDER BY loc.account_number,
             -- Prefer locations with an org number
             CASE WHEN NULLIF(btrim(COALESCE(loc.ehf_org_number, loc.org_number)), '') IS NOT NULL THEN 0 ELSE 1 END,
             loc."Id"
)

SELECT
    ea.account_owner_guid,
    ea.account_owner_name,
    ea.account_number,
    ea.account_classification,
    ea.invoice_distribution,
    ea.email,
    ea.main_phone,

    -- Address: prefer account's own address, fall back to location
    COALESCE(NULLIF(btrim(ea.address_1_street_1), ''), lo.loc_address) AS address,
    COALESCE(NULLIF(btrim(ea.address_1_city), ''), lo.loc_city) AS city,
    COALESCE(NULLIF(btrim(ea.address_1_postal_code), ''), lo.loc_postal_code::TEXT) AS postal_code,

    -- Org data: prefer account's own (corporate RFID), then Location match
    COALESCE(
        NULLIF(ea.own_org_number, ''),
        REPLACE(btrim(COALESCE(NULLIF(btrim(lo.ehf_org_number), ''), lo.org_number)), ' ', '')
    ) AS org_number,
    lo.loc_owner

FROM eligible_accounts ea
LEFT JOIN location_org lo ON lo.account_number::TEXT = ea.account_number;
