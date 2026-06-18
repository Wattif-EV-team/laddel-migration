-- ============================================================================
-- MATERIALIZED TABLES: Source.EvseTariffGroupAssignment + Source.EvseTariffRows
-- ============================================================================
-- Full pricing transformation from source CSMS to target CSMS tariff model.
-- Implements the algorithm in doc/project-sleet/research/final_pricing_transform_algorithm.md
--
-- Materialized as TABLES (not views) for performance. The 12-CTE pricing
-- transform takes ~60s to evaluate. Downstream target views (310, 311, 313)
-- would re-trigger this on every query, making migration scripts unusably slow.
-- Tables are dropped and recreated during each build (same lifecycle as views).
--
-- Two output tables:
--   Source.EvseTariffGroupAssignment  — EVSE grain (one row per in-scope connector)
--   Source.EvseTariffRows             — Tariff grain (one row per EVSE × tariff order)
--
-- CTE architecture:
--   1. evse_with_product        — Scope-filtered connectors with product derivation
--   2. evse_charger_groups      — Charger groups per EVSE
--   3. evse_all_rules           — All price rules per EVSE (deduplicated)
--   4. evse_public_rules        — Public rules with price list item check
--   5. evse_public_selected     — Deterministic tie-break for public rule
--   6. evse_fallback            — Location/default fallback for EVSEs with no public rules
--   7. evse_standard_source     — Standard tariff (order 1) data
--   8. evse_adhoc_source        — Ad-hoc tariff (order 2) data with SMS override
--   9. evse_usergroup_tariffs   — User group tariffs (order 3..N)
--  10. evse_all_tariffs         — UNION ALL of standard + ad-hoc + user group
--  11. evse_signature           — Canonical pricing signature per EVSE
--  12. evse_tariff_group_hash   — MD5 hash + grouping key → tariff group assignment
--
-- Depends on: Source.EvseProduct (203), Mapping.MasterPartnerResolution (202),
--             Source.ChargerToChargerGroup (101), 0xx mapping tables
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Materialized as tables (not views) for performance.
-- The 12-CTE pricing transform is too expensive to re-evaluate on every query.
-- Tables are dropped and recreated during each build (same lifecycle as views).
DROP TABLE IF EXISTS "Source"."EvseTariffRows" CASCADE;
DROP TABLE IF EXISTS "Source"."EvseTariffGroupAssignment" CASCADE;

-- ============================================================================
-- TABLE 1: Source.EvseTariffGroupAssignment
-- One row per in-scope connector with tariff group hash and key
-- ============================================================================
CREATE TABLE "Source"."EvseTariffGroupAssignment" AS
WITH
-- ============================================================================
-- CTE 1: evse_with_product — Scope-filtered connectors
-- ============================================================================
evse_with_product AS (
    SELECT
        ep.connector_guid,
        ep.charger_id,
        ep.connector_level,
        ep.connector_type,
        ep.power_type,
        ep.charger_guid,
        ep.location_nr,
        ep.product,
        loc."Id"          AS location_guid,
        loc.price_list_guid AS location_price_list_guid,
        lm.mapping_key    AS location_mapping_key
    FROM "Source"."EvseProduct" ep
    JOIN "Source"."Locations" loc ON loc.location_nr = ep.location_nr
    LEFT JOIN "Mapping"."location_mapping" lm
        ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
),

-- ============================================================================
-- CTE 2: evse_charger_groups — Charger groups per EVSE
-- One row per (connector, charger_group) combination
-- ============================================================================
evse_charger_groups AS (
    SELECT DISTINCT
        ewp.connector_guid,
        ewp.charger_id,
        ewp.connector_level,
        ewp.charger_guid,
        ewp.location_nr,
        ewp.product,
        ewp.location_guid,
        ewp.location_price_list_guid,
        ewp.location_mapping_key,
        ctcg.charger_group_guid,
        ctcg.charger_to_charger_group_name AS charger_group_name
    FROM evse_with_product ewp
    JOIN "Source"."ChargerToChargerGroup" ctcg
        ON ctcg.charger_guid = ewp.charger_guid
),

-- ============================================================================
-- CTE 3: evse_all_rules — All applicable price rules per EVSE
-- Joins charger groups → PriceToUsersAndChargers → PriceList
-- INNER JOIN to PriceList excludes rules with missing/invalid price_list_guid
-- Deduplicates rules (same rule can be reached via multiple charger groups)
-- ============================================================================
evse_all_rules_raw AS (
    SELECT
        ecg.connector_guid,
        ecg.charger_id,
        ecg.connector_level,
        ecg.charger_guid,
        ecg.location_nr,
        ecg.product,
        ecg.location_guid,
        ecg.location_price_list_guid,
        ecg.location_mapping_key,
        ptuc.price_to_users_and_chargers_guid AS rule_guid,
        ptuc.price_to_users_and_chargers_name AS rule_name,
        ptuc.price_list_guid AS rule_price_list_guid,
        ptuc.all_active_customers,
        ptuc.discount,
        ptuc.show_in_app,
        ptuc.search_in_app,
        ptuc.charger_user_group_guid AS rule_charger_user_group_guid,
        ptuc.charger_user_group_name AS rule_charger_user_group_name,
        ptuc.modified_on AS rule_modified_on,
        ptuc.created_on AS rule_created_on,
        ecg.charger_group_name,
        pl.is_spot_price AS price_list_is_spot,
        -- Classify rule type
        CASE
            WHEN ptuc.all_active_customers = 'Yes' THEN 'public'
            WHEN ptuc.all_active_customers = 'No'  THEN 'usergroup'
            ELSE 'sms'  -- empty string
        END AS rule_type
    FROM evse_charger_groups ecg
    JOIN "Source"."PriceToUsersAndChargers" ptuc
        ON ptuc.charger_group_guid = ecg.charger_group_guid
    -- INNER JOIN ensures rules with missing price lists are excluded
    -- (known edge case: Vegamot 8 AS rule references non-existent price list)
    INNER JOIN "Source"."PriceList" pl
        ON pl."Id" = ptuc.price_list_guid
    -- Exclude roaming price lists
    WHERE (pl.roaming_party_id IS NULL OR btrim(pl.roaming_party_id::TEXT) = '')
),

-- Deduplicate: a rule can be reached via multiple charger groups for the same EVSE
-- Keep track of all contributing charger group names
evse_all_rules AS (
    SELECT DISTINCT ON (connector_guid, rule_guid)
        connector_guid,
        charger_id,
        connector_level,
        charger_guid,
        location_nr,
        product,
        location_guid,
        location_price_list_guid,
        location_mapping_key,
        rule_guid,
        rule_name,
        rule_price_list_guid,
        all_active_customers,
        discount,
        show_in_app,
        search_in_app,
        rule_charger_user_group_guid,
        rule_charger_user_group_name,
        rule_modified_on,
        rule_created_on,
        price_list_is_spot,
        rule_type,
        -- Aggregate charger group names for this rule+EVSE (for naming)
        (SELECT STRING_AGG(DISTINCT sub.charger_group_name, ', ' ORDER BY sub.charger_group_name)
         FROM evse_all_rules_raw sub
         WHERE sub.connector_guid = evse_all_rules_raw.connector_guid
           AND sub.rule_guid = evse_all_rules_raw.rule_guid
        ) AS charger_group_names
    FROM evse_all_rules_raw
    ORDER BY connector_guid, rule_guid
),

-- ============================================================================
-- CTE 4: evse_public_rules — Public rules with price list item check
-- ============================================================================
evse_public_rules AS (
    SELECT
        ear.*,
        -- Check if this rule's price list has a matching item
        CASE WHEN pli.price_list_configuration_guid IS NOT NULL THEN TRUE ELSE FALSE END
            AS has_matching_item,
        -- Count public rules per EVSE for flagging
        COUNT(*) OVER (PARTITION BY ear.connector_guid) AS public_rule_count
    FROM evse_all_rules ear
    LEFT JOIN "Source"."PriceListItems" pli
        ON pli.price_list_guid = ear.rule_price_list_guid
        AND pli.product = ear.product
        AND pli.registered_customer = 'Yes'
    WHERE ear.rule_type = 'public'
),

