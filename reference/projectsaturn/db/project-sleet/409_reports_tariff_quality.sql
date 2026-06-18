-- ============================================================================
-- QUALITY CHECK VIEW: TariffQuality
-- ============================================================================
-- Reports data quality issues that may affect tariff/pricing migration.
-- Scope: Entities joined to a Location where exclude = FALSE (includes both
--        migrate and non-migrate locations; excludes only explicitly excluded).
--
-- Classification:
--   INFO    - Data is informational, auto-corrected, or expected given context
--   WARNING - Data may be corrupt/malformed, using fallback values — verify
--   ERROR   - Major problems requiring attention before migration
--
-- Severity Downgrade:
--   For non-migrating locations (migrate = FALSE), all ERROR/WARNING
--   classifications are downgraded to INFO, since the tariff data has no
--   practical effect on non-migrating entities in the target CSMS.
--
-- Issue Types - Pricing Resolution Flags (from Source.EvseTariffRows):
--   has_missing_tariff_items                [ERROR]   - Price list has no matching item for product
--   has_multiple_public_rules               [WARNING] - Multiple public rules, deterministic tie-break used
--   has_adhoc_from_sms                      [INFO]    - Ad-hoc price overridden by SMS rule
--   has_sms_price_lower_ignored             [WARNING] - SMS rule ignored (price lower than standard)
--   has_sms_override_skipped_spot           [INFO]    - SMS override skipped due to spot pricing
--   has_usergroup_price_ambiguity           [WARNING] - Multiple rules for user group, highest picked
--   has_fallback_location                   [INFO]    - Using location's fallback price list
--   has_fallback_default                    [WARNING] - Using system default price list
--
-- Issue Types - Structural Issues (direct source and mapping table queries):
--   has_missing_user_group_mapping          [ERROR]   - User group tariff cannot apply restrictions
--   has_no_charger_groups                   [WARNING] - Charger has no charger groups, using fallback
--   has_inconsistent_pricing_in_location    [INFO]    - Multiple tariff groups at same location
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Reports"."TariffQuality";

CREATE OR REPLACE VIEW "Reports"."TariffQuality" AS

-- ============================================================================
-- Helper CTE: Deduplicated tariff flag rows
-- Splits comma-separated flags from EvseTariffRows into individual rows,
-- deduplicated to one row per (location × tariff_group × tariff_order × flag).
-- Flags are computed exclusively in 204_source_evse_pricing.sql — no business
-- rules are duplicated here. This view only classifies and formats them.
-- ============================================================================
WITH tariff_flag_issues AS (
    SELECT DISTINCT
        etr.location_nr,
        etr.tariff_group_key,
        etr.tariff_order,
        etr.tariff_kind,
        etr.product,
        etr.tariff_mapping_key,
        etr.source_price_list_name,
        etr.location_mapping_key,
        btrim(flag_value) AS flag_value
    FROM "Source"."EvseTariffRows" etr
    JOIN "Mapping"."location_mapping" lm ON lm.mapping_key = etr.location_mapping_key
    CROSS JOIN UNNEST(STRING_TO_ARRAY(etr.flags, ',')) AS flag_value
    WHERE lm.exclude = FALSE
      AND etr.flags IS NOT NULL
      AND btrim(etr.flags) != ''
      AND btrim(flag_value) != ''
)

-- ============================================================================
-- SECTION A: PRICING RESOLUTION FLAGS
-- Source: Quality flags from Source.EvseTariffRows (204)
-- One row per (location × tariff_group_key × tariff_order × flag_type).
-- The classification logic reads flags that were already computed by the
-- pricing transform in 204 — no business rules are duplicated here.
-- ============================================================================

