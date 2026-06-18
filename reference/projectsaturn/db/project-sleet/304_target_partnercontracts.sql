SET ROLE db_sleetmigration_owner;

-- Target.PartnerContracts view for Project Sleet
-- One partner contract per location, linked via project_code to get target_partner_id
-- Uses COALESCE for location_name: prefer LocationMapping override, fallback to Source.Locations
DROP VIEW IF EXISTS "Target"."PartnerContracts";

CREATE OR REPLACE VIEW "Target"."PartnerContracts" AS
SELECT 
    -- Mapping columns (no merge for partner contracts - one per source location)
    'location_mapping'::TEXT AS mapping_table,
    lm.mapping_key,
    NULL::TEXT AS merge_with_mapping_key,
    
    -- Source identifiers for debugging
    SUBSTRING(lm.mapping_key FROM 'Sleet\\|Location\\|(.*)') AS source_location_guid,
    
    -- Target IDs
    pcm.target_partner_id AS "TargetPartnerID",
    lm.target_partner_contract_id AS "TargetPartnerContractID",
    
    -- Contract dates
    CURRENT_DATE AS "startDate",
    NULL::DATE AS "endDate",
    
    -- Contract title: "{project_code} - {location_name} ({price}kr/evse {operator_share}%)"
    lm.project_code || ' - ' || COALESCE(lm.location_name, loc.location_name) || ' (' || 
        COALESCE(lm.partner_contract_price_per_evse, 0)::TEXT || 'kr/evse ' || 
        COALESCE(lm.partner_contract_operator_share_pct, 0)::TEXT || '%)' AS "title",
    
    -- Partner reference
    pcm.target_partner_id AS "partnerId",
    
    -- Auto renewal
    TRUE AS "autoRenewal",
    
    -- Contract type: CO-MDU -> paymentFacilitation, otherwise revenueSharing
    CASE WHEN lm.partner_model = 'CO-MDU' THEN 'paymentFacilitation' ELSE 'revenueSharing' END AS "contractType",

    -- Access and permissions (all enabled)
    TRUE AS "accessAndPermissions_sessionsRemoteControl",
    TRUE AS "accessAndPermissions_startReservation",
    TRUE AS "accessAndPermissions_stopReservation",
    TRUE AS "accessAndPermissions_resetChargePoint",
    TRUE AS "accessAndPermissions_firmwareUpdate",
    
    -- Revenue sharing
    -- paymentFacilitation: partner always gets 100%, operator takes a handling fee instead
    -- revenueSharing: partner gets their negotiated share percentage
    CASE WHEN lm.partner_model = 'CO-MDU' THEN 100
         ELSE COALESCE(lm.partner_contract_partner_share_pct, 0)
    END AS "revenueSharing_partnerSharePercentageAcEvse",
    CASE WHEN lm.partner_model = 'CO-MDU' THEN 100
         ELSE COALESCE(lm.partner_contract_partner_share_pct, 0)
    END AS "revenueSharing_partnerSharePercentageDcEvse",
    FALSE AS "revenueSharing_excludeConnectionFee",
    FALSE AS "revenueSharing_deductElectricityCost",
    FALSE AS "revenueSharing_reimburseForElectricityCost",
    NULL::NUMERIC AS "revenueSharing_fixedFeePerSessionAc",
    NULL::NUMERIC AS "revenueSharing_fixedFeePerSessionDc",
    NULL::NUMERIC AS "revenueSharing_feePerKwhAc",
    NULL::NUMERIC AS "revenueSharing_feePerKwhDc",
    -- paymentFacilitation: handling fee = operator share % / 1.25 (strips 25% MVA)
    -- revenueSharing: no handling fee
    CASE WHEN lm.partner_model = 'CO-MDU'
         THEN COALESCE(lm.partner_contract_operator_share_pct, 0) / 1.25
         ELSE NULL
    END::NUMERIC AS "revenueSharing_handlingFee",
    
    -- Monthly platform fees
    0 AS "monthlyPlatformFees_perChargePoint",
    COALESCE(lm.partner_contract_price_per_evse, 0) AS "monthlyPlatformFees_perAcEvse",
    COALESCE(lm.partner_contract_price_per_evse, 0) AS "monthlyPlatformFees_perDcEvse"

FROM "Mapping"."location_mapping" lm
JOIN "Mapping"."project_code_mapping" pcm ON pcm.project_code = lm.project_code
JOIN "Source"."Locations" loc ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
WHERE lm.migrate = TRUE
  AND pcm.target_partner_id IS NOT NULL;
