SET ROLE db_sleetmigration_owner;

-- Target.Partners view for Project Sleet
-- Two partner types combined via UNION ALL:
--   1. Station-based (infrastructure) partners: derived from Source.Locations grouped by ProjectCode
--   2. Corporate billing partners: derived from ChargerUserAccounts with EHF/E-invoicing
-- Joins with Mapping.LocationMapping for migration flags and target IDs
-- Joins with Mapping.MasterPartnerResolution for target partner IDs and org-level master partner

DROP VIEW IF EXISTS "Target"."Partners";

CREATE OR REPLACE VIEW "Target"."Partners" AS

-- ============================================================================
-- Part 1: Station-based (infrastructure) partners
-- ============================================================================
SELECT 
    -- Mapping columns (uses project_code_mapping for partner mapping)
    'project_code_mapping'::TEXT AS mapping_table,
    'Sleet|ProjectCode|' || lm.project_code AS mapping_key,
    NULL::TEXT AS merge_with_mapping_key,
    
    -- Source identifiers for debugging
    SUBSTRING(lm.mapping_key FROM 'Sleet\\|Location\\|(.*)') AS source_location_guid,
    
    -- Target ID (populated after creation)
    mpr.target_partner_id AS "TargetPartnerID",
    
    -- Project code
    lm.project_code AS "ProjectCode",
    
    -- Business information
    -- Append ' (Master)' when org has multiple partners and this is the master (2nd run only)
    COALESCE(loc.location_owner, loc.location_name) || ' [' || lm.project_code || ']'
        || CASE
            WHEN mpr.partner_count >= 2
                 AND mpr.target_partner_id = mpr.master_target_partner_id
                 AND mpr.target_partner_id IS NOT NULL
            THEN ' (Master)'
            ELSE ''
           END AS "businessName",
    COALESCE(loc.location_owner, loc.location_name) AS "name",
    
    -- Registration number: prefer EHF org number, fallback to org number, remove spaces
    REPLACE(btrim(COALESCE(NULLIF(btrim(loc.ehf_org_number), ''), loc.org_number)), ' ', '') AS "regNo",
    
    -- VAT number: regNo + 'MVA'
    REPLACE(btrim(COALESCE(NULLIF(btrim(loc.ehf_org_number), ''), loc.org_number)), ' ', '') || 'MVA' AS "vatNo",
    
    -- Address: prefer address_2 fields, fallback to address
    COALESCE(
        NULLIF(btrim(COALESCE(loc.address_2_street_1, '') || ' ' || COALESCE(loc.address_2_street_2, '')), ''),
        loc.address
    ) AS "address",
    
    -- Postcode: prefer address_2 postal code, fallback to postal_code
    COALESCE(
        NULLIF(btrim(loc.address_2_zip_postal_code), ''),
        loc.postal_code::TEXT
    ) AS "postcode",
    
    -- City: static placeholder (TODO: fix when address_2_city data is available)
    '-'::TEXT AS "city",
    
    -- Country
    'NO'::TEXT AS "country",
    
    -- Region
    ''::TEXT AS "region",
    
    -- Administrative contact details
    ''::TEXT AS "contactDetails_administrative_contactPerson",
    COALESCE(loc.email, '')::TEXT AS "contactDetails_administrative_email",
    ''::TEXT AS "contactDetails_administrative_phone",
    
    -- Technical contact details
    ''::TEXT AS "contactDetails_technical_contactPerson",
    ''::TEXT AS "contactDetails_technical_email",
    ''::TEXT AS "contactDetails_technical_phone",
    
    -- Billing contact details
    COALESCE(loc.invoice_ref, '')::TEXT AS "contactDetails_billing_contactPerson",
    COALESCE(loc.invoice_email, '')::TEXT AS "contactDetails_billing_email",
    ''::TEXT AS "contactDetails_billing_phone",
    
    -- Notifications
    FALSE::BOOLEAN AS "notifications_technical_chargePointFaults",
    FALSE::BOOLEAN AS "notifications_billing_settlementReports",
    
    -- Monthly platform fee from LocationMapping
    lm.partner_monthly_fee AS "monthlyPlatformFee",
    
    -- Partner options
    FALSE::BOOLEAN AS "options_createUsers",
    FALSE::BOOLEAN AS "options_addUserBalance",
    -- supplierOnReceipts: TRUE if MDU model, FALSE otherwise
    CASE WHEN UPPER(btrim(lm.partner_model)) = 'CO-MDU' THEN TRUE ELSE FALSE END::BOOLEAN AS "options_supplierOnReceipts",
    TRUE::BOOLEAN AS "options_allowToControlTariffs",
    FALSE::BOOLEAN AS "options_allowToControlTariffGroups",
    TRUE::BOOLEAN AS "options_allowViewingUsersWhoAcceptedInvite",
    
    -- Corporate billing
    FALSE::BOOLEAN AS "corporateBilling_enabled",
    NULL::NUMERIC AS "corporateBilling_monthlyLimit",
    NULL::NUMERIC AS "corporateBilling_discount",
    
    -- External ID (same as ProjectCode)
    lm.project_code AS "externalId",
    
    -- Bank details
    NULL::TEXT AS "bankDetails_bankIban",
    lm.partner_monthly_fee_description AS "bankDetails_bankName",  -- Used for fee description
    NULL::TEXT AS "bankDetails_bankAddress",
    NULL::TEXT AS "bankDetails_bankCode",
    loc.bank_account_number AS "bankDetails_bankAccountNumber",
    ''::TEXT AS "bankDetails_bankAccountType"

