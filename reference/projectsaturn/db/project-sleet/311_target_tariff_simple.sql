SET ROLE db_sleetmigration_owner;

-- Target.Tariff_Simple view for Project Sleet
-- One row per tariff (standard/ad-hoc/roaming/usergroup) per tariff group
-- Tariff groups are scoped per organisation × pricing profile (grouping_key × tariff_group_hash)
-- Pricing is VAT-inclusive (ex-VAT × vat_multiplier from source)
--
-- Tariff types:
--   duration+energy  — Fixed-price tariffs (standard, ad-hoc, roaming, usergroup with price > 0)
--   energy tou       — Spot-price tariffs (uses fallbackElectricityRateId + markup)
--   free             — User group tariffs with price = 0
--
-- Restriction rules:
--   Standard  (order 1):  No restrictions (all false/null)
--   Ad-hoc   (order 2):  applyToAdHocUsers=true, pre-auth 300/1
--   Roaming  (order 2R): applyToUsersOfAllRoamingEmsps=true
--   UserGroup (order 3+): applyToUserGroupIds = target user group ID
DROP VIEW IF EXISTS "Target"."Tariff_Simple";

CREATE OR REPLACE VIEW "Target"."Tariff_Simple" AS
WITH tariff_rows AS (
    -- Deduplicate EvseTariffRows to tariff-group grain (one row per tariff_group_key × tariff_order)
    -- All EVSEs sharing a tariff_group_key have identical pricing by construction of the hash
    SELECT DISTINCT ON (etr.tariff_group_key, etr.tariff_order)
        etr.tariff_group_key,
        etr.tariff_order,
        etr.tariff_kind,
        etr.tariff_group_mapping_key,
        etr.tariff_mapping_key,
        etr.is_spot_price,
        etr.price_per_kwh_ex_vat,
        etr.spot_markup_ex_vat,
        etr.vat_multiplier,
        etr."taxID",
        etr."fallbackElectricityRateId",
        etr.charger_user_group_guid,
        etr.charger_user_group_name,
        etr.product,
        etr.location_nr,
        etr.location_mapping_key,
        etr.tariff_group_location_count
    FROM "Source"."EvseTariffRows" etr
    JOIN "Mapping"."location_mapping" lm
        ON lm.mapping_key = etr.location_mapping_key
    WHERE lm.migrate = TRUE
    ORDER BY etr.tariff_group_key, etr.tariff_order, etr.location_nr ASC
),
typed_tariffs AS (
    -- Derive tariff type and pre-compute VAT-inclusive prices for naming
    SELECT
        tr.*,
        CASE
            WHEN tr.is_spot_price THEN 'energy tou'
            WHEN tr.tariff_kind = 'usergroup' AND COALESCE(tr.price_per_kwh_ex_vat, 0) = 0 THEN 'free'
            ELSE 'duration+energy'
        END AS tariff_type,
        ROUND(COALESCE(tr.price_per_kwh_ex_vat, 0) * tr.vat_multiplier, 2) AS price_per_kwh_incl_vat,
        ROUND(COALESCE(tr.spot_markup_ex_vat, 0) * tr.vat_multiplier, 2) AS spot_markup_incl_vat
    FROM tariff_rows tr
),
expanded_tariffs AS (
    -- Non-adhoc tariffs pass through unchanged
    -- User group tariffs are excluded when their user group has no mapping
    -- (no entry in user_group_mapping = restrictions cannot be applied correctly)
    SELECT
        tt.*,
        tt.tariff_mapping_key AS effective_mapping_key,
        tt.tariff_kind AS effective_kind
    FROM typed_tariffs tt
    LEFT JOIN "Mapping"."user_group_mapping" ugm
        ON tt.tariff_kind = 'usergroup'
        AND ugm.mapping_key = 'Sleet|UserGroup|' || LOWER(tt.charger_user_group_guid)
    WHERE tt.tariff_kind != 'adhoc'
      -- Exclude usergroup tariffs with no target user group mapping
      AND NOT (tt.tariff_kind = 'usergroup' AND (ugm.mapping_key IS NULL OR ugm.target_user_group_id IS NULL))

    UNION ALL

    -- Ad-hoc tariff (keeps original mapping key with |2 suffix)
    SELECT
        tt.*,
        tt.tariff_mapping_key AS effective_mapping_key,
        'adhoc'::TEXT AS effective_kind
    FROM typed_tariffs tt
    WHERE tt.tariff_kind = 'adhoc'

    UNION ALL

    -- Roaming tariff (clone of adhoc with |2R suffix mapping key)
    SELECT
        tt.*,
        'Sleet|Tariff|' || tt.tariff_group_key || '|2R' AS effective_mapping_key,
        'roaming'::TEXT AS effective_kind
    FROM typed_tariffs tt
    WHERE tt.tariff_kind = 'adhoc'
)
SELECT
    -- ========================================
    -- Mapping columns (key-based pattern)
    -- ========================================
    'tariff_mapping'::TEXT AS mapping_table,
    et.effective_mapping_key AS mapping_key,
    NULL::TEXT AS merge_with_mapping_key,

    -- ========================================
    -- Target IDs (from mapping tables)
    -- ========================================
    tgm.target_tariff_group_id AS "TargetTariffGroupID",
    tgm.target_tariff_base_id AS "TargetTariffBaseID",
    tm.target_tariff_id AS "TargetTariffID",

    -- ========================================
    -- API Payload: Tariff
    -- ========================================
    et.tariff_type AS "type",

    -- Name: {prefix}{suffix} {price_text}
    -- Prefix: single-location = "W047xxx - LocationName", multi-location = partner name
    -- Suffix: standard = none, adhoc = ": Ad-hoc", roaming = ": Roaming", usergroup = ": {UG name}"
    -- Price text: "(X.XX kr/kWh)", "(Gratis)", "(Spot + X.XX kr/kWh)"
    CASE
        WHEN et.tariff_group_location_count = 1
        THEN mpr.project_code || ' - ' || mpr.location_name
        ELSE mpr.master_partner_name
    END
    || CASE et.effective_kind
        WHEN 'standard' THEN ''
        WHEN 'adhoc'    THEN ': Ad-hoc'
        WHEN 'roaming'  THEN ': Roaming'
        WHEN 'usergroup' THEN ': ' || et.charger_user_group_name
    END
    || ' ' ||
    CASE
        WHEN et.tariff_type = 'free' THEN '(Gratis)'
        WHEN et.tariff_type = 'duration+energy' AND et.price_per_kwh_incl_vat = 0 THEN '(Gratis)'
        WHEN et.is_spot_price THEN '(Spot + ' || to_char(et.spot_markup_incl_vat, 'FM990.00') || ' kr/kWh)'
        ELSE '(' || to_char(et.price_per_kwh_incl_vat, 'FM990.00') || ' kr/kWh)'
    END AS "name",

    -- ========================================
    -- Pricing block (populated for duration+energy and energy tou; NULL for free)
    -- ========================================
    CASE WHEN et.tariff_type = 'duration+energy' THEN 0::NUMERIC
        ELSE NULL::NUMERIC END AS "pricing_connectionFee",
    CASE WHEN et.tariff_type = 'duration+energy'
        THEN ROUND(COALESCE(et.price_per_kwh_ex_vat, 0) * et.vat_multiplier, 2)
        ELSE NULL::NUMERIC END AS "pricing_pricePerKwh",
    CASE WHEN et.tariff_type = 'duration+energy' THEN 1::INTEGER
        ELSE NULL::INTEGER END AS "pricing_pricePeriodInMinutes",
    CASE WHEN et.tariff_type = 'duration+energy' THEN 0::NUMERIC
        ELSE NULL::NUMERIC END AS "pricing_pricePerPeriod",
    NULL::NUMERIC AS "pricing_idleFeePerMinute",
    NULL::INTEGER AS "pricing_idleFeeGracePeriodMinutes",
    CASE WHEN et.tariff_type = 'duration+energy' THEN 0::INTEGER
        ELSE NULL::INTEGER END AS "pricing_connectionFeeMinimumSessionDuration",
    CASE WHEN et.tariff_type = 'duration+energy' THEN 0::NUMERIC
        ELSE NULL::NUMERIC END AS "pricing_connectionFeeMinimumSessionEnergy",
    NULL::INTEGER AS "pricing_durationFeeGracePeriod",
    CASE WHEN et.tariff_type IN ('duration+energy', 'energy tou') THEN 3.00::NUMERIC
        ELSE NULL::NUMERIC END AS "pricing_minPrice",
    NULL::NUMERIC AS "pricing_preAuthorizeAmount",
    et."taxID" AS "pricing_taxID",

    -- ========================================
    -- Partner
    -- ========================================
    mpr.master_target_partner_id AS "partner_id",

    -- ========================================
    -- Restrictions
    -- ========================================
    -- User group tariffs: apply to specific user group
    CASE WHEN et.effective_kind = 'usergroup'
        THEN ugm.target_user_group_id
        ELSE NULL::INTEGER
    END AS "restrictions_applyToUserGroupIds",

    -- Always false — no tariff applies to CP partner users
    FALSE AS "restrictions_applyToUsersOfChargePointPartner",

    -- Roaming: apply to all roaming EMSPs
    CASE WHEN et.effective_kind = 'roaming' THEN TRUE ELSE FALSE END
        AS "restrictions_applyToUsersOfAllRoamingEmsps",

    -- Ad-hoc only
    CASE WHEN et.effective_kind = 'adhoc' THEN TRUE ELSE FALSE END
        AS "restrictions_applyToAdHocUsers",

    -- Ad-hoc pre-auth amounts (NULL for non-adhoc)
    CASE WHEN et.effective_kind = 'adhoc' THEN 300::NUMERIC
        ELSE NULL::NUMERIC END AS "restrictions_adHocPreAuthorizeAmount",
    CASE WHEN et.effective_kind = 'adhoc' THEN 1::NUMERIC
        ELSE NULL::NUMERIC END AS "restrictions_adHocStopWhenPreAuthorizedAmountFallsBelow",

    -- ========================================
    -- Spot-specific columns (for energy tou type)
    -- ========================================
    CASE WHEN et.is_spot_price THEN FALSE ELSE NULL::BOOLEAN END AS "pricing_chargePointElectricityRate",
    et."fallbackElectricityRateId" AS "pricing_fallbackElectricityRateId",
    CASE WHEN et.is_spot_price
        THEN ROUND(COALESCE(et.spot_markup_ex_vat, 0) * et.vat_multiplier, 2)
        ELSE NULL::NUMERIC
    END AS "pricing_markupFixedFeePerKwh",
    CASE WHEN et.is_spot_price THEN 0::NUMERIC ELSE NULL::NUMERIC END AS "pricing_markupPercentagePerKwh",

    -- ========================================
    -- Description (localized)
    -- ========================================
    '<div>Remember to check parking rules and fees.</div>'::TEXT AS "description_en",
    '<div>Ikke glem å sjekke parkeringsavgifter og regler.</div>'::TEXT AS "description_nb-NO",

    -- ========================================
    -- Additional Information (localized)
    -- ========================================
    '<div></div>'::TEXT AS "additionalInformation_en",
    '<div></div>'::TEXT AS "additionalInformation_nb-NO"

FROM expanded_tariffs et
LEFT JOIN "Mapping"."tariff_group_mapping" tgm
    ON tgm.mapping_key = et.tariff_group_mapping_key
LEFT JOIN "Mapping"."tariff_mapping" tm
    ON tm.mapping_key = et.effective_mapping_key
JOIN "Mapping"."MasterPartnerResolution" mpr
    ON mpr.mapping_key = et.location_mapping_key
LEFT JOIN "Mapping"."user_group_mapping" ugm
    ON et.effective_kind = 'usergroup'
    AND ugm.mapping_key = 'Sleet|UserGroup|' || LOWER(et.charger_user_group_guid)
WHERE mpr.master_target_partner_id IS NOT NULL;
