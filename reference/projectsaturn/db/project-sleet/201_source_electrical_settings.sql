-- ============================================================================
-- VIEW: Source.ElectricalSettingsNormalized
-- ============================================================================
-- Intermediate view for electrical settings normalization.
-- One row per connector on chargers (no location or status filter).
--
-- Business rules ported from Source.ElectricalSettingsAnalysis (098).
-- Target views (112, 113) apply their own location_mapping filters.
-- Quality report (202) applies exclude = FALSE.
--
-- Phase B rewrite: 2026-02-08
-- Fixes TT network bug (TT now correctly produces 'delta' configuration).
-- Adds: is_first_connector, target_cp_default_max_current, EVSE phase
-- rotation level selection, two new quality flags.
--
-- Fix: 2026-02-17
-- AC_1_PHASE connectors with full rotation-format phase mappings (N312, N231,
-- etc.) were misclassified as three_phase because the N[123]{3} pattern matched
-- before power_type was consulted. Now power_type is authoritative: AC_1_PHASE
-- always produces single_phase. Connected phase derived from first digit after N
-- (consistent with rotation table: N312→TRS→L3 on charger L1). 46 connectors.
--
-- COLUMN ORDERING RULES:
--   1. Primary key & metadata  – charger_id, connector_level,
--                                 is_first_connector, charger_name
--   2. Raw source              – controller_nettype, charger N/L1/L2/L3,
--                                 connector power_type/phase_mapping/amperage/max_effect
--   2b. Lookup                 – max_charger_current (from Mapping.ChargerProductLookup)
--   3. Intermediate            – charger_phase_mapping_derived
--   4. Target: Charge Point    – electrical_configuration, phases, max_voltage,
--                                 phase_rotation (level-selected), connected_phase,
--                                 default_max_current
--   5. Target: EVSE            – phases, max_voltage, phase_rotation (level-selected),
--                                 connected_phase, max_amperage, max_power
--   6. Quality flags           – 12 boolean flags prefixed has_
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Source"."ElectricalSettingsNormalized" CASCADE;

CREATE OR REPLACE VIEW "Source"."ElectricalSettingsNormalized" AS

-- ============================================================================
-- CTE 1: raw_data — source columns, no location filter
-- ============================================================================
WITH raw_data AS (
    SELECT 
        -- Primary key
        conn.id AS charger_id,
        conn.connector_level AS connector_level,
        -- Metadata
        chr.charger_name AS charger_name,
        chr.status AS charger_status,
        conn.status_reason AS connector_status_reason,
        -- Source: Controller
        UPPER(TRIM(COALESCE(ctrl.nettype, ''))) AS controller_nettype,
        -- Source: Charger
        chr.n AS charger_n,
        chr.l1 AS charger_l1,
        chr.l2 AS charger_l2,
        chr.l3 AS charger_l3,
        -- Source: Connector
        conn.power_type AS connector_power_type,
        conn.phase_mapping AS connector_phase_mapping,
        conn.amperage_connector AS connector_amperage,
        conn.max_effect AS connector_max_effect,
        -- Charger product lookup: vendor-specified max current per phase (A)
        cpl.max_charger_current AS max_charger_current,
        -- Count how many charger columns have a value (expected: 0, 2, or 3)
        (CASE WHEN NULLIF(TRIM(COALESCE(chr.n, '')), '') IS NOT NULL THEN 1 ELSE 0 END +
         CASE WHEN NULLIF(TRIM(COALESCE(chr.l1, '')), '') IS NOT NULL THEN 1 ELSE 0 END +
         CASE WHEN NULLIF(TRIM(COALESCE(chr.l2, '')), '') IS NOT NULL THEN 1 ELSE 0 END +
         CASE WHEN NULLIF(TRIM(COALESCE(chr.l3, '')), '') IS NOT NULL THEN 1 ELSE 0 END
        ) AS charger_columns_populated
    FROM "Source"."Connectors" conn
    JOIN "Source"."Chargers" chr ON chr.charger_id = conn.id
    -- Exclude empty serial numbers from join to avoid false matches
    LEFT JOIN "Source"."Controllers" ctrl 
        ON chr.controller_serial_number = ctrl.serial_number
        AND NULLIF(TRIM(chr.controller_serial_number), '') IS NOT NULL
    -- Charger product lookup for vendor-specified max current
    LEFT JOIN "Mapping"."ChargerProductLookup" cpl
        ON cpl.charger_product_lower = LOWER(chr.charger_product)
),