SELECT
    lm.project_code,
    COALESCE(lm.location_name, loc.location_name)::TEXT AS location_name,
    lm.migrate,
    lm.status,
    -- Classification with severity downgrade for non-migrating locations
    CASE
        WHEN NOT lm.migrate THEN 'INFO'
        WHEN tfi.flag_value = 'MISSING_TARIFF_ITEMS' THEN 'ERROR'
        WHEN tfi.flag_value IN (
            'MULTIPLE_PUBLIC_RULES_RESOLVED',
            'SMS_PRICE_LOWER_THAN_STANDARD_IGNORED',
            'MULTIPLE_USERGROUP_RULES_PRICE_AMBIGUOUS',
            'PUBLIC_FROM_DEFAULT_FALLBACK'
        ) THEN 'WARNING'
        ELSE 'INFO'
    END::TEXT AS classification,
    'Tariff'::TEXT AS entity_type,
    tfi.tariff_mapping_key::TEXT AS entity_id,
    tfi.tariff_kind || ' (' || tfi.product || ')' AS entity_name,
    -- Map raw flag string to snake_case issue_type
    CASE tfi.flag_value
        WHEN 'MISSING_TARIFF_ITEMS'                     THEN 'has_missing_tariff_items'
        WHEN 'MULTIPLE_PUBLIC_RULES_RESOLVED'           THEN 'has_multiple_public_rules'
        WHEN 'ADHOC_FROM_SMS'                           THEN 'has_adhoc_from_sms'
        WHEN 'SMS_PRICE_LOWER_THAN_STANDARD_IGNORED'    THEN 'has_sms_price_lower_ignored'
        WHEN 'SMS_OVERRIDE_SKIPPED_DUE_TO_SPOT'         THEN 'has_sms_override_skipped_spot'
        WHEN 'MULTIPLE_USERGROUP_RULES_PRICE_AMBIGUOUS' THEN 'has_usergroup_price_ambiguity'
        WHEN 'PUBLIC_FROM_LOCATION_FALLBACK'            THEN 'has_fallback_location'
        WHEN 'PUBLIC_FROM_DEFAULT_FALLBACK'             THEN 'has_fallback_default'
        ELSE 'unknown_flag'
    END::TEXT AS issue_type,
    -- Human-readable explanation for each flag
    CASE tfi.flag_value
        WHEN 'MISSING_TARIFF_ITEMS'
            THEN 'Price list has no matching item for product - tariff cannot derive pricing data'
        WHEN 'MULTIPLE_PUBLIC_RULES_RESOLVED'
            THEN 'Multiple public price rules applied to EVSE - deterministic tie-break selected one'
        WHEN 'ADHOC_FROM_SMS'
            THEN 'Ad-hoc price overridden by SMS rule with higher price than standard'
        WHEN 'SMS_PRICE_LOWER_THAN_STANDARD_IGNORED'
            THEN 'SMS rule exists but price is lower than standard - ignored per algorithm'
        WHEN 'SMS_OVERRIDE_SKIPPED_DUE_TO_SPOT'
            THEN 'SMS override skipped because standard or SMS pricing uses spot'
        WHEN 'MULTIPLE_USERGROUP_RULES_PRICE_AMBIGUOUS'
            THEN 'Multiple rules for same user group with different prices - highest price selected'
        WHEN 'PUBLIC_FROM_LOCATION_FALLBACK'
            THEN 'No public price rules found for EVSE - using location fallback price list'
        WHEN 'PUBLIC_FROM_DEFAULT_FALLBACK'
            THEN 'No public rules and no location fallback - using system default price list'
        ELSE 'Unknown quality flag: ' || tfi.flag_value
    END::TEXT AS issue_reason,
    ('tariff_group_key=' || tfi.tariff_group_key
     || ', tariff_order=' || tfi.tariff_order
     || ', product=' || tfi.product
     || ', price_list=' || COALESCE(tfi.source_price_list_name, '[NULL]'))::TEXT AS referenced_value,
    tfi.location_nr
FROM tariff_flag_issues tfi
JOIN "Source"."Locations" loc ON loc.location_nr = tfi.location_nr
JOIN "Mapping"."location_mapping" lm ON lm.mapping_key = tfi.location_mapping_key

UNION ALL

-- ============================================================================
-- SECTION B: STRUCTURAL ISSUES
-- Source: Direct queries against Source and Mapping tables
-- ============================================================================

-- has_missing_user_group_mapping: ERROR (user group tariff has no target mapping, INFO if !migrate)
-- User group tariffs require a target user group ID to apply restrictions.
-- Without a mapping, the tariff will be created but restrictions cannot be set.
-- Note: Target.Tariff_Simple (311) excludes these tariffs to prevent creating
-- tariffs with broken restrictions. This report flags them for manual review.
SELECT DISTINCT
    lm.project_code,
    COALESCE(lm.location_name, loc.location_name)::TEXT AS location_name,
    lm.migrate,
    lm.status,
    CASE WHEN lm.migrate THEN 'ERROR' ELSE 'INFO' END::TEXT AS classification,
    'Tariff'::TEXT AS entity_type,
    etr.tariff_mapping_key::TEXT AS entity_id,
    etr.tariff_kind || ' (' || etr.product || '): ' || COALESCE(etr.charger_user_group_name, '[unnamed]') AS entity_name,
    'has_missing_user_group_mapping'::TEXT AS issue_type,
    'User group tariff references user group with no target mapping - tariff excluded from Target.Tariff_Simple'::TEXT AS issue_reason,
    ('charger_user_group_guid=' || COALESCE(etr.charger_user_group_guid, '[NULL]')
     || ', charger_user_group_name=' || COALESCE(etr.charger_user_group_name, '[NULL]'))::TEXT AS referenced_value,
    etr.location_nr