FROM "Mapping"."location_mapping" lm
JOIN "Source"."Locations" loc ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
LEFT JOIN "Mapping"."MasterPartnerResolution" mpr ON mpr.mapping_key = lm.mapping_key

WHERE 
    -- Only include locations flagged for migration
    lm.migrate = TRUE
    -- Only include primary location for each project_code (merge logic)
    AND (
        lm.merge_with_mapping_key IS NULL 
        OR lm.mapping_key = lm.merge_with_mapping_key
    )

UNION ALL

-- ============================================================================
-- Part 2: Corporate billing partners
-- ============================================================================
-- Derived from ChargerUserAccounts with invoice_distribution = 'E-invoicing' or 'EHF'.
-- These are SEPARATE partners from infrastructure partners even if the same
-- organisation owns both infrastructure and billing accounts.
-- Org data (regNo, vatNo) is enriched from Source.Locations via account_number
-- in the Source.BillingAccounts shared view (205).
-- ============================================================================
SELECT
    -- Mapping columns (uses billing_partner_mapping)
    'billing_partner_mapping'::TEXT AS mapping_table,
    'Sleet|BillingPartner|' || ba.account_owner_guid AS mapping_key,
    NULL::TEXT AS merge_with_mapping_key,

    -- Source identifier for debugging
    ba.account_owner_guid::TEXT AS source_location_guid,

    -- Target ID (populated after creation)
    bpm.target_partner_id AS "TargetPartnerID",

    -- No project code for billing partners
    NULL::TEXT AS "ProjectCode",

    -- Business information — name with (Billing) suffix, no project code
    ba.account_owner_name || ' (Billing)' AS "businessName",
    ba.account_owner_name AS "name",

    -- Registration number: from Location org data (may be NULL)
    ba.org_number AS "regNo",

    -- VAT number: regNo + 'MVA' (NULL if no org_number)
    CASE WHEN ba.org_number IS NOT NULL
         THEN ba.org_number || 'MVA'
         ELSE NULL
    END AS "vatNo",

    -- Address from account (fallback to location in BillingAccounts view)
    COALESCE(ba.address, '')::TEXT AS "address",
    COALESCE(ba.postal_code, '')::TEXT AS "postcode",
    COALESCE(ba.city, '-')::TEXT AS "city",
    'NO'::TEXT AS "country",
    ''::TEXT AS "region",

    -- Administrative contact
    ''::TEXT AS "contactDetails_administrative_contactPerson",
    COALESCE(ba.email, '')::TEXT AS "contactDetails_administrative_email",
    ''::TEXT AS "contactDetails_administrative_phone",

    -- Technical contact (empty for billing partners)
    ''::TEXT AS "contactDetails_technical_contactPerson",
    ''::TEXT AS "contactDetails_technical_email",
    ''::TEXT AS "contactDetails_technical_phone",

    -- Billing contact
    ''::TEXT AS "contactDetails_billing_contactPerson",
    COALESCE(ba.email, '')::TEXT AS "contactDetails_billing_email",
    ''::TEXT AS "contactDetails_billing_phone",

    -- Notifications
    FALSE::BOOLEAN AS "notifications_technical_chargePointFaults",
    FALSE::BOOLEAN AS "notifications_billing_settlementReports",

    -- No platform fee for billing partners
    0::NUMERIC AS "monthlyPlatformFee",

    -- Partner options (all disabled for billing partners)
    FALSE::BOOLEAN AS "options_createUsers",
    FALSE::BOOLEAN AS "options_addUserBalance",
    FALSE::BOOLEAN AS "options_supplierOnReceipts",
    FALSE::BOOLEAN AS "options_allowToControlTariffs",
    FALSE::BOOLEAN AS "options_allowToControlTariffGroups",
    TRUE::BOOLEAN AS "options_allowViewingUsersWhoAcceptedInvite",

    -- Corporate billing ENABLED
    TRUE::BOOLEAN AS "corporateBilling_enabled",
    NULL::NUMERIC AS "corporateBilling_monthlyLimit",
    NULL::NUMERIC AS "corporateBilling_discount",

    -- No external ID for billing partners
    NULL::TEXT AS "externalId",

    -- Bank details (empty for billing partners)
    NULL::TEXT AS "bankDetails_bankIban",
    NULL::TEXT AS "bankDetails_bankName",
    NULL::TEXT AS "bankDetails_bankAddress",
    NULL::TEXT AS "bankDetails_bankCode",
    NULL::TEXT AS "bankDetails_bankAccountNumber",
    ''::TEXT AS "bankDetails_bankAccountType"

FROM "Source"."BillingAccounts" ba
LEFT JOIN "Mapping"."billing_partner_mapping" bpm
    ON bpm.mapping_key = 'Sleet|BillingPartner|' || ba.account_owner_guid

ORDER BY
    mapping_table,
    mapping_key;