-- ============================================================================
-- CTE 2: with_charger_mapping — derive charger phase mapping from N/L1/L2/L3
-- ============================================================================
with_charger_mapping AS (
    SELECT
        rd.*,
        CASE
            -- ================================================================
            -- IT/TT pattern: N and L1 populated with distinct grid phases, L2/L3 empty
            -- Charger N connects to one grid phase, Charger L1 (live) to another
            -- ================================================================
            WHEN NULLIF(TRIM(COALESCE(rd.charger_n, '')), '') IS NOT NULL
                 AND rd.charger_n IN ('L1','L2','L3')
                 AND NULLIF(TRIM(COALESCE(rd.charger_l1, '')), '') IS NOT NULL
                 AND rd.charger_l1 IN ('L1','L2','L3')
                 AND rd.charger_n != rd.charger_l1
                 AND COALESCE(NULLIF(TRIM(rd.charger_l2), ''), '') = ''
                 AND COALESCE(NULLIF(TRIM(rd.charger_l3), ''), '') = ''
            THEN
                'x' ||
                CASE WHEN rd.charger_n = 'L1' THEN 'N' WHEN rd.charger_l1 = 'L1' THEN 'L' ELSE 'x' END ||
                CASE WHEN rd.charger_n = 'L2' THEN 'N' WHEN rd.charger_l1 = 'L2' THEN 'L' ELSE 'x' END ||
                CASE WHEN rd.charger_n = 'L3' THEN 'N' WHEN rd.charger_l1 = 'L3' THEN 'L' ELSE 'x' END

            -- ================================================================
            -- TN 3-phase: L1, L2, L3 all populated with distinct grid phases, N empty
            -- Each charger phase connects to a (possibly rotated) grid phase
            -- ================================================================
            WHEN COALESCE(NULLIF(TRIM(rd.charger_n), ''), '') = ''
                 AND rd.charger_l1 IN ('L1','L2','L3')
                 AND rd.charger_l2 IN ('L1','L2','L3')
                 AND rd.charger_l3 IN ('L1','L2','L3')
                 AND rd.charger_l1 != rd.charger_l2
                 AND rd.charger_l1 != rd.charger_l3
                 AND rd.charger_l2 != rd.charger_l3
            THEN
                'N' ||
                CASE WHEN rd.charger_l1 = 'L1' THEN '1' WHEN rd.charger_l2 = 'L1' THEN '2' WHEN rd.charger_l3 = 'L1' THEN '3' END ||
                CASE WHEN rd.charger_l1 = 'L2' THEN '1' WHEN rd.charger_l2 = 'L2' THEN '2' WHEN rd.charger_l3 = 'L2' THEN '3' END ||
                CASE WHEN rd.charger_l1 = 'L3' THEN '1' WHEN rd.charger_l2 = 'L3' THEN '2' WHEN rd.charger_l3 = 'L3' THEN '3' END

            -- ================================================================
            -- TN 1-phase: only L1 populated with a grid phase, N/L2/L3 empty
            -- Charger's single live wire connects to one grid phase
            -- ================================================================
            WHEN COALESCE(NULLIF(TRIM(rd.charger_n), ''), '') = ''
                 AND rd.charger_l1 IN ('L1','L2','L3')
                 AND COALESCE(NULLIF(TRIM(rd.charger_l2), ''), '') = ''
                 AND COALESCE(NULLIF(TRIM(rd.charger_l3), ''), '') = ''
            THEN
                'N' ||
                CASE WHEN rd.charger_l1 = 'L1' THEN '1' ELSE 'x' END ||
                CASE WHEN rd.charger_l1 = 'L2' THEN '1' ELSE 'x' END ||
                CASE WHEN rd.charger_l1 = 'L3' THEN '1' ELSE 'x' END

            -- All other combinations are invalid → NULL
            ELSE NULL
        END AS charger_phase_mapping_derived
    FROM raw_data rd
),