-- ============================================================================
-- CTE 5: evse_public_selected — Deterministic tie-break for public rule
-- Selects exactly one public rule per EVSE using the algorithm's priority:
-- 1. Has matching price list item (TRUE first)
-- 2. Discount = 'No' preferred
-- 3. Show in APP = 'Yes' preferred
-- 4. Search in APP = 'Yes' preferred
-- 5. Latest modified_on
-- 6. Latest created_on
-- 7. Lowest rule GUID (string compare)
-- ============================================================================
evse_public_selected AS (
    SELECT DISTINCT ON (connector_guid)
        connector_guid,
        charger_id,
        connector_level,
        charger_guid,
        location_nr,
        product,
        location_guid,
        location_price_list_guid,
        location_mapping_key,
        rule_guid AS selected_rule_guid,
        rule_name AS selected_rule_name,
        rule_price_list_guid AS selected_price_list_guid,
        price_list_is_spot AS selected_is_spot,
        charger_group_names AS selected_charger_group_names,
        -- Flag: multiple public rules resolved
        CASE WHEN public_rule_count > 1
             THEN 'MULTIPLE_PUBLIC_RULES_RESOLVED'
             ELSE NULL
        END AS flag_multiple_public
    FROM evse_public_rules
    ORDER BY connector_guid,
        has_matching_item DESC,            -- TRUE first
        (discount = 'No') DESC,            -- Discount=No preferred
        (show_in_app = 'Yes') DESC,        -- Show in APP=Yes preferred
        (search_in_app = 'Yes') DESC,      -- Search in APP=Yes preferred
        rule_modified_on DESC NULLS LAST,  -- Latest modified
        rule_created_on DESC NULLS LAST,   -- Latest created
        rule_guid ASC                      -- Lowest GUID as final tie-break
),

-- ============================================================================
-- CTE 6: evse_fallback — For EVSEs with NO public rules
-- Try location.price_list_guid, else default price list
-- ============================================================================
evse_no_public AS (
    SELECT ewp.*
    FROM evse_with_product ewp
    WHERE NOT EXISTS (
        SELECT 1 FROM evse_public_selected eps
        WHERE eps.connector_guid = ewp.connector_guid
    )
),

evse_fallback AS (
    SELECT
        enp.connector_guid,
        enp.charger_id,
        enp.connector_level,
        enp.charger_guid,
        enp.location_nr,
        enp.product,
        enp.location_guid,
        enp.location_price_list_guid,
        enp.location_mapping_key,
        -- Resolve fallback price list: location first, then default
        COALESCE(
            loc_pl."Id",
            def_pl."Id"
        ) AS fallback_price_list_guid,
        COALESCE(
            loc_pl.is_spot_price,
            def_pl.is_spot_price
        ) AS fallback_is_spot,
        -- Flags
        CASE
            WHEN loc_pl."Id" IS NOT NULL THEN 'PUBLIC_FROM_LOCATION_FALLBACK'
            WHEN def_pl."Id" IS NOT NULL THEN 'PUBLIC_FROM_DEFAULT_FALLBACK'
            ELSE NULL
        END AS flag_fallback
    FROM evse_no_public enp
    -- Try location's price_list_guid
    LEFT JOIN "Source"."PriceList" loc_pl
        ON loc_pl."Id" = NULLIF(btrim(enp.location_price_list_guid), '')::uuid
    -- Default price list (Standard NO)
    LEFT JOIN "Source"."PriceList" def_pl
        ON def_pl.default_price_list = 'Yes'
        AND loc_pl."Id" IS NULL  -- only use default if location fallback failed
),

-- ============================================================================
-- CTE 7: evse_standard_source — Standard tariff data (TariffOrder = 1)
-- Merges selected public rule and fallback paths
-- ============================================================================
evse_standard_resolved AS (
    -- Path A: EVSEs with a selected public rule
    SELECT
        eps.connector_guid,
        eps.charger_id,
        eps.connector_level,
        eps.charger_guid,
        eps.location_nr,
        eps.product,
        eps.location_guid,
        eps.location_mapping_key,
        eps.selected_price_list_guid AS resolved_price_list_guid,
        eps.selected_is_spot AS resolved_is_spot,
        eps.selected_rule_guid,
        eps.selected_rule_name,
        eps.selected_charger_group_names,
        eps.flag_multiple_public AS flag
    FROM evse_public_selected eps
    UNION ALL
    -- Path B: EVSEs using fallback
    SELECT
        ef.connector_guid,
        ef.charger_id,
        ef.connector_level,
        ef.charger_guid,
        ef.location_nr,
        ef.product,
        ef.location_guid,
        ef.location_mapping_key,
        ef.fallback_price_list_guid AS resolved_price_list_guid,
        ef.fallback_is_spot AS resolved_is_spot,
        NULL::uuid AS selected_rule_guid,
        NULL::TEXT AS selected_rule_name,
        NULL::TEXT AS selected_charger_group_names,
        ef.flag_fallback AS flag
    FROM evse_fallback ef
),

evse_standard_source AS (
    SELECT
        esr.connector_guid,
        esr.charger_id,
        esr.connector_level,
        esr.charger_guid,
        esr.location_nr,
        esr.product,
        esr.location_guid,
        esr.location_mapping_key,
        esr.resolved_price_list_guid,
        esr.resolved_is_spot,
        esr.selected_rule_guid,
        esr.selected_rule_name,
        esr.selected_charger_group_names,
        pl.price_list_name AS source_price_list_name,
        -- Pricing from the matched item
        CASE
            WHEN esr.resolved_is_spot = 'Yes' THEN NULL
            WHEN pli.price_list_configuration_guid IS NULL THEN NULL  -- MISSING_TARIFF_ITEMS
            ELSE COALESCE(NULLIF(pli.price_per_kwh_ex_vat, '')::NUMERIC, 0)
        END AS standard_price_per_kwh_ex_vat,
        CASE
            WHEN esr.resolved_is_spot = 'Yes' AND pli.price_list_configuration_guid IS NOT NULL
                THEN COALESCE(NULLIF(pli.addon_spot_price, '')::NUMERIC, 0)
            ELSE NULL
        END AS standard_spot_markup_ex_vat,
        (esr.resolved_is_spot = 'Yes') AS standard_is_spot,
        -- Flags
        CASE
            WHEN esr.flag IS NOT NULL AND pli.price_list_configuration_guid IS NULL
                THEN esr.flag || ',MISSING_TARIFF_ITEMS'
            WHEN pli.price_list_configuration_guid IS NULL
                THEN 'MISSING_TARIFF_ITEMS'
            ELSE esr.flag
        END AS standard_flags
    FROM evse_standard_resolved esr
    LEFT JOIN "Source"."PriceList" pl
        ON pl."Id" = esr.resolved_price_list_guid
    LEFT JOIN "Source"."PriceListItems" pli
        ON pli.price_list_guid = esr.resolved_price_list_guid
        AND pli.product = esr.product
        AND pli.registered_customer = 'Yes'
),

