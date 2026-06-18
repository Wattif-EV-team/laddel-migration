-- ============================================================================
-- REPORT VIEW: Reports.CorporateBillingAccounts
-- ============================================================================
-- Human-readable report of all corporate billing accounts (EHF/E-invoicing)
-- for Ops and Finance review.
--
-- Two sources combined via UNION ALL + DISTINCT ON:
--   1. ChargerUserAccounts linked to non-excluded locations via RawChargerUsers
--   2. CorporateRFIDTags accounts linked to non-excluded locations via
--      hard-coded location_guid
--
-- One row per billing partner (keyed by account_owner_guid).  All columns
-- that feed into Target.Partners are shown so reviewers can verify data
-- before partner creation.
--
-- "Created" column: 'Y' if target_partner_id exists in billing_partner_mapping,
--                   'N' otherwise.
--
-- "Quality Issues" column: comma-separated list of data gaps (if any).
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Reports"."CorporateBillingAccounts";

CREATE OR REPLACE VIEW "Reports"."CorporateBillingAccounts" AS

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
        NULL::TEXT AS own_org_number,
        0 AS source_priority
    FROM "Source"."ChargerUserAccounts" a
    WHERE a.invoice_distribution IN ('E-invoicing', 'EHF')
      -- Linked to at least one non-excluded location
      AND EXISTS (
          SELECT 1
          FROM "Source"."AllRawChargerUsers" r
          JOIN "Mapping"."location_mapping" lm
              ON lm.mapping_key = 'Sleet|Location|' || r.location_guid
          WHERE r.account_owner_guid = a.account_owner_guid
            AND lm.exclude = FALSE
      )
),

-- Leg 2: Corporate RFID accounts
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
        REPLACE(btrim(COALESCE(
            NULLIF(btrim(crt.ehf_org_number), ''),
            crt.org_number
        )), ' ', '') AS own_org_number,
        1 AS source_priority
    FROM "Source"."CorporateRFIDTags" crt
    WHERE crt.invoice_distribution IN ('E-invoicing', 'EHF')
      AND EXISTS (
          SELECT 1
          FROM "Mapping"."location_mapping" lm
          WHERE lm.mapping_key = 'Sleet|Location|' || crt.location_guid
            AND lm.exclude = FALSE
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

-- Best Location match per account_number for org data enrichment
location_org AS (
    SELECT DISTINCT ON (loc.account_number)
        loc.account_number,
        loc.org_number,
        loc.ehf_org_number,
        loc.location_owner,
        loc.address       AS loc_address,
        loc.city           AS loc_city,
        loc.postal_code    AS loc_postal_code
    FROM "Source"."Locations" loc
    WHERE loc.account_number IS NOT NULL
    ORDER BY loc.account_number,
             CASE WHEN NULLIF(btrim(COALESCE(loc.ehf_org_number, loc.org_number)), '') IS NOT NULL THEN 0 ELSE 1 END,
             loc."Id"
),

enriched AS (
    SELECT
        ea.account_owner_guid,
        ea.account_owner_name,
        ea.account_number,
        ea.account_classification,
        ea.invoice_distribution,
        ea.email,
        ea.main_phone,

        -- Resolved address (account first, location fallback)
        COALESCE(NULLIF(btrim(ea.address_1_street_1), ''), lo.loc_address) AS address,
        COALESCE(NULLIF(btrim(ea.address_1_city), ''), lo.loc_city) AS city,
        COALESCE(NULLIF(btrim(ea.address_1_postal_code), ''), lo.loc_postal_code::TEXT) AS postal_code,

        -- Org data: prefer account's own (corporate RFID), then Location match
        COALESCE(
            NULLIF(ea.own_org_number, ''),
            REPLACE(btrim(COALESCE(NULLIF(btrim(lo.ehf_org_number), ''), lo.org_number)), ' ', '')
        ) AS org_number,
        lo.location_owner AS loc_owner,

        -- Partner name as it will appear in target
        ea.account_owner_name || ' (Billing)' AS partner_business_name,

        -- Mapping table target ID
        bpm.target_partner_id

    FROM eligible_accounts ea
    LEFT JOIN location_org lo ON lo.account_number::TEXT = ea.account_number
    LEFT JOIN "Mapping"."billing_partner_mapping" bpm
        ON bpm.mapping_key = 'Sleet|BillingPartner|' || ea.account_owner_guid
),

-- Locations where this account's users charge
-- Path 1: via RawChargerUsers access matrix (original accounts)
-- Path 2: via CorporateRFIDTags hard-coded location_guid (corporate accounts)
linked_locations AS (
    SELECT
        account_owner_guid,
        STRING_AGG(DISTINCT location_name, ', ' ORDER BY location_name) AS location_names
    FROM (
        -- Original path: RawChargerUsers → Locations
        SELECT
            r.account_owner_guid::TEXT AS account_owner_guid,
            loc.location_name
        FROM "Source"."AllRawChargerUsers" r
        JOIN "Source"."Locations" loc ON loc."Id"::TEXT = r.location_guid::TEXT
        JOIN "Mapping"."location_mapping" lm
            ON lm.mapping_key = 'Sleet|Location|' || r.location_guid
            AND lm.exclude = FALSE
        WHERE r.account_owner_guid::TEXT IN (SELECT account_owner_guid FROM eligible_accounts)

        UNION

        -- Corporate path: CorporateRFIDTags → Locations via hard-coded location_guid
        SELECT DISTINCT
            crt.account_owner_guid,
            loc.location_name
        FROM "Source"."CorporateRFIDTags" crt
        JOIN "Source"."Locations" loc ON LOWER(loc."Id"::TEXT) = crt.location_guid
        JOIN "Mapping"."location_mapping" lm
            ON lm.mapping_key = 'Sleet|Location|' || crt.location_guid
            AND lm.exclude = FALSE
        WHERE crt.account_owner_guid IN (SELECT account_owner_guid FROM eligible_accounts)
    ) all_links
    GROUP BY account_owner_guid
)

SELECT
    e.partner_business_name                    AS "Partner Name (Billing)",
    e.account_owner_name                       AS "Account Name",
    e.account_number                           AS "Account Number",
    e.account_classification                   AS "Classification",
    e.invoice_distribution                     AS "Invoice Distribution",
    e.email                                    AS "Email",
    e.main_phone                               AS "Phone",
    e.org_number                               AS "Org Number (regNo)",
    CASE WHEN e.org_number IS NOT NULL
         THEN e.org_number || 'MVA'
         ELSE NULL
    END                                        AS "VAT Number",
    e.address                                  AS "Address",
    e.city                                     AS "City",
    e.postal_code                              AS "Postal Code",
    e.loc_owner                                AS "Owns Location",
    ll.location_names                          AS "Linked Locations",
    e.target_partner_id                        AS "Partner ID",

    -- Quality issues (comma-separated, NULL if none)
    NULLIF(
        ARRAY_TO_STRING(
            ARRAY_REMOVE(
                ARRAY[
                    CASE WHEN e.org_number IS NULL         THEN 'Missing org number' END,
                    CASE WHEN e.address IS NULL             THEN 'Missing address' END,
                    CASE WHEN e.account_classification = 'Private Person'
                                                           THEN 'Private person with corporate invoicing' END
                ],
                NULL
            ),
            ', '
        ),
        ''
    )                                          AS "Quality Issues"

FROM enriched e
LEFT JOIN linked_locations ll ON ll.account_owner_guid = e.account_owner_guid
ORDER BY e.account_owner_name;