FROM "Source"."EvseTariffRows" etr
JOIN "Source"."Locations" loc ON loc.location_nr = etr.location_nr
JOIN "Mapping"."location_mapping" lm ON lm.mapping_key = etr.location_mapping_key
LEFT JOIN "Mapping"."user_group_mapping" ugm
    ON ugm.mapping_key = 'Sleet|UserGroup|' || LOWER(etr.charger_user_group_guid)
WHERE lm.exclude = FALSE
  AND etr.tariff_kind = 'usergroup'
  AND (ugm.mapping_key IS NULL OR ugm.target_user_group_id IS NULL)

UNION ALL

-- has_no_charger_groups: WARNING (charger has no charger group assignments, INFO if !migrate)
-- Without charger groups, the EVSE cannot match any price rules and falls back
-- to the location or system default price list.
SELECT DISTINCT
    lm.project_code,
    COALESCE(lm.location_name, loc.location_name)::TEXT AS location_name,
    lm.migrate,
    lm.status,
    CASE WHEN lm.migrate THEN 'WARNING' ELSE 'INFO' END::TEXT AS classification,
    'Charger'::TEXT AS entity_type,
    chr."Id"::TEXT AS entity_id,
    chr.charger_name AS entity_name,
    'has_no_charger_groups'::TEXT AS issue_type,
    'Charger has no charger group assignments - EVSE pricing derived from location/default fallback'::TEXT AS issue_reason,
    ('[no charger groups]')::TEXT AS referenced_value,
    etga.location_nr
FROM "Source"."EvseTariffGroupAssignment" etga
JOIN "Source"."Chargers" chr ON chr.charger_id = etga.charger_id
JOIN "Source"."Locations" loc ON loc.location_nr = etga.location_nr
JOIN "Mapping"."location_mapping" lm ON lm.mapping_key = etga.location_mapping_key
WHERE lm.exclude = FALSE
  AND NOT EXISTS (
    SELECT 1 FROM "Source"."ChargerToChargerGroup" ctcg
    WHERE ctcg.charger_guid = etga.charger_guid
  )

UNION ALL

-- has_inconsistent_pricing_in_location: INFO (multiple tariff groups at same location)
-- Location has EVSEs with different pricing profiles, resulting in multiple tariff groups.
-- This is expected when AC and DC connectors coexist or when different products have
-- different pricing, but may indicate data issues worth verifying.
SELECT
    lm.project_code,
    COALESCE(lm.location_name, loc.location_name)::TEXT AS location_name,
    lm.migrate,
    lm.status,
    'INFO'::TEXT AS classification,
    'Location'::TEXT AS entity_type,
    lm.mapping_key::TEXT AS entity_id,
    COALESCE(lm.location_name, loc.location_name) AS entity_name,
    'has_inconsistent_pricing_in_location'::TEXT AS issue_type,
    'Location has multiple tariff groups - EVSEs have different pricing profiles'::TEXT AS issue_reason,
    ('tariff_group_count=' || loc_groups.group_count)::TEXT AS referenced_value,
    loc.location_nr
FROM (
    SELECT
        etga.location_nr,
        etga.location_mapping_key,
        COUNT(DISTINCT etga.tariff_group_key) AS group_count
    FROM "Source"."EvseTariffGroupAssignment" etga
    JOIN "Mapping"."location_mapping" lm2 ON lm2.mapping_key = etga.location_mapping_key
    WHERE lm2.exclude = FALSE
    GROUP BY etga.location_nr, etga.location_mapping_key
    HAVING COUNT(DISTINCT etga.tariff_group_key) > 1
) loc_groups
JOIN "Source"."Locations" loc ON loc.location_nr = loc_groups.location_nr
JOIN "Mapping"."location_mapping" lm ON lm.mapping_key = loc_groups.location_mapping_key;

-- Verify view was created
SELECT 'TariffQuality view created successfully' AS status;