-- ============================================================================
-- CTE 8: evse_adhoc_source — Ad-hoc tariff data (TariffOrder = 2)
-- Default: same price list as standard, registered_customer='No'
-- SMS override: only when both fixed (not spot) and sms_price >= standard_price
-- ============================================================================
evse_adhoc_default AS (
    SELECT
        ess.connector_guid,
        ess.resolved_price_list_guid,
        ess.resolved_is_spot,
        ess.standard_price_per_kwh_ex_vat,
        ess.standard_is_spot,
        -- Ad-hoc item from same price list
        CASE
            WHEN ess.resolved_is_spot = 'Yes' THEN NULL
            WHEN pli_adhoc.price_list_configuration_guid IS NULL THEN NULL
            ELSE COALESCE(NULLIF(pli_adhoc.price_per_kwh_ex_vat, '')::NUMERIC, 0)
        END AS adhoc_default_price,
        CASE
            WHEN ess.resolved_is_spot = 'Yes' AND pli_adhoc.price_list_configuration_guid IS NOT NULL
                THEN COALESCE(NULLIF(pli_adhoc.addon_spot_price, '')::NUMERIC, 0)
            ELSE NULL
        END AS adhoc_default_spot_markup,
        pli_adhoc.price_list_guid AS adhoc_default_price_list_guid,
        pl_adhoc.price_list_name AS adhoc_default_price_list_name
    FROM evse_standard_source ess
    LEFT JOIN "Source"."PriceListItems" pli_adhoc
        ON pli_adhoc.price_list_guid = ess.resolved_price_list_guid
        AND pli_adhoc.product = ess.product
        AND pli_adhoc.registered_customer = 'No'
    LEFT JOIN "Source"."PriceList" pl_adhoc
        ON pl_adhoc."Id" = ess.resolved_price_list_guid
),

-- SMS candidates: rules with all_active_customers='' that have a matching item
evse_sms_candidates AS (
    SELECT
        ear.connector_guid,
        ear.rule_guid AS sms_rule_guid,
        ear.rule_name AS sms_rule_name,
        ear.rule_price_list_guid AS sms_price_list_guid,
        ear.price_list_is_spot AS sms_is_spot,
        NULLIF(pli_sms.price_per_kwh_ex_vat, '')::NUMERIC AS sms_price,
        NULLIF(pli_sms.addon_spot_price, '')::NUMERIC AS sms_spot_markup,
        pl_sms.price_list_name AS sms_price_list_name,
        ear.charger_group_names AS sms_charger_group_names,
        -- Rank SMS candidates (same tie-break as public rules, for determinism)
        ROW_NUMBER() OVER (
            PARTITION BY ear.connector_guid
            ORDER BY
                COALESCE(NULLIF(pli_sms.price_per_kwh_ex_vat, '')::NUMERIC, 0) DESC,
                ear.rule_modified_on DESC NULLS LAST,
                ear.rule_guid ASC
        ) AS sms_rank
    FROM evse_all_rules ear
    INNER JOIN "Source"."PriceListItems" pli_sms
        ON pli_sms.price_list_guid = ear.rule_price_list_guid
        AND pli_sms.product = (
            SELECT ess.product FROM evse_standard_source ess
            WHERE ess.connector_guid = ear.connector_guid
            LIMIT 1
        )
        AND pli_sms.registered_customer = 'No'
    INNER JOIN "Source"."PriceList" pl_sms
        ON pl_sms."Id" = ear.rule_price_list_guid
    WHERE ear.rule_type = 'sms'
),

evse_adhoc_source AS (
    SELECT
        ess.connector_guid,
        ess.product,
        -- Determine final ad-hoc price with SMS override logic
        CASE
            -- SMS override: both fixed, sms_price >= standard_price
            WHEN sms.sms_rule_guid IS NOT NULL
                AND ess.standard_is_spot = FALSE
                AND (sms.sms_is_spot IS NULL OR sms.sms_is_spot != 'Yes')
                AND COALESCE(sms.sms_price, 0) >= COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN COALESCE(sms.sms_price, 0)
            -- Default ad-hoc price
            ELSE ead.adhoc_default_price
        END AS adhoc_price_per_kwh_ex_vat,
        -- Spot markup for ad-hoc
        CASE
            WHEN sms.sms_rule_guid IS NOT NULL
                AND ess.standard_is_spot = FALSE
                AND (sms.sms_is_spot IS NULL OR sms.sms_is_spot != 'Yes')
                AND COALESCE(sms.sms_price, 0) >= COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN NULL  -- SMS override is fixed price
            ELSE ead.adhoc_default_spot_markup
        END AS adhoc_spot_markup_ex_vat,
        -- Is adhoc spot?
        CASE
            WHEN sms.sms_rule_guid IS NOT NULL
                AND ess.standard_is_spot = FALSE
                AND (sms.sms_is_spot IS NULL OR sms.sms_is_spot != 'Yes')
                AND COALESCE(sms.sms_price, 0) >= COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN FALSE
            ELSE ess.standard_is_spot
        END AS adhoc_is_spot,
        -- Price list source
        CASE
            WHEN sms.sms_rule_guid IS NOT NULL
                AND ess.standard_is_spot = FALSE
                AND (sms.sms_is_spot IS NULL OR sms.sms_is_spot != 'Yes')
                AND COALESCE(sms.sms_price, 0) >= COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN sms.sms_price_list_guid
            ELSE ead.adhoc_default_price_list_guid
        END AS adhoc_price_list_guid,
        CASE
            WHEN sms.sms_rule_guid IS NOT NULL
                AND ess.standard_is_spot = FALSE
                AND (sms.sms_is_spot IS NULL OR sms.sms_is_spot != 'Yes')
                AND COALESCE(sms.sms_price, 0) >= COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN sms.sms_price_list_name
            ELSE ead.adhoc_default_price_list_name
        END AS adhoc_price_list_name,
        -- Flags
        CASE
            WHEN sms.sms_rule_guid IS NOT NULL
                AND ess.standard_is_spot = FALSE
                AND (sms.sms_is_spot IS NULL OR sms.sms_is_spot != 'Yes')
                AND COALESCE(sms.sms_price, 0) >= COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN 'ADHOC_FROM_SMS'
            WHEN sms.sms_rule_guid IS NOT NULL
                AND (ess.standard_is_spot = TRUE OR sms.sms_is_spot = 'Yes')
                THEN 'SMS_OVERRIDE_SKIPPED_DUE_TO_SPOT'
            WHEN sms.sms_rule_guid IS NOT NULL
                AND COALESCE(sms.sms_price, 0) < COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN 'SMS_PRICE_LOWER_THAN_STANDARD_IGNORED'
            ELSE NULL
        END AS adhoc_flags
    FROM evse_standard_source ess
    JOIN evse_adhoc_default ead ON ead.connector_guid = ess.connector_guid
    LEFT JOIN evse_sms_candidates sms
        ON sms.connector_guid = ess.connector_guid
        AND sms.sms_rank = 1
),

-- ============================================================================
-- CTE 9: evse_usergroup_tariffs — User group tariffs (TariffOrder = 3..N)
-- One tariff per charger_user_group_guid per EVSE
-- ============================================================================
evse_usergroup_raw AS (
    SELECT
        ear.connector_guid,
        ear.product,
        ear.rule_charger_user_group_guid,
        ear.rule_charger_user_group_name,
        ear.rule_guid,
        ear.rule_price_list_guid,
        ear.price_list_is_spot,
        ear.charger_group_names,
        -- Get price from items (Product, RegisteredCustomer='Yes')
        NULLIF(pli.price_per_kwh_ex_vat, '')::NUMERIC AS ug_price,
        NULLIF(pli.addon_spot_price, '')::NUMERIC AS ug_spot_markup,
        pl.price_list_name AS ug_price_list_name
    FROM evse_all_rules ear
    INNER JOIN "Source"."PriceListItems" pli
        ON pli.price_list_guid = ear.rule_price_list_guid
        AND pli.product = ear.product
        AND pli.registered_customer = 'Yes'  -- Ignore No+No combo
    INNER JOIN "Source"."PriceList" pl
        ON pl."Id" = ear.rule_price_list_guid
    WHERE ear.rule_type = 'usergroup'
      AND ear.rule_charger_user_group_guid IS NOT NULL
      AND btrim(ear.rule_charger_user_group_guid) != ''
),