-- ============================================================================
-- CTE 3: with_derived — electrical configuration and charger-level flags
-- ============================================================================
with_derived AS (
    SELECT 
        cm.*,
        -- Target electrical configuration for charge point
        CASE
            WHEN cm.connector_power_type = 'DC' THEN 'star'
            WHEN cm.controller_nettype = 'TN' THEN 'star'
            WHEN cm.controller_nettype IN ('IT', 'TT') THEN 'delta'
            WHEN cm.controller_nettype = '' AND cm.connector_power_type = 'AC_3_PHASE' THEN 'star'
            ELSE 'star'
        END AS target_chargepoint_electrical_configuration,
        CASE
            WHEN cm.connector_power_type = 'DC' THEN FALSE
            WHEN cm.controller_nettype = 'TN' THEN FALSE
            WHEN cm.controller_nettype IN ('IT', 'TT') THEN FALSE
            WHEN cm.controller_nettype = '' AND cm.connector_power_type = 'AC_3_PHASE' THEN FALSE
            ELSE TRUE
        END AS has_unknown_electrical_configuration,
        -- Invalid phase mapping for IT/TT: should start with 'x' for delta networks
        CASE
            WHEN cm.controller_nettype IN ('IT', 'TT') 
                 AND NULLIF(TRIM(COALESCE(cm.connector_phase_mapping, '')), '') IS NOT NULL
                 AND LEFT(UPPER(cm.connector_phase_mapping), 1) != 'X'
            THEN TRUE
            ELSE FALSE
        END AS has_invalid_phase_mapping_for_it_tt,
        -- Phase mapping without neutral on TN: should start with 'N'
        CASE
            WHEN cm.controller_nettype = 'TN'
                 AND NULLIF(TRIM(COALESCE(cm.connector_phase_mapping, '')), '') IS NOT NULL
                 AND LEFT(UPPER(cm.connector_phase_mapping), 1) != 'N'
            THEN TRUE
            ELSE FALSE
        END AS has_phase_mapping_without_neutral_on_tn,
        -- Charger has phase columns populated but they form an invalid combination
        CASE
            WHEN cm.charger_columns_populated > 0
                 AND cm.charger_phase_mapping_derived IS NULL
            THEN TRUE
            ELSE FALSE
        END AS has_invalid_charger_phases,
        -- Charger-derived phase mapping disagrees with connector phase mapping
        -- For IT/TT (delta) patterns where both start with 'x', allow N/L swap
        -- since AC is bidirectional and swapping N↔L has no practical effect
        CASE
            WHEN cm.charger_phase_mapping_derived IS NOT NULL
                 AND NULLIF(TRIM(COALESCE(cm.connector_phase_mapping, '')), '') IS NOT NULL
                 AND UPPER(cm.charger_phase_mapping_derived) != UPPER(cm.connector_phase_mapping)
                 -- Exclude N/L swaps on delta patterns (same two phases, just N↔L flipped)
                 AND NOT (
                     LEFT(UPPER(cm.charger_phase_mapping_derived), 1) = 'X'
                     AND LEFT(UPPER(cm.connector_phase_mapping), 1) = 'X'
                     AND REPLACE(REPLACE(UPPER(cm.charger_phase_mapping_derived), 'N', '*'), 'L', '*')
                       = REPLACE(REPLACE(UPPER(cm.connector_phase_mapping), 'N', '*'), 'L', '*')
                 )
            THEN TRUE
            ELSE FALSE
        END AS has_charger_connector_phase_mismatch
    FROM with_charger_mapping cm
),

