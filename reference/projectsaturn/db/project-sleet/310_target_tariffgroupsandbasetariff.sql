SET ROLE db_sleetmigration_owner;

-- Target.TariffGroupsAndBaseTariff view for Project Sleet
-- One tariff group per organisation × pricing profile (grouping_key × tariff_group_hash)
-- Uses tariff_group_mapping for key-based pattern
-- Joins MasterPartnerResolution for org-level master partner ID
DROP VIEW IF EXISTS "Target"."TariffGroupsAndBaseTariff";

CREATE OR REPLACE VIEW "Target"."TariffGroupsAndBaseTariff" AS
WITH representative AS (
    -- Pick one representative EVSE per tariff group (lowest location_nr among migrating locations)
    -- All EVSEs sharing a tariff_group_key have identical pricing by construction of the hash
    SELECT DISTINCT ON (etga.tariff_group_key)
        etga.tariff_group_key,
        etga.mapping_key,
        etga.grouping_key,
        etga.location_nr,
        etga.location_guid,
        etga.location_mapping_key,
        etga.tariff_group_location_count
    FROM "Source"."EvseTariffGroupAssignment" etga
    JOIN "Mapping"."location_mapping" lm
        ON lm.mapping_key = etga.location_mapping_key
    WHERE lm.migrate = TRUE
    ORDER BY etga.tariff_group_key, etga.location_nr ASC
)
SELECT 
    -- ========================================
    -- Mapping columns (key-based pattern)
    -- ========================================
    'tariff_group_mapping'::TEXT AS mapping_table,
    rep.mapping_key,
    NULL::TEXT AS merge_with_mapping_key,
    
    -- ========================================
    -- Target IDs (from mapping tables)
    -- ========================================
    tgm.target_tariff_group_id AS "TargetTariffGroupID",
    tgm.target_tariff_base_id AS "TargetTariffBaseID",
    mpr.master_target_partner_id AS "TargetPartnerID",
    
    -- ========================================
    -- API Payload: Tariff Group
    -- ========================================
    -- Name prefix: single-location uses project_code + location name,
    -- multi-location uses org-level partner name
    CASE
        WHEN rep.tariff_group_location_count = 1
        THEN mpr.project_code || ' - ' || mpr.location_name
        ELSE mpr.master_partner_name
    END AS "tariffGroup_name",
    mpr.master_target_partner_id AS "tariffGroup_partnerId",
    
    -- ========================================
    -- API Payload: Base Tariff
    -- ========================================
    'charging not allowed'::TEXT AS "basetariff_type",
    CASE
        WHEN rep.tariff_group_location_count = 1
        THEN mpr.project_code || ' - ' || mpr.location_name || ': Base tariff (Lading ikke tillatt)'
        ELSE mpr.master_partner_name || ': Base tariff (Lading ikke tillatt)'
    END AS "basetariff_name",
    
    -- Description (localized)
    '<div>Remember to check parking rules and fees.</div>'::TEXT AS "basetariff_description_en",
    '<div>Ikke glem å sjekke parkeringsavgifter og regler.</div>'::TEXT AS "basetariff_description_nb-NO",
    
    -- Additional Information (localized)
    '<div></div>'::TEXT AS "basetariff_additionalInformation_en",
    '<div></div>'::TEXT AS "basetariff_additionalInformation_nb-NO",
    
    -- Pricing (all NULL for "charging not allowed" type)
    NULL::NUMERIC AS "basetariff_pricing_connectionFee",
    NULL::NUMERIC AS "basetariff_pricing_pricePerKwh",
    NULL::INTEGER AS "basetariff_pricing_pricePeriodInMinutes",
    NULL::NUMERIC AS "basetariff_pricing_pricePerPeriod",
    NULL::NUMERIC AS "basetariff_pricing_idleFeePerMinute",
    NULL::INTEGER AS "basetariff_pricing_idleFeeGracePeriodMinutes",
    NULL::INTEGER AS "basetariff_pricing_connectionFeeMinimumSessionDuration",
    NULL::NUMERIC AS "basetariff_pricing_connectionFeeMinimumSessionEnergy",
    NULL::INTEGER AS "basetariff_pricing_durationFeeGracePeriod",
    NULL::NUMERIC AS "basetariff_pricing_minPrice",
    NULL::NUMERIC AS "basetariff_pricing_preAuthorizeAmount",
    NULL::INTEGER AS "basetariff_pricing_taxID",
    
    -- Partner for base tariff
    mpr.master_target_partner_id AS "basetariff_partner_id"

FROM representative rep
LEFT JOIN "Mapping"."tariff_group_mapping" tgm
    ON tgm.mapping_key = rep.mapping_key
JOIN "Mapping"."MasterPartnerResolution" mpr
    ON mpr.mapping_key = rep.location_mapping_key
WHERE mpr.master_target_partner_id IS NOT NULL;