-- For each EVSE × user group: if multiple rules with different prices, pick highest
evse_usergroup_deduped AS (
    SELECT DISTINCT ON (connector_guid, rule_charger_user_group_guid)
        connector_guid,
        product,
        rule_charger_user_group_guid,
        rule_charger_user_group_name,
        rule_price_list_guid,
        price_list_is_spot,
        charger_group_names,
        ug_price,
        ug_spot_markup,
        ug_price_list_name,
        -- Flag if multiple rules with different prices
        CASE
            WHEN COUNT(*) OVER (PARTITION BY connector_guid, rule_charger_user_group_guid) > 1
            THEN 'MULTIPLE_USERGROUP_RULES_PRICE_AMBIGUOUS'
            ELSE NULL
        END AS ug_flag
    FROM evse_usergroup_raw
    ORDER BY connector_guid, rule_charger_user_group_guid,
        -- Pick highest price (conservative)
        COALESCE(ug_price, 0) DESC,
        rule_guid ASC
),

-- Count members per user group (distinct account numbers)
usergroup_member_counts AS (
    SELECT
        cugm.charger_user_group_guid,
        COUNT(DISTINCT cua.account_number) AS member_account_count
    FROM "Source"."ChargerUserGroupMemberships" cugm
    JOIN "Source"."ChargerUsers" cu
        ON cu.charger_user_guid = cugm.charger_user_guid
    JOIN "Source"."ChargerUserAccounts" cua
        ON cua.account_owner_guid = cu.account_owner_guid
    WHERE cua.account_number IS NOT NULL
    GROUP BY cugm.charger_user_group_guid
),

-- Assign tariff order (3..N) for user group tariffs
evse_usergroup_tariffs AS (
    SELECT
        eud.connector_guid,
        eud.product,
        eud.rule_charger_user_group_guid,
        eud.rule_charger_user_group_name,
        eud.rule_price_list_guid,
        eud.price_list_is_spot,
        eud.charger_group_names,
        eud.ug_price,
        eud.ug_spot_markup,
        eud.ug_price_list_name,
        eud.ug_flag,
        COALESCE(umc.member_account_count, 0) AS member_account_count,
        -- Tariff order: price DESC, then GUID ASC
        ROW_NUMBER() OVER (
            PARTITION BY eud.connector_guid
            ORDER BY COALESCE(eud.ug_price, 0) DESC, eud.rule_charger_user_group_guid ASC
        ) + 2 AS tariff_order  -- starts at 3
    FROM evse_usergroup_deduped eud
    LEFT JOIN usergroup_member_counts umc
        ON umc.charger_user_group_guid = NULLIF(btrim(eud.rule_charger_user_group_guid), '')::uuid
),

-- ============================================================================
-- CTE 10: evse_all_tariffs — UNION ALL of all tariff types
-- ============================================================================
evse_all_tariffs AS (
    -- Standard tariff (order 1)
    SELECT
        ess.connector_guid,
        ess.charger_id,
        ess.connector_level,
        ess.charger_guid,
        ess.location_nr,
        ess.product,
        ess.location_guid,
        ess.location_mapping_key,
        1 AS tariff_order,
        'standard' AS tariff_kind,
        ess.standard_is_spot AS is_spot_price,
        ess.standard_price_per_kwh_ex_vat AS price_per_kwh_ex_vat,
        ess.standard_spot_markup_ex_vat AS spot_markup_ex_vat,
        ess.resolved_price_list_guid::TEXT AS source_price_list_guid,
        ess.source_price_list_name,
        ess.selected_rule_guid::TEXT AS selected_rule_guid,
        ess.selected_rule_name,
        ess.selected_charger_group_names AS charger_group_names,
        NULL::TEXT AS charger_user_group_guid,
        NULL::TEXT AS charger_user_group_name,
        NULL::INTEGER AS member_account_count,
        ess.standard_flags AS flags
    FROM evse_standard_source ess

    UNION ALL

    -- Ad-hoc tariff (order 2)
    SELECT
        eas.connector_guid,
        ess2.charger_id,
        ess2.connector_level,
        ess2.charger_guid,
        ess2.location_nr,
        eas.product,
        ess2.location_guid,
        ess2.location_mapping_key,
        2 AS tariff_order,
        'adhoc' AS tariff_kind,
        eas.adhoc_is_spot AS is_spot_price,
        eas.adhoc_price_per_kwh_ex_vat AS price_per_kwh_ex_vat,
        eas.adhoc_spot_markup_ex_vat AS spot_markup_ex_vat,
        eas.adhoc_price_list_guid::TEXT AS source_price_list_guid,
        eas.adhoc_price_list_name AS source_price_list_name,
        NULL::TEXT AS selected_rule_guid,
        NULL::TEXT AS selected_rule_name,
        NULL::TEXT AS charger_group_names,
        NULL::TEXT AS charger_user_group_guid,
        NULL::TEXT AS charger_user_group_name,
        NULL::INTEGER AS member_account_count,
        eas.adhoc_flags AS flags
    FROM evse_adhoc_source eas
    JOIN evse_standard_source ess2 ON ess2.connector_guid = eas.connector_guid

    UNION ALL

    -- User group tariffs (order 3..N)
    SELECT
        eut.connector_guid,
        ess3.charger_id,
        ess3.connector_level,
        ess3.charger_guid,
        ess3.location_nr,
        eut.product,
        ess3.location_guid,
        ess3.location_mapping_key,
        eut.tariff_order,
        'usergroup' AS tariff_kind,
        (eut.price_list_is_spot = 'Yes') AS is_spot_price,
        CASE
            WHEN eut.price_list_is_spot = 'Yes' THEN NULL
            ELSE COALESCE(eut.ug_price, 0)
        END AS price_per_kwh_ex_vat,
        CASE
            WHEN eut.price_list_is_spot = 'Yes' THEN COALESCE(eut.ug_spot_markup, 0)
            ELSE NULL
        END AS spot_markup_ex_vat,
        eut.rule_price_list_guid::TEXT AS source_price_list_guid,
        eut.ug_price_list_name AS source_price_list_name,
        NULL::TEXT AS selected_rule_guid,
        NULL::TEXT AS selected_rule_name,
        eut.charger_group_names,
        eut.rule_charger_user_group_guid AS charger_user_group_guid,
        eut.rule_charger_user_group_name AS charger_user_group_name,
        eut.member_account_count,
        eut.ug_flag AS flags
    FROM evse_usergroup_tariffs eut
    JOIN evse_standard_source ess3 ON ess3.connector_guid = eut.connector_guid
),

-- ============================================================================
-- CTE 11: evse_signature — Canonical pricing signature per EVSE
-- Used for tariff group hashing. Must be deterministic and stable.
-- ============================================================================
evse_tariff_parts AS (
    SELECT
        connector_guid,
        product,
        tariff_order,
        tariff_kind,
        is_spot_price,
        price_per_kwh_ex_vat,
        spot_markup_ex_vat,
        charger_user_group_guid
    FROM evse_all_tariffs
),

evse_signature AS (
    SELECT
        connector_guid,
        product,
        -- Build canonical signature:
        -- product|std_part|adhoc_part|ug1_part;ug2_part;...
        product || '|' ||
        -- Standard part
        COALESCE(
            (SELECT
                CASE WHEN etp_s.is_spot_price THEN 'SPOT:' || COALESCE(etp_s.spot_markup_ex_vat::TEXT, '0')
                     ELSE 'FIXED:' || COALESCE(etp_s.price_per_kwh_ex_vat::TEXT, '0')
                END
             FROM evse_tariff_parts etp_s
             WHERE etp_s.connector_guid = etp_main.connector_guid AND etp_s.tariff_order = 1
            ), 'NONE'
        ) || '|' ||
        -- Ad-hoc part
        COALESCE(
            (SELECT
                CASE WHEN etp_a.is_spot_price THEN 'SPOT:' || COALESCE(etp_a.spot_markup_ex_vat::TEXT, '0')
                     ELSE 'FIXED:' || COALESCE(etp_a.price_per_kwh_ex_vat::TEXT, '0')
                END
             FROM evse_tariff_parts etp_a
             WHERE etp_a.connector_guid = etp_main.connector_guid AND etp_a.tariff_order = 2
            ), 'NONE'
        ) || '|' ||
        -- User group parts (sorted by price DESC, GUID ASC — matches tariff_order assignment)
        COALESCE(
            (SELECT STRING_AGG(
                LOWER(etp_u.charger_user_group_guid) || ':' ||
                CASE WHEN etp_u.is_spot_price THEN 'SPOT:' || COALESCE(etp_u.spot_markup_ex_vat::TEXT, '0')
                     ELSE 'FIXED:' || COALESCE(etp_u.price_per_kwh_ex_vat::TEXT, '0')
                END,
                ';' ORDER BY etp_u.tariff_order
             )
             FROM evse_tariff_parts etp_u
             WHERE etp_u.connector_guid = etp_main.connector_guid AND etp_u.tariff_order >= 3
            ), ''
        ) AS canonical_signature
    FROM (SELECT DISTINCT connector_guid, product FROM evse_tariff_parts) etp_main
),