-- ============================================================================
-- CTE 4: with_phases — derive phases (single/three) and phases flags
-- ============================================================================
with_phases AS (
    SELECT 
        wd.*,
        CASE
            WHEN wd.connector_power_type = 'DC' THEN 'three_phase'
            WHEN wd.target_chargepoint_electrical_configuration = 'delta' THEN 'single_phase'
            WHEN wd.target_chargepoint_electrical_configuration = 'star'
                 AND UPPER(wd.connector_phase_mapping) ~ '^N[123X]{3}$'
                 AND UPPER(wd.connector_phase_mapping) ~ 'X'
            THEN 'single_phase'
            -- Power type authoritative: AC_1_PHASE with full rotation format → single_phase
            -- The N[123]{3} format encodes rotation info even for single-phase chargers;
            -- the first digit after N tells which grid phase charger L1 is on.
            WHEN wd.target_chargepoint_electrical_configuration = 'star'
                 AND UPPER(wd.connector_phase_mapping) ~ '^N[123]{3}$'
                 AND wd.connector_power_type = 'AC_1_PHASE'
            THEN 'single_phase'
            WHEN wd.target_chargepoint_electrical_configuration = 'star'
                 AND UPPER(wd.connector_phase_mapping) ~ '^N[123]{3}$'
            THEN 'three_phase'
            WHEN wd.target_chargepoint_electrical_configuration = 'star'
                 AND (NULLIF(TRIM(COALESCE(wd.connector_phase_mapping, '')), '') IS NULL
                      OR LEFT(UPPER(wd.connector_phase_mapping), 1) NOT IN ('N', 'X'))
                 AND wd.connector_power_type = 'AC_1_PHASE'
            THEN 'single_phase'
            WHEN wd.target_chargepoint_electrical_configuration = 'star'
                 AND (NULLIF(TRIM(COALESCE(wd.connector_phase_mapping, '')), '') IS NULL
                      OR LEFT(UPPER(wd.connector_phase_mapping), 1) NOT IN ('N', 'X'))
                 AND wd.connector_power_type = 'AC_3_PHASE'
            THEN 'three_phase'
            ELSE 'three_phase'
        END AS target_chargepoint_phases,
        CASE
            WHEN wd.connector_power_type = 'DC' THEN FALSE
            WHEN wd.target_chargepoint_electrical_configuration = 'delta' THEN FALSE
            WHEN wd.target_chargepoint_electrical_configuration = 'star'
                 AND UPPER(wd.connector_phase_mapping) ~ '^N[123X]{3}$'
                 AND UPPER(wd.connector_phase_mapping) ~ 'X'
            THEN FALSE
            -- Matches new single_phase branch: known from power_type + mapping
            WHEN wd.target_chargepoint_electrical_configuration = 'star'
                 AND UPPER(wd.connector_phase_mapping) ~ '^N[123]{3}$'
                 AND wd.connector_power_type = 'AC_1_PHASE'
            THEN FALSE
            WHEN wd.target_chargepoint_electrical_configuration = 'star'
                 AND UPPER(wd.connector_phase_mapping) ~ '^N[123]{3}$'
            THEN FALSE
            WHEN wd.target_chargepoint_electrical_configuration = 'star'
                 AND (NULLIF(TRIM(COALESCE(wd.connector_phase_mapping, '')), '') IS NULL
                      OR LEFT(UPPER(wd.connector_phase_mapping), 1) NOT IN ('N', 'X'))
                 AND wd.connector_power_type IN ('AC_1_PHASE', 'AC_3_PHASE')
            THEN TRUE
            ELSE TRUE
        END AS has_unknown_phases,
        -- NEW: IT/TT connector has AC_3_PHASE power type (overridden to single_phase by delta rule)
        CASE
            WHEN wd.target_chargepoint_electrical_configuration = 'delta'
                 AND wd.connector_power_type = 'AC_3_PHASE'
            THEN TRUE
            ELSE FALSE
        END AS has_power_type_override_on_it_tt
    FROM with_derived wd
),

