-- ============================================================================
-- View: Source.CorporateRFIDTags
-- Description: Intermediate merge view for dedicated Corporate RFID Tag files
--              received from the source CPO for corporate billing accounts.
--
--              Each file lands in a separate source table (e.g.
--              Source.RFIDTags_Gardermoen) and is UNION ALL'd here into a
--              unified shape.  When a new file arrives, add a new leg.
--
--              Resolves account_owner_guid via Source.ChargerUserAccounts
--              (joined on account_number).  Accounts not present in
--              ChargerUserAccounts get a synthetic key so they can still
--              flow through Users, Partners, and PartnerInvites.
--
--              Each leg hard-codes the location_guid(s) the RFID tags are
--              associated with.  These RFID files are grouped per location
--              (or other criteria) and the accounts in them do NOT appear
--              in RawChargerUsers, so the normal location link through
--              ChargerGroups is not available.  The hard-coded location_guid
--              allows downstream views to join to location_mapping and
--              respect the migrate flag.
--
-- Grain: One row per RFID tag (across all RFIDTags_* source tables)
-- Source: Source.RFIDTags_* tables + Source.ChargerUserAccounts (for GUID resolution)
--
-- Downstream consumers (out of scope until wired in):
--   Source.RFIDs (104)          – RFID tag data
--   Source.BillingAccounts (205) – Account-level data for billing partners
--   Target.Users (301)          – User creation (via BillingAccounts)
--   Target.IdTags (302)         – IdTag creation (via RFIDs)
--   Target.Partners (303)       – Billing partner creation (via BillingAccounts)
--   Target.PartnerInvites (314) – Partner invite creation (via BillingAccounts)
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Source"."CorporateRFIDTags";

CREATE OR REPLACE VIEW "Source"."CorporateRFIDTags" AS

-- ============================================================================
-- Leg 1: RFIDTags_Gardermoen
-- Location: Gardermoen Leiebilservice AS (location_nr 3006)
--           GUID cbdd1089-8965-ed11-9561-6045bd905ded  (migrate=TRUE, W047903)
-- Companies: SHARED MOBILITY, First Rent A Car Norway AS,
--            RAC Norway As, Autoleie AS
-- ============================================================================
SELECT
    -- RFID tag identity
    rg."Id"                           AS rfid_tag_guid,
    rg.hex,
    rg.uid,
    rg.description_tag                AS rfid_description,
    rg.tag_type                       AS rfid_tag_type,
    rg.rfid_tag_number,
    rg.status,

    -- Account identity — resolve canonical account_owner_guid via
    -- ChargerUserAccounts.  Fallback to a deterministic synthetic key
    -- when the account is not (yet) present in RawChargerUsers.
    COALESCE(
        cua.account_owner_guid::TEXT,
        'CorporateRFID|' || rg.account_number
    )                                 AS account_owner_guid,

    -- Account data (from the RFID file itself — authoritative for these files)
    rg.customer                       AS account_owner_name,
    rg.account_number,
    rg.account_classification,
    rg.invoice_distribution,
    rg.email,
    rg.invoice_email,
    rg.main_phone,
    rg.org_number,
    rg.ehf_org_number,

    -- Address (primary)
    rg.address_1_street               AS address_1_street_1,
    rg.address_1_zip                  AS address_1_postal_code,
    rg.address_1_city,
    rg.address_1_country,

    -- Address (secondary)
    rg.address_2_street               AS address_2_street_1,
    rg.address_2_zip                  AS address_2_postal_code,
    rg.address_2_city,
    rg.address_2_country,

    -- Location link — hard-coded per source file because these accounts
    -- are not present in RawChargerUsers and have no ChargerGroup path
    -- to a location.  Used by downstream views to check location_mapping.migrate.
    'cbdd1089-8965-ed11-9561-6045bd905ded'::TEXT AS location_guid,

    -- Provenance
    'RFIDTags_Gardermoen'::TEXT       AS source_table

FROM "Source"."RFIDTags_Gardermoen" rg
LEFT JOIN "Source"."ChargerUserAccounts" cua
    ON cua.account_number::TEXT = rg.account_number
WHERE rg.tag_type != 'Virtual'

UNION ALL