-- ============================================================================
-- CTE 12: evse_tariff_group_hash — MD5 hash + org grouping key
-- ============================================================================
evse_tariff_group_hash AS (
    SELECT
        es.connector_guid,
        es.product,
        es.canonical_signature,
        UPPER(md5(es.canonical_signature)) AS tariff_group_hash,
        mpr.grouping_key,
        COALESCE(mpr.grouping_key, 'LOC:' || ewp.location_guid::TEXT) || ':' || UPPER(md5(es.canonical_signature)) AS tariff_group_key
    FROM evse_signature es
    JOIN evse_with_product ewp ON ewp.connector_guid = es.connector_guid
    LEFT JOIN "Mapping"."MasterPartnerResolution" mpr ON mpr.mapping_key = ewp.location_mapping_key
)

-- ============================================================================
-- Final SELECT: One row per in-scope connector with tariff group assignment
-- ============================================================================
SELECT
    etgh.connector_guid,
    ewp.charger_id,
    ewp.connector_level,
    ewp.charger_guid,
    ewp.location_nr,
    etgh.product,
    ewp.location_guid,
    ewp.location_mapping_key,
    etgh.tariff_group_hash,
    etgh.grouping_key,
    etgh.tariff_group_key,
    'Sleet|TariffGroup|' || etgh.tariff_group_key AS mapping_key,
    etgh.canonical_signature,
    -- How many distinct locations share this tariff group (unfiltered)
    (SELECT COUNT(DISTINCT ewp2.location_nr)
     FROM evse_tariff_group_hash etgh2
     JOIN evse_with_product ewp2 ON ewp2.connector_guid = etgh2.connector_guid
     WHERE etgh2.tariff_group_key = etgh.tariff_group_key
    ) AS tariff_group_location_count
FROM evse_tariff_group_hash etgh
JOIN evse_with_product ewp ON ewp.connector_guid = etgh.connector_guid;

-- Indexes for fast joins from target views (310, 311, 313)
CREATE INDEX IF NOT EXISTS idx_etga_mapping_key ON "Source"."EvseTariffGroupAssignment" (mapping_key);
CREATE INDEX IF NOT EXISTS idx_etga_tariff_group_key ON "Source"."EvseTariffGroupAssignment" (tariff_group_key);
CREATE INDEX IF NOT EXISTS idx_etga_connector_guid ON "Source"."EvseTariffGroupAssignment" (connector_guid);
CREATE INDEX IF NOT EXISTS idx_etga_location_mapping_key ON "Source"."EvseTariffGroupAssignment" (location_mapping_key);


-- ============================================================================
-- TABLE 2: Source.EvseTariffRows
-- One row per EVSE × tariff order with full pricing and quality data
-- ============================================================================
CREATE TABLE "Source"."EvseTariffRows" AS
WITH
-- Same CTE chain as EvseTariffGroupAssignment (duplicated because
-- CREATE TABLE AS requires self-contained CTEs; evaluated once at build time)
evse_with_product AS (
    SELECT
        ep.connector_guid,
        ep.charger_id,
        ep.connector_level,
        ep.connector_type,
        ep.power_type,
        ep.charger_guid,
        ep.location_nr,
        ep.product,
        loc."Id"          AS location_guid,
        loc.price_list_guid AS location_price_list_guid,
        lm.mapping_key    AS location_mapping_key
    FROM "Source"."EvseProduct" ep
    JOIN "Source"."Locations" loc ON loc.location_nr = ep.location_nr
    LEFT JOIN "Mapping"."location_mapping" lm
        ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
),

evse_charger_groups AS (
    SELECT DISTINCT
        ewp.connector_guid,
        ewp.charger_id,
        ewp.connector_level,
        ewp.charger_guid,
        ewp.location_nr,
        ewp.product,
        ewp.location_guid,
        ewp.location_price_list_guid,
        ewp.location_mapping_key,
        ctcg.charger_group_guid,
        ctcg.charger_to_charger_group_name AS charger_group_name
    FROM evse_with_product ewp
    JOIN "Source"."ChargerToChargerGroup" ctcg
        ON ctcg.charger_guid = ewp.charger_guid
),

evse_all_rules_raw AS (
    SELECT
        ecg.connector_guid,
        ecg.charger_id,
        ecg.connector_level,
        ecg.charger_guid,
        ecg.location_nr,
        ecg.product,
        ecg.location_guid,
        ecg.location_price_list_guid,
        ecg.location_mapping_key,
        ptuc.price_to_users_and_chargers_guid AS rule_guid,
        ptuc.price_to_users_and_chargers_name AS rule_name,
        ptuc.price_list_guid AS rule_price_list_guid,
        ptuc.all_active_customers,
        ptuc.discount,
        ptuc.show_in_app,
        ptuc.search_in_app,
        ptuc.charger_user_group_guid AS rule_charger_user_group_guid,
        ptuc.charger_user_group_name AS rule_charger_user_group_name,
        ptuc.modified_on AS rule_modified_on,
        ptuc.created_on AS rule_created_on,
        ecg.charger_group_name,
        pl.is_spot_price AS price_list_is_spot,
        CASE
            WHEN ptuc.all_active_customers = 'Yes' THEN 'public'
            WHEN ptuc.all_active_customers = 'No'  THEN 'usergroup'
            ELSE 'sms'
        END AS rule_type
    FROM evse_charger_groups ecg
    JOIN "Source"."PriceToUsersAndChargers" ptuc
        ON ptuc.charger_group_guid = ecg.charger_group_guid
    INNER JOIN "Source"."PriceList" pl
        ON pl."Id" = ptuc.price_list_guid
    WHERE (pl.roaming_party_id IS NULL OR btrim(pl.roaming_party_id::TEXT) = '')
),

evse_all_rules AS (
    SELECT DISTINCT ON (connector_guid, rule_guid)
        connector_guid,
        charger_id,
        connector_level,
        charger_guid,
        location_nr,
        product,
        location_guid,
        location_price_list_guid,
        location_mapping_key,
        rule_guid,
        rule_name,
        rule_price_list_guid,
        all_active_customers,
        discount,
        show_in_app,
        search_in_app,
        rule_charger_user_group_guid,
        rule_charger_user_group_name,
        rule_modified_on,
        rule_created_on,
        price_list_is_spot,
        rule_type,
        (SELECT STRING_AGG(DISTINCT sub.charger_group_name, ', ' ORDER BY sub.charger_group_name)
         FROM evse_all_rules_raw sub
         WHERE sub.connector_guid = evse_all_rules_raw.connector_guid
           AND sub.rule_guid = evse_all_rules_raw.rule_guid
        ) AS charger_group_names
    FROM evse_all_rules_raw
    ORDER BY connector_guid, rule_guid
),

evse_public_rules AS (
    SELECT
        ear.*,
        CASE WHEN pli.price_list_configuration_guid IS NOT NULL THEN TRUE ELSE FALSE END
            AS has_matching_item,
        COUNT(*) OVER (PARTITION BY ear.connector_guid) AS public_rule_count
    FROM evse_all_rules ear
    LEFT JOIN "Source"."PriceListItems" pli
        ON pli.price_list_guid = ear.rule_price_list_guid
        AND pli.product = ear.product
        AND pli.registered_customer = 'Yes'
    WHERE ear.rule_type = 'public'
),