-- ============================================================================
-- CTE 5: with_voltage_and_rotation — voltage, phase rotation, connected phase
-- ============================================================================
with_voltage_and_rotation AS (
    SELECT 
        wp.*,
        CASE
            WHEN wp.target_chargepoint_electrical_configuration = 'delta' THEN '230'
            WHEN wp.target_chargepoint_electrical_configuration = 'star' 
                 AND wp.target_chargepoint_phases = 'single_phase' THEN '220-240'
            WHEN wp.target_chargepoint_electrical_configuration = 'star' 
                 AND wp.target_chargepoint_phases = 'three_phase' THEN '400'
            ELSE '400'
        END AS target_chargepoint_max_voltage,
        -- Phase rotation for three-phase charge points
        -- Maps 4-char notation (N + position of L1,L2,L3) to RST notation
        -- R=L1, S=L2, T=L3. Only connector_phase_mapping is used (DD#4).
        CASE
            -- 4-char format: N followed by three digits
            WHEN UPPER(wp.connector_phase_mapping) = 'N123' THEN 'RST'
            WHEN UPPER(wp.connector_phase_mapping) = 'N132' THEN 'RTS'
            WHEN UPPER(wp.connector_phase_mapping) = 'N213' THEN 'SRT'
            WHEN UPPER(wp.connector_phase_mapping) = 'N231' THEN 'STR'
            WHEN UPPER(wp.connector_phase_mapping) = 'N312' THEN 'TRS'
            WHEN UPPER(wp.connector_phase_mapping) = 'N321' THEN 'TSR'
            -- Legacy hyphen format: L_-L_-L_-N
            WHEN UPPER(wp.connector_phase_mapping) = 'L1-L2-L3-N' THEN 'RST'
            WHEN UPPER(wp.connector_phase_mapping) = 'L1-L3-L2-N' THEN 'RTS'
            WHEN UPPER(wp.connector_phase_mapping) = 'L2-L1-L3-N' THEN 'SRT'
            WHEN UPPER(wp.connector_phase_mapping) = 'L2-L3-L1-N' THEN 'STR'
            WHEN UPPER(wp.connector_phase_mapping) = 'L3-L1-L2-N' THEN 'TRS'
            WHEN UPPER(wp.connector_phase_mapping) = 'L3-L2-L1-N' THEN 'TSR'
            -- All other formats (empty, single-phase Nxx1, delta xNLx, etc.) → RST default
            ELSE 'RST'
        END AS target_chargepoint_phase_rotation,
        -- Connected phase: active line conductors used in the circuit
        -- NULL for three_phase; L1/L2/L3 for star single_phase; L1_L2 for delta single_phase
        CASE
            WHEN wp.target_chargepoint_phases = 'three_phase' THEN NULL
            -- Star single-phase: derive from connector phase mapping position (explicit format)
            WHEN wp.target_chargepoint_electrical_configuration = 'star'
                 AND wp.target_chargepoint_phases = 'single_phase'
                 AND UPPER(wp.connector_phase_mapping) = 'N1XX' THEN 'L1'
            WHEN wp.target_chargepoint_electrical_configuration = 'star'
                 AND wp.target_chargepoint_phases = 'single_phase'
                 AND UPPER(wp.connector_phase_mapping) = 'NX1X' THEN 'L2'
            WHEN wp.target_chargepoint_electrical_configuration = 'star'
                 AND wp.target_chargepoint_phases = 'single_phase'
                 AND UPPER(wp.connector_phase_mapping) = 'NXX1' THEN 'L3'
            -- Star single-phase: full rotation format N[123]{3}
            -- First digit after N = grid phase number that charger L1 is connected to.
            -- Consistent with rotation table: N312→TRS means T(L3) on charger L1.
            WHEN wp.target_chargepoint_electrical_configuration = 'star'
                 AND wp.target_chargepoint_phases = 'single_phase'
                 AND UPPER(wp.connector_phase_mapping) ~ '^N[123]{3}$'
                 AND SUBSTR(UPPER(wp.connector_phase_mapping), 2, 1) = '1' THEN 'L1'
            WHEN wp.target_chargepoint_electrical_configuration = 'star'
                 AND wp.target_chargepoint_phases = 'single_phase'
                 AND UPPER(wp.connector_phase_mapping) ~ '^N[123]{3}$'
                 AND SUBSTR(UPPER(wp.connector_phase_mapping), 2, 1) = '2' THEN 'L2'
            WHEN wp.target_chargepoint_electrical_configuration = 'star'
                 AND wp.target_chargepoint_phases = 'single_phase'
                 AND UPPER(wp.connector_phase_mapping) ~ '^N[123]{3}$'
                 AND SUBSTR(UPPER(wp.connector_phase_mapping), 2, 1) = '3' THEN 'L3'
            -- Star single-phase: unknown mapping → default L1
            WHEN wp.target_chargepoint_electrical_configuration = 'star'
                 AND wp.target_chargepoint_phases = 'single_phase' THEN 'L1'
            -- Delta single-phase: positions 2+3 active → L1_L2
            WHEN wp.target_chargepoint_electrical_configuration = 'delta'
                 AND wp.target_chargepoint_phases = 'single_phase'
                 AND UPPER(wp.connector_phase_mapping) IN ('XNLX', 'XLNX') THEN 'L1_L2'
            -- Delta single-phase: positions 2+4 active → L1_L3
            WHEN wp.target_chargepoint_electrical_configuration = 'delta'
                 AND wp.target_chargepoint_phases = 'single_phase'
                 AND UPPER(wp.connector_phase_mapping) IN ('XNXL', 'XLXN') THEN 'L1_L3'
            -- Delta single-phase: positions 3+4 active → L2_L3
            WHEN wp.target_chargepoint_electrical_configuration = 'delta'
                 AND wp.target_chargepoint_phases = 'single_phase'
                 AND UPPER(wp.connector_phase_mapping) IN ('XXNL', 'XXLN') THEN 'L2_L3'
            -- Delta single-phase: unknown mapping → default L1_L2
            WHEN wp.target_chargepoint_electrical_configuration = 'delta'
                 AND wp.target_chargepoint_phases = 'single_phase' THEN 'L1_L2'
            ELSE NULL
        END AS target_chargepoint_connected_phase,
        -- Quality flag: connected phase was set to a default value
        CASE
            WHEN wp.target_chargepoint_phases = 'three_phase' THEN FALSE
            WHEN wp.target_chargepoint_electrical_configuration = 'star'
                 AND wp.target_chargepoint_phases = 'single_phase'
                 AND UPPER(wp.connector_phase_mapping) IN ('N1XX', 'NX1X', 'NXX1') THEN FALSE
            -- Full rotation format: connected phase derivable from first digit
            WHEN wp.target_chargepoint_electrical_configuration = 'star'
                 AND wp.target_chargepoint_phases = 'single_phase'
                 AND UPPER(wp.connector_phase_mapping) ~ '^N[123]{3}$' THEN FALSE
            WHEN wp.target_chargepoint_electrical_configuration = 'delta'
                 AND wp.target_chargepoint_phases = 'single_phase'
                 AND UPPER(wp.connector_phase_mapping) IN ('XLNX', 'XNLX', 'XLXN', 'XNXL', 'XXLN', 'XXNL') THEN FALSE
            -- All other single-phase cases used a default value
            WHEN wp.target_chargepoint_phases = 'single_phase' THEN TRUE
            ELSE FALSE
        END AS has_default_value_on_connected_phase,
        -- NEW: fallback phase rotation — three-phase connector that fell back to RST default
        -- because connector_phase_mapping is not in a recognized 3-phase pattern
        CASE
            WHEN wp.target_chargepoint_phases = 'three_phase'
                 AND UPPER(COALESCE(wp.connector_phase_mapping, '')) NOT IN (
                     'N123', 'N132', 'N213', 'N231', 'N312', 'N321',
                     'L1-L2-L3-N', 'L1-L3-L2-N', 'L2-L1-L3-N', 'L2-L3-L1-N', 'L3-L1-L2-N', 'L3-L2-L1-N'
                 )
            THEN TRUE
            ELSE FALSE
        END AS has_fallback_phase_rotation
    FROM with_phases wp
),

-- ============================================================================
-- CTE 6: with_evse — EVSE target values (overrides for delta API workarounds)
-- ============================================================================
with_evse AS (
    SELECT
        wv.*,
        -- ================================================================
        -- Target EVSE: shares base logic with Charge Point columns.
        -- API WORKAROUND: EVSE API does not yet support delta configuration.
        -- Delta values are overridden where the API cannot represent them.
        -- ================================================================

        -- EVSE phases: identical to charge point (no override needed)
        wv.target_chargepoint_phases AS target_evse_phases,

        -- API WORKAROUND: EVSE API does not yet support delta configuration.
        -- Use '220-240' (single-phase range) instead of '230' (delta-specific)
        CASE
            WHEN wv.target_chargepoint_electrical_configuration = 'delta' THEN '220-240'
            ELSE wv.target_chargepoint_max_voltage
        END AS target_evse_max_voltage,

        -- API WORKAROUND: EVSE API does not yet support delta configuration.
        -- L1_L2/L1_L3/L2_L3 variants are not available; override to 'L1'
        CASE
            WHEN wv.target_chargepoint_electrical_configuration = 'delta' THEN 'L1'
            ELSE wv.target_chargepoint_connected_phase
        END AS target_evse_connected_phase,

        -- EVSE max amperage: from connector source, with calculated fallback
        -- Fallback uses derived phases (not source voltage which is inconsistent):
        --   single_phase → P(kW)*1000 / 230  (assume 230V regardless of star/delta)
        --   three_phase  → P(kW)*1000 / (400 * sqrt(3))  (400V phase-to-phase)
        CASE
            WHEN wv.connector_amperage IS NOT NULL AND wv.connector_amperage > 0
            THEN wv.connector_amperage
            -- Fallback: calculate from max_effect (kW) using derived phases
            WHEN NULLIF(TRIM(COALESCE(wv.connector_max_effect, '')), '') IS NOT NULL
                 AND TRIM(wv.connector_max_effect) ~ '^[0-9]+[.]?[0-9]*$'
                 AND wv.connector_max_effect::numeric > 0
            THEN CASE
                WHEN wv.target_chargepoint_phases = 'three_phase'
                THEN ROUND(wv.connector_max_effect::numeric * 1000 / (400 * SQRT(3)))::int
                ELSE ROUND(wv.connector_max_effect::numeric * 1000 / 230)::int
            END
            -- Ultimate fallback
            ELSE 16
        END AS target_evse_max_amperage,

        -- EVSE max power in W (source max_effect is in kW)
        CASE
            WHEN NULLIF(TRIM(COALESCE(wv.connector_max_effect, '')), '') IS NOT NULL
                 AND TRIM(wv.connector_max_effect) ~ '^[0-9]+[.]?[0-9]*$'
                 AND wv.connector_max_effect::numeric > 0
            THEN ROUND(wv.connector_max_effect::numeric * 1000)::int
            ELSE NULL
        END AS target_evse_max_power,

        -- Quality flag: connector is missing amperage (null or 0)
        CASE
            WHEN wv.connector_amperage IS NULL OR wv.connector_amperage = 0 THEN TRUE
            ELSE FALSE
        END AS has_missing_current_on_connector,

        -- Quality flag: amperage and max_effect imply different phase configuration
        -- Computes implied voltage = kW * 1000 / A and compares to expected range:
        --   single_phase (star or delta): expect 200-250V (~230V)
        --   three_phase:                  expect 600-750V (~693V = sqrt(3)*400)
        -- Delta networks are checked too: 230V line-to-line limits real-world
        -- delivery, so 22kW @ 32A on delta is physically implausible.
        CASE
            WHEN wv.connector_power_type = 'DC' THEN FALSE
            WHEN wv.connector_amperage IS NULL OR wv.connector_amperage = 0 THEN FALSE
            WHEN NULLIF(TRIM(COALESCE(wv.connector_max_effect, '')), '') IS NULL THEN FALSE
            WHEN TRIM(wv.connector_max_effect) !~ '^[0-9]+[.]?[0-9]*$' THEN FALSE
            WHEN wv.connector_max_effect::numeric = 0 THEN FALSE
            -- Star single-phase: expect ~230V (200-250 range)
            WHEN wv.target_chargepoint_phases = 'single_phase'
                 AND wv.target_chargepoint_electrical_configuration = 'star'
                 AND (wv.connector_max_effect::numeric * 1000 / wv.connector_amperage)::int NOT BETWEEN 200 AND 250
            THEN TRUE
            -- Three-phase: expect ~693V (600-750 range)
            WHEN wv.target_chargepoint_phases = 'three_phase'
                 AND (wv.connector_max_effect::numeric * 1000 / wv.connector_amperage)::int NOT BETWEEN 600 AND 750
            THEN TRUE
            -- Delta single-phase: expect ~230V (limited to 230V line-to-line)
            WHEN wv.target_chargepoint_phases = 'single_phase'
                 AND wv.target_chargepoint_electrical_configuration = 'delta'
                 AND (wv.connector_max_effect::numeric * 1000 / wv.connector_amperage)::int NOT BETWEEN 200 AND 250
            THEN TRUE
            ELSE FALSE
        END AS has_power_effect_phase_mismatch
    FROM with_voltage_and_rotation wv
),

-- ============================================================================
-- CTE 7: mixed_rotation_chargers — detect chargers needing EVSE-level rotation
-- ============================================================================
-- Chargers with multiple three-phase connectors that have different phase
-- rotations. These need rotation on EVSE level instead of CP level.
-- PostgreSQL does not support COUNT(DISTINCT) as a window function,
-- so we compute this as a grouped subquery.
mixed_rotation_chargers AS (
    SELECT charger_id
    FROM with_evse
    WHERE target_chargepoint_phases = 'three_phase'
    GROUP BY charger_id
    HAVING COUNT(DISTINCT target_chargepoint_phase_rotation) > 1
),

-- ============================================================================
-- CTE 8: with_flags_and_metadata — new columns not in 098
-- ============================================================================
with_flags_and_metadata AS (
    SELECT
        we.*,
        -- is_first_connector: TRUE for the first connector per charger (by connector_level)
        -- Used by 112 Target.ChargePoints to pick one connector per charger
        (ROW_NUMBER() OVER (PARTITION BY we.charger_id ORDER BY we.connector_level) = 1)
            AS is_first_connector,
        -- CP default max current: vendor lookup → connector amperage → 32A fallback
        COALESCE(we.max_charger_current, we.connector_amperage, 32)
            AS target_cp_default_max_current,
        -- Duplicate identity: charger_name + connector_level is not globally unique
        CASE
            WHEN COUNT(*) OVER (PARTITION BY we.charger_name, we.connector_level) > 1 THEN TRUE
            ELSE FALSE
        END AS has_duplicate_connector_identity,
        -- Auto-detect chargers with mixed phase rotation across three-phase connectors
        CASE
            WHEN mrc.charger_id IS NOT NULL THEN TRUE
            ELSE FALSE
        END AS has_mixed_phase_rotation
    FROM with_evse we
    LEFT JOIN mixed_rotation_chargers mrc ON mrc.charger_id = we.charger_id
)

-- ============================================================================
-- Final SELECT with logical column ordering
-- ============================================================================
SELECT
    -- === Primary key & metadata ===
    charger_id,
    connector_level,
    is_first_connector,
    charger_name,
    charger_status,
    connector_status_reason,

    -- === Raw source: Controller ===
    controller_nettype,

    -- === Raw source: Charger ===
    charger_n,
    charger_l1,
    charger_l2,
    charger_l3,

    -- === Raw source: Connector ===
    connector_power_type,
    connector_phase_mapping,
    connector_amperage,
    connector_max_effect,

    -- === Lookup: Charger Product ===
    max_charger_current,

    -- === Intermediate ===
    charger_phase_mapping_derived,

    -- === Target: Charge Point ===
    target_chargepoint_electrical_configuration,
    target_chargepoint_phases,
    target_chargepoint_max_voltage,
    -- Phase rotation level selection (DD#9):
    -- Default: rotation on CP level, RST on EVSE.
    -- Exception: chargers with mixed three-phase rotation across connectors
    -- get RST on CP, per-connector rotation on EVSE.
    CASE
        WHEN has_mixed_phase_rotation THEN 'RST'
        ELSE target_chargepoint_phase_rotation
    END AS target_cp_phase_rotation,
    target_chargepoint_connected_phase,
    target_cp_default_max_current,

    -- === Target: EVSE ===
    target_evse_phases,
    target_evse_max_voltage,
    -- Phase rotation level selection (DD#9): see CP comment above
    CASE
        WHEN has_mixed_phase_rotation THEN target_chargepoint_phase_rotation
        ELSE 'RST'
    END AS target_evse_phase_rotation,
    target_evse_connected_phase,
    target_evse_max_amperage,
    target_evse_max_power,

    -- === Quality flags (12 total) ===
    -- Charger-level flags (use with is_first_connector for reports)
    has_unknown_electrical_configuration,     -- WARNING: no controller nettype, fallback to star
    has_default_value_on_connected_phase,     -- WARNING: connected phase set to default
    has_unknown_phases,                       -- WARNING: phases derived from power_type fallback
    has_invalid_charger_phases,               -- WARNING: charger N/L1/L2/L3 form invalid combo
    has_fallback_phase_rotation,              -- WARNING: three-phase rotation fell back to RST

    -- Connector-level flags (all rows)
    has_missing_current_on_connector,         -- WARNING: connector amperage is null or 0
    has_charger_connector_phase_mismatch,     -- WARNING: charger vs connector phase mapping disagree
    has_power_effect_phase_mismatch,          -- WARNING: kW/A ratio disagrees with derived phases
    has_invalid_phase_mapping_for_it_tt,      -- ERROR: IT/TT connector phase_mapping doesn't start with 'x'
    has_phase_mapping_without_neutral_on_tn,  -- ERROR: TN connector phase_mapping doesn't start with 'N'
    has_duplicate_connector_identity,         -- ERROR: charger_name + connector_level not unique
    has_power_type_override_on_it_tt,         -- INFO: delta connector has AC_3_PHASE power type

    -- Cross-validation column: base derived rotation before level selection
    -- Compare this against 098's target_chargepoint_phase_rotation (not the level-selected values)
    target_chargepoint_phase_rotation

FROM with_flags_and_metadata
ORDER BY charger_name, connector_level;

COMMENT ON VIEW "Source"."ElectricalSettingsNormalized" IS 
'Intermediate view for electrical settings normalization. One row per connector on charger (no location or status filter). Passes through charger_status and connector_status_reason. Business rules from ElectricalSettingsAnalysis (098). Fixes TT→delta bug. Phase B rewrite 2026-02-08.';