-- ============================================================================
-- Leg 2: RFIDTags_Geomatikk
-- Location: Økernveien 94 AS (location_nr 1021)
--           GUID 3c45bdab-5859-ea11-a811-000d3a20f3f8  (W047752)
-- Companies: Geomatikk AS
-- Note: Source file has minimal account data — only customer name and
--       account_number.  Missing fields hard-coded:
--         invoice_distribution = 'EHF'
--         account_classification = 'Company'
--         email = 'firmapost@geomatikk.no'
--       All other account/address fields are NULL.
-- ============================================================================
SELECT
    -- RFID tag identity
    rg."Id"                           AS rfid_tag_guid,
    rg.hex,
    rg.uid,
    rg.description_tag                AS rfid_description,
    rg.tag_type                       AS rfid_tag_type,
    rg.rfid_tag_number,
    NULL::TEXT                        AS status,

    -- Account identity — resolve canonical account_owner_guid via
    -- ChargerUserAccounts.  Fallback to a deterministic synthetic key.
    COALESCE(
        cua.account_owner_guid::TEXT,
        'CorporateRFID|' || rg.account_number
    )                                 AS account_owner_guid,

    -- Account data (hard-coded; source file only has customer + account_number)
    rg.customer                       AS account_owner_name,
    rg.account_number,
    'Company'::TEXT                   AS account_classification,
    'EHF'::TEXT                       AS invoice_distribution,
    'firmapost@geomatikk.no'::TEXT   AS email,
    NULL::TEXT                        AS invoice_email,
    NULL::TEXT                        AS main_phone,
    NULL::TEXT                        AS org_number,
    NULL::TEXT                        AS ehf_org_number,

    -- Address (not available in source file)
    NULL::TEXT                        AS address_1_street_1,
    NULL::TEXT                        AS address_1_postal_code,
    NULL::TEXT                        AS address_1_city,
    NULL::TEXT                        AS address_1_country,

    NULL::TEXT                        AS address_2_street_1,
    NULL::TEXT                        AS address_2_postal_code,
    NULL::TEXT                        AS address_2_city,
    NULL::TEXT                        AS address_2_country,

    -- Location link — hard-coded (Økernveien 94 AS)
    '3c45bdab-5859-ea11-a811-000d3a20f3f8'::TEXT AS location_guid,

    -- Provenance
    'RFIDTags_Geomatikk'::TEXT       AS source_table

FROM "Source"."RFIDTags_Geomatikk" rg
LEFT JOIN "Source"."ChargerUserAccounts" cua
    ON cua.account_number::TEXT = rg.account_number
WHERE rg.tag_type != 'Virtual'

UNION ALL

-- ============================================================================
-- Leg 3: RFIDTags_AutoleieOslo
-- Location: Gardermoen Leiebilservice AS (location_nr 3006)
--           GUID cbdd1089-8965-ed11-9561-6045bd905ded  (migrate=TRUE, W047903)
-- Companies: Autoleie Oslo AS (org 992432489)
-- Note: Tags used on Sixt chargers at Gardermoen.
--       Invoice address overridden per customer request:
--         Kjøita 40, 4630 Kristiansand (EHF preferred)
--         Invoice email: 992432489@fakturapost.no
-- ============================================================================
SELECT
    -- RFID tag identity
    rg."Id"                           AS rfid_tag_guid,
    rg.hex,
    rg.uid,
    rg.description_tag                AS rfid_description,
    rg.tag_type                       AS rfid_tag_type,
    rg.rfid_tag_number,
    rg.status,

    -- Account identity — resolve canonical account_owner_guid via
    -- ChargerUserAccounts.  Fallback to a deterministic synthetic key.
    COALESCE(
        cua.account_owner_guid::TEXT,
        'CorporateRFID|' || rg.account_number
    )                                 AS account_owner_guid,

    -- Account data (from the RFID file itself)
    rg.customer                       AS account_owner_name,
    rg.account_number,
    rg.account_classification,
    rg.invoice_distribution,
    rg.email,
    -- Override invoice email per customer request
    '992432489@fakturapost.no'::TEXT  AS invoice_email,
    rg.main_phone,
    rg.org_number,
    rg.ehf_org_number,

    -- Address overridden per customer request (invoicing address)
    'Kjøita 40'::TEXT                 AS address_1_street_1,
    '4630'::TEXT                      AS address_1_postal_code,
    'Kristiansand'::TEXT              AS address_1_city,
    'NO'::TEXT                        AS address_1_country,

    -- Address (secondary — from source file)
    rg.address_2_street               AS address_2_street_1,
    rg.address_2_zip::TEXT            AS address_2_postal_code,
    rg.address_2_city,
    rg.address_2_country,

    -- Location link — hard-coded (Gardermoen Leiebilservice AS)
    'cbdd1089-8965-ed11-9561-6045bd905ded'::TEXT AS location_guid,

    -- Provenance
    'RFIDTags_AutoleieOslo'::TEXT    AS source_table

FROM "Source"."RFIDTags_AutoleieOslo" rg
LEFT JOIN "Source"."ChargerUserAccounts" cua
    ON cua.account_number::TEXT = rg.account_number
WHERE rg.tag_type != 'Virtual'

-- ============================================================================
-- To add a new file, append a UNION ALL leg.  Required per-leg decisions:
--   1. location_guid: the location(s) these RFID tags are tied to
--   2. source_table: a descriptive literal for provenance
--   3. hard-code any missing account fields (see Leg 2 for an example)
-- ============================================================================
;