evse_public_selected AS (
    SELECT DISTINCT ON (connector_guid)
        connector_guid,
        charger_id,
        connector_level,
        charger_guid,
        location_nr,
        product,
        location_guid,
        location_price_list_guid,
        location_mapping_key,
        rule_guid AS selected_rule_guid,
        rule_name AS selected_rule_name,
        rule_price_list_guid AS selected_price_list_guid,
        price_list_is_spot AS selected_is_spot,
        charger_group_names AS selected_charger_group_names,
        CASE WHEN public_rule_count > 1
             THEN 'MULTIPLE_PUBLIC_RULES_RESOLVED'
             ELSE NULL
        END AS flag_multiple_public
    FROM evse_public_rules
    ORDER BY connector_guid,
        has_matching_item DESC,
        (discount = 'No') DESC,
        (show_in_app = 'Yes') DESC,
        (search_in_app = 'Yes') DESC,
        rule_modified_on DESC NULLS LAST,
        rule_created_on DESC NULLS LAST,
        rule_guid ASC
),

evse_no_public AS (
    SELECT ewp.*
    FROM evse_with_product ewp
    WHERE NOT EXISTS (
        SELECT 1 FROM evse_public_selected eps
        WHERE eps.connector_guid = ewp.connector_guid
    )
),

evse_fallback AS (
    SELECT
        enp.connector_guid,
        enp.charger_id,
        enp.connector_level,
        enp.charger_guid,
        enp.location_nr,
        enp.product,
        enp.location_guid,
        enp.location_price_list_guid,
        enp.location_mapping_key,
        COALESCE(loc_pl."Id", def_pl."Id") AS fallback_price_list_guid,
        COALESCE(loc_pl.is_spot_price, def_pl.is_spot_price) AS fallback_is_spot,
        CASE
            WHEN loc_pl."Id" IS NOT NULL THEN 'PUBLIC_FROM_LOCATION_FALLBACK'
            WHEN def_pl."Id" IS NOT NULL THEN 'PUBLIC_FROM_DEFAULT_FALLBACK'
            ELSE NULL
        END AS flag_fallback
    FROM evse_no_public enp
    LEFT JOIN "Source"."PriceList" loc_pl
        ON loc_pl."Id" = NULLIF(btrim(enp.location_price_list_guid), '')::uuid
    LEFT JOIN "Source"."PriceList" def_pl
        ON def_pl.default_price_list = 'Yes'
        AND loc_pl."Id" IS NULL
),

evse_standard_resolved AS (
    SELECT
        eps.connector_guid, eps.charger_id, eps.connector_level, eps.charger_guid,
        eps.location_nr, eps.product, eps.location_guid, eps.location_mapping_key,
        eps.selected_price_list_guid AS resolved_price_list_guid,
        eps.selected_is_spot AS resolved_is_spot,
        eps.selected_rule_guid, eps.selected_rule_name,
        eps.selected_charger_group_names,
        eps.flag_multiple_public AS flag
    FROM evse_public_selected eps
    UNION ALL
    SELECT
        ef.connector_guid, ef.charger_id, ef.connector_level, ef.charger_guid,
        ef.location_nr, ef.product, ef.location_guid, ef.location_mapping_key,
        ef.fallback_price_list_guid, ef.fallback_is_spot,
        NULL::uuid, NULL::TEXT, NULL::TEXT,
        ef.flag_fallback
    FROM evse_fallback ef
),

evse_standard_source AS (
    SELECT
        esr.connector_guid, esr.charger_id, esr.connector_level, esr.charger_guid,
        esr.location_nr, esr.product, esr.location_guid, esr.location_mapping_key,
        esr.resolved_price_list_guid, esr.resolved_is_spot,
        esr.selected_rule_guid, esr.selected_rule_name, esr.selected_charger_group_names,
        pl.price_list_name AS source_price_list_name,
        CASE
            WHEN esr.resolved_is_spot = 'Yes' THEN NULL
            WHEN pli.price_list_configuration_guid IS NULL THEN NULL
            ELSE COALESCE(NULLIF(pli.price_per_kwh_ex_vat, '')::NUMERIC, 0)
        END AS standard_price_per_kwh_ex_vat,
        CASE
            WHEN esr.resolved_is_spot = 'Yes' AND pli.price_list_configuration_guid IS NOT NULL
                THEN COALESCE(NULLIF(pli.addon_spot_price, '')::NUMERIC, 0)
            ELSE NULL
        END AS standard_spot_markup_ex_vat,
        (esr.resolved_is_spot = 'Yes') AS standard_is_spot,
        CASE
            WHEN esr.flag IS NOT NULL AND pli.price_list_configuration_guid IS NULL
                THEN esr.flag || ',MISSING_TARIFF_ITEMS'
            WHEN pli.price_list_configuration_guid IS NULL
                THEN 'MISSING_TARIFF_ITEMS'
            ELSE esr.flag
        END AS standard_flags
    FROM evse_standard_resolved esr
    LEFT JOIN "Source"."PriceList" pl ON pl."Id" = esr.resolved_price_list_guid
    LEFT JOIN "Source"."PriceListItems" pli
        ON pli.price_list_guid = esr.resolved_price_list_guid
        AND pli.product = esr.product
        AND pli.registered_customer = 'Yes'
),

evse_adhoc_default AS (
    SELECT
        ess.connector_guid, ess.resolved_price_list_guid, ess.resolved_is_spot,
        ess.standard_price_per_kwh_ex_vat, ess.standard_is_spot,
        CASE
            WHEN ess.resolved_is_spot = 'Yes' THEN NULL
            WHEN pli_adhoc.price_list_configuration_guid IS NULL THEN NULL
            ELSE COALESCE(NULLIF(pli_adhoc.price_per_kwh_ex_vat, '')::NUMERIC, 0)
        END AS adhoc_default_price,
        CASE
            WHEN ess.resolved_is_spot = 'Yes' AND pli_adhoc.price_list_configuration_guid IS NOT NULL
                THEN COALESCE(NULLIF(pli_adhoc.addon_spot_price, '')::NUMERIC, 0)
            ELSE NULL
        END AS adhoc_default_spot_markup,
        pli_adhoc.price_list_guid AS adhoc_default_price_list_guid,
        pl_adhoc.price_list_name AS adhoc_default_price_list_name
    FROM evse_standard_source ess
    LEFT JOIN "Source"."PriceListItems" pli_adhoc
        ON pli_adhoc.price_list_guid = ess.resolved_price_list_guid
        AND pli_adhoc.product = ess.product
        AND pli_adhoc.registered_customer = 'No'
    LEFT JOIN "Source"."PriceList" pl_adhoc
        ON pl_adhoc."Id" = ess.resolved_price_list_guid
),

evse_sms_candidates AS (
    SELECT
        ear.connector_guid,
        ear.rule_guid AS sms_rule_guid,
        ear.rule_name AS sms_rule_name,
        ear.rule_price_list_guid AS sms_price_list_guid,
        ear.price_list_is_spot AS sms_is_spot,
        NULLIF(pli_sms.price_per_kwh_ex_vat, '')::NUMERIC AS sms_price,
        NULLIF(pli_sms.addon_spot_price, '')::NUMERIC AS sms_spot_markup,
        pl_sms.price_list_name AS sms_price_list_name,
        ear.charger_group_names AS sms_charger_group_names,
        ROW_NUMBER() OVER (
            PARTITION BY ear.connector_guid
            ORDER BY
                COALESCE(NULLIF(pli_sms.price_per_kwh_ex_vat, '')::NUMERIC, 0) DESC,
                ear.rule_modified_on DESC NULLS LAST,
                ear.rule_guid ASC
        ) AS sms_rank
    FROM evse_all_rules ear
    INNER JOIN "Source"."PriceListItems" pli_sms
        ON pli_sms.price_list_guid = ear.rule_price_list_guid
        AND pli_sms.product = (
            SELECT ess.product FROM evse_standard_source ess
            WHERE ess.connector_guid = ear.connector_guid
            LIMIT 1
        )
        AND pli_sms.registered_customer = 'No'
    INNER JOIN "Source"."PriceList" pl_sms
        ON pl_sms."Id" = ear.rule_price_list_guid
    WHERE ear.rule_type = 'sms'
),

evse_adhoc_source AS (
    SELECT
        ess.connector_guid,
        ess.product,
        CASE
            WHEN sms.sms_rule_guid IS NOT NULL
                AND ess.standard_is_spot = FALSE
                AND (sms.sms_is_spot IS NULL OR sms.sms_is_spot != 'Yes')
                AND COALESCE(sms.sms_price, 0) >= COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN COALESCE(sms.sms_price, 0)
            ELSE ead.adhoc_default_price
        END AS adhoc_price_per_kwh_ex_vat,
        CASE
            WHEN sms.sms_rule_guid IS NOT NULL
                AND ess.standard_is_spot = FALSE
                AND (sms.sms_is_spot IS NULL OR sms.sms_is_spot != 'Yes')
                AND COALESCE(sms.sms_price, 0) >= COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN NULL
            ELSE ead.adhoc_default_spot_markup
        END AS adhoc_spot_markup_ex_vat,
        CASE
            WHEN sms.sms_rule_guid IS NOT NULL
                AND ess.standard_is_spot = FALSE
                AND (sms.sms_is_spot IS NULL OR sms.sms_is_spot != 'Yes')
                AND COALESCE(sms.sms_price, 0) >= COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN FALSE
            ELSE ess.standard_is_spot
        END AS adhoc_is_spot,
        CASE
            WHEN sms.sms_rule_guid IS NOT NULL
                AND ess.standard_is_spot = FALSE
                AND (sms.sms_is_spot IS NULL OR sms.sms_is_spot != 'Yes')
                AND COALESCE(sms.sms_price, 0) >= COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN sms.sms_price_list_guid
            ELSE ead.adhoc_default_price_list_guid
        END AS adhoc_price_list_guid,
        CASE
            WHEN sms.sms_rule_guid IS NOT NULL
                AND ess.standard_is_spot = FALSE
                AND (sms.sms_is_spot IS NULL OR sms.sms_is_spot != 'Yes')
                AND COALESCE(sms.sms_price, 0) >= COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN sms.sms_price_list_name
            ELSE ead.adhoc_default_price_list_name
        END AS adhoc_price_list_name,
        CASE
            WHEN sms.sms_rule_guid IS NOT NULL
                AND ess.standard_is_spot = FALSE
                AND (sms.sms_is_spot IS NULL OR sms.sms_is_spot != 'Yes')
                AND COALESCE(sms.sms_price, 0) >= COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN 'ADHOC_FROM_SMS'
            WHEN sms.sms_rule_guid IS NOT NULL
                AND (ess.standard_is_spot = TRUE OR sms.sms_is_spot = 'Yes')
                THEN 'SMS_OVERRIDE_SKIPPED_DUE_TO_SPOT'
            WHEN sms.sms_rule_guid IS NOT NULL
                AND COALESCE(sms.sms_price, 0) < COALESCE(ess.standard_price_per_kwh_ex_vat, 0)
                THEN 'SMS_PRICE_LOWER_THAN_STANDARD_IGNORED'
            ELSE NULL
        END AS adhoc_flags
    FROM evse_standard_source ess
    JOIN evse_adhoc_default ead ON ead.connector_guid = ess.connector_guid
    LEFT JOIN evse_sms_candidates sms
        ON sms.connector_guid = ess.connector_guid
        AND sms.sms_rank = 1
),

evse_usergroup_raw AS (
    SELECT
        ear.connector_guid, ear.product,
        ear.rule_charger_user_group_guid, ear.rule_charger_user_group_name,
        ear.rule_guid, ear.rule_price_list_guid, ear.price_list_is_spot,
        ear.charger_group_names,
        NULLIF(pli.price_per_kwh_ex_vat, '')::NUMERIC AS ug_price,
        NULLIF(pli.addon_spot_price, '')::NUMERIC AS ug_spot_markup,
        pl.price_list_name AS ug_price_list_name
    FROM evse_all_rules ear
    INNER JOIN "Source"."PriceListItems" pli
        ON pli.price_list_guid = ear.rule_price_list_guid
        AND pli.product = ear.product
        AND pli.registered_customer = 'Yes'
    INNER JOIN "Source"."PriceList" pl
        ON pl."Id" = ear.rule_price_list_guid
    WHERE ear.rule_type = 'usergroup'
      AND ear.rule_charger_user_group_guid IS NOT NULL
      AND btrim(ear.rule_charger_user_group_guid) != ''
),

evse_usergroup_deduped AS (
    SELECT DISTINCT ON (connector_guid, rule_charger_user_group_guid)
        connector_guid, product, rule_charger_user_group_guid, rule_charger_user_group_name,
        rule_price_list_guid, price_list_is_spot, charger_group_names,
        ug_price, ug_spot_markup, ug_price_list_name,
        CASE
            WHEN COUNT(*) OVER (PARTITION BY connector_guid, rule_charger_user_group_guid) > 1
            THEN 'MULTIPLE_USERGROUP_RULES_PRICE_AMBIGUOUS'
            ELSE NULL
        END AS ug_flag
    FROM evse_usergroup_raw
    ORDER BY connector_guid, rule_charger_user_group_guid,
        COALESCE(ug_price, 0) DESC,
        rule_guid ASC
),

usergroup_member_counts AS (
    SELECT
        cugm.charger_user_group_guid,
        COUNT(DISTINCT cua.account_number) AS member_account_count
    FROM "Source"."ChargerUserGroupMemberships" cugm
    JOIN "Source"."ChargerUsers" cu
        ON cu.charger_user_guid = cugm.charger_user_guid
    JOIN "Source"."ChargerUserAccounts" cua
        ON cua.account_owner_guid = cu.account_owner_guid
    WHERE cua.account_number IS NOT NULL
    GROUP BY cugm.charger_user_group_guid
),

evse_usergroup_tariffs AS (
    SELECT
        eud.connector_guid, eud.product, eud.rule_charger_user_group_guid,
        eud.rule_charger_user_group_name, eud.rule_price_list_guid,
        eud.price_list_is_spot, eud.charger_group_names,
        eud.ug_price, eud.ug_spot_markup, eud.ug_price_list_name, eud.ug_flag,
        COALESCE(umc.member_account_count, 0) AS member_account_count,
        ROW_NUMBER() OVER (
            PARTITION BY eud.connector_guid
            ORDER BY COALESCE(eud.ug_price, 0) DESC, eud.rule_charger_user_group_guid ASC
        ) + 2 AS tariff_order
    FROM evse_usergroup_deduped eud
    LEFT JOIN usergroup_member_counts umc
        ON umc.charger_user_group_guid = NULLIF(btrim(eud.rule_charger_user_group_guid), '')::uuid
),

evse_all_tariffs AS (
    -- Standard (order 1)
    SELECT
        ess.connector_guid, ess.charger_id, ess.connector_level, ess.charger_guid,
        ess.location_nr, ess.product, ess.location_guid, ess.location_mapping_key,
        1 AS tariff_order, 'standard' AS tariff_kind,
        ess.standard_is_spot AS is_spot_price,
        ess.standard_price_per_kwh_ex_vat AS price_per_kwh_ex_vat,
        ess.standard_spot_markup_ex_vat AS spot_markup_ex_vat,
        ess.resolved_price_list_guid::TEXT AS source_price_list_guid,
        ess.source_price_list_name,
        ess.selected_rule_guid::TEXT AS selected_rule_guid,
        ess.selected_rule_name,
        ess.selected_charger_group_names AS charger_group_names,
        NULL::TEXT AS charger_user_group_guid,
        NULL::TEXT AS charger_user_group_name,
        NULL::INTEGER AS member_account_count,
        ess.standard_flags AS flags
    FROM evse_standard_source ess
    UNION ALL
    -- Ad-hoc (order 2)
    SELECT
        eas.connector_guid, ess2.charger_id, ess2.connector_level, ess2.charger_guid,
        ess2.location_nr, eas.product, ess2.location_guid, ess2.location_mapping_key,
        2, 'adhoc',
        eas.adhoc_is_spot, eas.adhoc_price_per_kwh_ex_vat, eas.adhoc_spot_markup_ex_vat,
        eas.adhoc_price_list_guid::TEXT, eas.adhoc_price_list_name,
        NULL::TEXT, NULL::TEXT, NULL::TEXT,
        NULL::TEXT, NULL::TEXT, NULL::INTEGER,
        eas.adhoc_flags
    FROM evse_adhoc_source eas
    JOIN evse_standard_source ess2 ON ess2.connector_guid = eas.connector_guid
    UNION ALL
    -- User group (order 3..N)
    SELECT
        eut.connector_guid, ess3.charger_id, ess3.connector_level, ess3.charger_guid,
        ess3.location_nr, eut.product, ess3.location_guid, ess3.location_mapping_key,
        eut.tariff_order, 'usergroup',
        (eut.price_list_is_spot = 'Yes'),
        CASE WHEN eut.price_list_is_spot = 'Yes' THEN NULL ELSE COALESCE(eut.ug_price, 0) END,
        CASE WHEN eut.price_list_is_spot = 'Yes' THEN COALESCE(eut.ug_spot_markup, 0) ELSE NULL END,
        eut.rule_price_list_guid::TEXT, eut.ug_price_list_name,
        NULL::TEXT, NULL::TEXT, eut.charger_group_names,
        eut.rule_charger_user_group_guid, eut.rule_charger_user_group_name,
        eut.member_account_count,
        eut.ug_flag
    FROM evse_usergroup_tariffs eut
    JOIN evse_standard_source ess3 ON ess3.connector_guid = eut.connector_guid
),

-- Build signature and hash for joining back
evse_tariff_parts AS (
    SELECT connector_guid, product, tariff_order, tariff_kind,
        is_spot_price, price_per_kwh_ex_vat, spot_markup_ex_vat, charger_user_group_guid
    FROM evse_all_tariffs
),

evse_signature AS (
    SELECT
        connector_guid, product,
        product || '|' ||
        COALESCE(
            (SELECT CASE WHEN etp_s.is_spot_price THEN 'SPOT:' || COALESCE(etp_s.spot_markup_ex_vat::TEXT, '0')
                         ELSE 'FIXED:' || COALESCE(etp_s.price_per_kwh_ex_vat::TEXT, '0') END
             FROM evse_tariff_parts etp_s
             WHERE etp_s.connector_guid = etp_main.connector_guid AND etp_s.tariff_order = 1), 'NONE'
        ) || '|' ||
        COALESCE(
            (SELECT CASE WHEN etp_a.is_spot_price THEN 'SPOT:' || COALESCE(etp_a.spot_markup_ex_vat::TEXT, '0')
                         ELSE 'FIXED:' || COALESCE(etp_a.price_per_kwh_ex_vat::TEXT, '0') END
             FROM evse_tariff_parts etp_a
             WHERE etp_a.connector_guid = etp_main.connector_guid AND etp_a.tariff_order = 2), 'NONE'
        ) || '|' ||
        COALESCE(
            (SELECT STRING_AGG(
                LOWER(etp_u.charger_user_group_guid) || ':' ||
                CASE WHEN etp_u.is_spot_price THEN 'SPOT:' || COALESCE(etp_u.spot_markup_ex_vat::TEXT, '0')
                     ELSE 'FIXED:' || COALESCE(etp_u.price_per_kwh_ex_vat::TEXT, '0') END,
                ';' ORDER BY etp_u.tariff_order)
             FROM evse_tariff_parts etp_u
             WHERE etp_u.connector_guid = etp_main.connector_guid AND etp_u.tariff_order >= 3), ''
        ) AS canonical_signature
    FROM (SELECT DISTINCT connector_guid, product FROM evse_tariff_parts) etp_main
),

evse_tariff_group_hash AS (
    SELECT
        es.connector_guid, es.product, es.canonical_signature,
        UPPER(md5(es.canonical_signature)) AS tariff_group_hash,
        mpr.grouping_key,
        COALESCE(mpr.grouping_key, 'LOC:' || ewp.location_guid::TEXT) || ':' || UPPER(md5(es.canonical_signature)) AS tariff_group_key
    FROM evse_signature es
    JOIN evse_with_product ewp ON ewp.connector_guid = es.connector_guid
    LEFT JOIN "Mapping"."MasterPartnerResolution" mpr ON mpr.mapping_key = ewp.location_mapping_key
)

-- Final SELECT: One row per EVSE × tariff with full pricing, VAT, and quality data
SELECT
    eat.connector_guid,
    eat.charger_id,
    eat.connector_level,
    eat.charger_guid,
    eat.location_nr,
    eat.product,
    eat.location_guid,
    eat.location_mapping_key,
    eat.tariff_order,
    eat.tariff_kind,
    eat.is_spot_price,
    eat.price_per_kwh_ex_vat,
    eat.spot_markup_ex_vat,
    -- VAT multiplier: 1.0 for "Flexilading uten fradrag", 1.25 for all others
    CASE WHEN eat.product = 'Flexilading uten fradrag' THEN 1.0 ELSE 1.25 END AS vat_multiplier,
    -- taxID: 2 for 0% No VAT (uten fradrag), 1 for 25% VAT (all others)
    CASE WHEN eat.product = 'Flexilading uten fradrag' THEN 2 ELSE 1 END AS "taxID",
    -- Spot zone mapping (hardcoded: Coop→NO1=2, Gardermoen→NO1=2)
    CASE
        WHEN eat.is_spot_price AND eat.source_price_list_name ILIKE '%Coop%' THEN 2
        WHEN eat.is_spot_price AND eat.source_price_list_name ILIKE '%Gardermoen%' THEN 2
        ELSE NULL
    END AS "fallbackElectricityRateId",
    eat.source_price_list_guid,
    eat.source_price_list_name,
    eat.selected_rule_guid,
    eat.selected_rule_name,
    eat.charger_group_names,
    eat.charger_user_group_guid,
    eat.charger_user_group_name,
    eat.member_account_count,
    eat.flags,
    -- Tariff group assignment (from hash)
    etgh.tariff_group_hash,
    etgh.grouping_key,
    etgh.tariff_group_key,
    'Sleet|TariffGroup|' || etgh.tariff_group_key AS tariff_group_mapping_key,
    'Sleet|Tariff|' || etgh.tariff_group_key || '|' || eat.tariff_order AS tariff_mapping_key,
    -- How many distinct locations share this tariff group (unfiltered)
    (SELECT COUNT(DISTINCT eat2.location_nr)
     FROM evse_all_tariffs eat2
     JOIN evse_tariff_group_hash etgh2 ON etgh2.connector_guid = eat2.connector_guid
     WHERE etgh2.tariff_group_key = etgh.tariff_group_key
    ) AS tariff_group_location_count
FROM evse_all_tariffs eat
JOIN evse_tariff_group_hash etgh ON etgh.connector_guid = eat.connector_guid;

-- Indexes for fast joins from target views (310, 311)
CREATE INDEX IF NOT EXISTS idx_etr_tariff_group_key_order ON "Source"."EvseTariffRows" (tariff_group_key, tariff_order);
CREATE INDEX IF NOT EXISTS idx_etr_tariff_group_mapping_key ON "Source"."EvseTariffRows" (tariff_group_mapping_key);
CREATE INDEX IF NOT EXISTS idx_etr_tariff_mapping_key ON "Source"."EvseTariffRows" (tariff_mapping_key);
CREATE INDEX IF NOT EXISTS idx_etr_location_mapping_key ON "Source"."EvseTariffRows" (location_mapping_key);
CREATE INDEX IF NOT EXISTS idx_etr_connector_guid ON "Source"."EvseTariffRows" (connector_guid);
