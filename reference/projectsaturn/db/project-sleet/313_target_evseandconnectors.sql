SET ROLE db_sleetmigration_owner;

-- Phase 2: Target.EvseAndConnectors view for Project Sleet
-- Maps Source.Connectors to Ampeco EVSE/Connector API payload format
-- 
-- Join path:
--   Source.Connectors conn -> Source.Chargers c ON conn.id = c.charger_id
--   Source.Chargers c -> Source.Locations loc ON c.location_nr = loc.location_nr
--   Source.Locations loc -> Mapping.location_mapping lm ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
--   Source.Connectors conn -> Mapping.connector_mapping conm ON 'Sleet|Connector|' || conn."Id"::TEXT = conm.mapping_key
--   Source.Chargers c -> Mapping.charger_mapping cm ON 'Sleet|Charger|' || c."Id"::TEXT = cm.mapping_key
--   Source.EvseId ei -> composite key (charger_id, connector_level) for physical reference lookup
--   Mapping.ChargerProductLookup cpl -> case-insensitive charger_product for schuko detection
--   Source.ElectricalSettingsNormalized esn -> (charger_id, connector_level) for electrical settings
--   Source.EvseTariffGroupAssignment etga -> connector_guid for pricing-derived tariff group
--   Mapping.tariff_group_mapping tgm -> etga.mapping_key for target tariff group ID
--
-- All EVSE electrical settings (phases, voltage, connected phase, amperage, power,
-- phase rotation) are pure passthroughs from Source.ElectricalSettingsNormalized (099)
-- which centralizes all business logic including API workarounds for delta networks
-- and mixed-rotation detection (DD#9: chargers with multiple three-phase connectors
-- having different rotations apply rotation at EVSE level instead of Charge Point level).

DROP VIEW IF EXISTS "Target"."EvseAndConnectors";

CREATE OR REPLACE VIEW "Target"."EvseAndConnectors" AS
WITH connectors_in_scope AS (
    -- CTE 1: Get all connectors for chargers at locations marked for migration
    -- Note: conn.id is the FK to Charger.charger_id (per SharePoint import naming)
    SELECT 
        conn."Id" AS connector_guid,
        conn.id AS charger_id,
        conn.connector_level,
        conn.connector_type AS source_connector_type,
        conn.power_type,
        conn.amperage_connector,
        conn.max_effect,
        conn.voltage_connector,
        conn.phase_mapping,
        conn.sms_code,
        conn.status_reason AS connector_status,
        c."Id" AS charger_guid,
        c.charger_name,
        c.location_nr,
        c.parking_spot,
        c.charger_product,
        c.controller_serial_number,
        lm.project_code,
        lm.target_location_id,
        lm.target_tariff_group_id,
        c.status AS charger_status,
        -- Count connectors per charger for label logic
        COUNT(*) OVER (PARTITION BY conn.id) AS connector_count
    FROM "Source"."Connectors" conn
    JOIN "Source"."Chargers" c ON c.charger_id = conn.id
    JOIN "Source"."Locations" loc ON loc.location_nr = c.location_nr
    JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    WHERE lm.migrate = TRUE
),
with_mappings AS (
    -- CTE 2: Join with mappings, EvseId lookup, ChargerProductLookup, and ElectricalSettingsNormalized
    SELECT 
        cis.*,
        'Sleet|Connector|' || cis.connector_guid::TEXT AS mapping_key,
        cm.target_charge_point_id,
        conm.target_evse_id,
        conm.target_connector_id,
        conm.physical_reference AS mapped_physical_reference,
        ei."Id" AS evse_id_from_planning,
        cpl.has_schuko,
        cpl.schuko_connector_level,
        -- Electrical settings from the normalized intermediate view (connector grain)
        -- All EVSE electrical settings are pure passthroughs from ESN (099)
        esn.target_evse_phases AS phases,
        esn.target_evse_max_voltage AS max_voltage_evse,
        esn.target_evse_connected_phase AS connected_phase_evse,
        esn.target_evse_max_amperage,
        esn.target_evse_max_power,
        esn.target_evse_phase_rotation,
        tgm.target_tariff_group_id AS tgm_target_tariff_group_id
    FROM connectors_in_scope cis
    LEFT JOIN "Mapping"."charger_mapping" cm ON cm.mapping_key = 'Sleet|Charger|' || cis.charger_guid::TEXT
    LEFT JOIN "Mapping"."connector_mapping" conm ON conm.mapping_key = 'Sleet|Connector|' || cis.connector_guid::TEXT
    LEFT JOIN "Source"."EvseId" ei ON ei.charger_id = cis.charger_id AND ei.connector_level = cis.connector_level
    LEFT JOIN "Mapping"."ChargerProductLookup" cpl ON cpl.charger_product_lower = LOWER(cis.charger_product)
    LEFT JOIN "Source"."ElectricalSettingsNormalized" esn 
        ON esn.charger_id = cis.charger_id AND esn.connector_level = cis.connector_level
    -- Tariff group assignment: pricing-derived path (Phase 3)
    LEFT JOIN "Source"."EvseTariffGroupAssignment" etga
        ON etga.connector_guid = cis.connector_guid
    LEFT JOIN "Mapping"."tariff_group_mapping" tgm
        ON tgm.mapping_key = etga.mapping_key
),
-- CTE 3: De-duplicate connectors sharing the same charger_id + connector_level.
-- When an active and inactive connector occupy the same slot, only the active
-- one may be sent to the target CSMS (networkId must be unique per charge point).
-- ROW_NUMBER prioritizes Active over Inactive; ties broken by connector_guid.
with_dedup AS (
    SELECT
        wm.*,
        ROW_NUMBER() OVER (
            PARTITION BY wm.charger_id, wm.connector_level
            ORDER BY
                CASE WHEN wm.connector_status = 'Active' THEN 0 ELSE 1 END,
                CASE WHEN wm.charger_status = 'Active' THEN 0 ELSE 1 END,
                wm.connector_guid
        ) AS _rn
    FROM with_mappings wm
)
-- Final SELECT: Map all columns per Ampeco EVSE/Connector API specification
-- Reads from with_dedup to skip inactive connectors that conflict with active
-- ones on the same charger_id + connector_level (networkId must be unique).
SELECT 
    -- Mapping columns
    'connector_mapping'::TEXT AS mapping_table,
    wm.mapping_key,
    NULL::TEXT AS merge_with_mapping_key,
    
    -- Target IDs
    wm.target_charge_point_id AS "TargetChargePointID",
    wm.target_evse_id AS "TargetEvseID",
    wm.target_connector_id AS "TargetConnectorID",
    
    -- Current type (AC/DC based on power_type)
    CASE 
        WHEN wm.power_type = 'DC' THEN 'dc'
        ELSE 'ac'  -- AC_3_PHASE, AC_1_PHASE, or default
    END AS "currentType",
    
    -- Status (disabled for inactive entities or schuko EVSEs)
    -- Note: Some chargers use 11/12 instead of 1/2, so we use modulo 10 for comparison
    CASE 
        WHEN wm.connector_status = 'Inactive' THEN 'disabled'
        WHEN wm.charger_status = 'Inactive' THEN 'disabled'
        WHEN wm.has_schuko = TRUE AND (wm.connector_level % 10) = wm.schuko_connector_level THEN 'disabled'
        ELSE 'enabled'
    END AS "status",
    
    -- Physical reference (priority: EvseId planning table -> ConnectorMapping -> connector_level)
    TRIM(COALESCE(
        wm.evse_id_from_planning,
        wm.mapped_physical_reference, 
        wm.connector_level::TEXT
    )) AS "physicalReference",
    
    -- Label (parking_spot + connector_level if multi-EVSE, NULL if no parking_spot)
    CASE 
        WHEN NULLIF(TRIM(wm.parking_spot), '') IS NULL THEN NULL
        WHEN wm.connector_count > 1 THEN TRIM(wm.parking_spot) || ' ' || wm.connector_level::TEXT
        ELSE TRIM(wm.parking_spot)
    END AS "label",
    
    -- Network ID (connector_level for EVSE identification within ChargePoint)
    wm.connector_level::TEXT AS "networkId",
    
    -- MID meter certification (not applicable)
    NULL::INTEGER AS "midMeterCertificationEndYear",
    
    -- Tariff group (from pricing-derived tariff_group_mapping via EvseTariffGroupAssignment)
    wm.tgm_target_tariff_group_id AS "tariffGroupId",
    
    -- Reservation (disabled)
    FALSE AS "allowsReservation",
    
    -- Power options (from ESN: kW converted to Watts, NULL if missing/invalid)
    wm.target_evse_max_power AS "powerOptions_maxPower",
    
    -- maxVoltage (from ESN: includes API workaround for delta → '220-240')
    wm.max_voltage_evse AS "powerOptions_maxVoltage",
    
    -- maxAmperage (from ESN: 3-tier fallback — direct → calculated from kW → default 16)
    wm.target_evse_max_amperage AS "powerOptions_maxAmperage",
    
    -- Phases (from ESN: 'single_phase' or 'three_phase')
    wm.phases AS "powerOptions_phases",
    
    -- Phase rotation (from ESN: level-selected per DD#9 — 'RST' for most EVSEs,
    -- per-connector rotation for chargers with mixed three-phase rotations)
    wm.target_evse_phase_rotation AS "powerOptions_phaseRotation",
    
    -- Connected phase (from ESN: includes API workaround for delta → 'L1')
    wm.connected_phase_evse AS "powerOptions_connectedPhase",
    
    -- External ID (SMS code for traceability)
    wm.sms_code AS "externalId",
    
    -- Project code
    wm.project_code AS "ProjectCode",
    
    -- Connector type (schuko for Smart Wallbox connector_level 2 or 12, ccs2 for CCS, else type2)
    -- Note: Some chargers use 11/12 instead of 1/2, so we use modulo 10 for comparison
    CASE 
        WHEN wm.has_schuko = TRUE AND (wm.connector_level % 10) = wm.schuko_connector_level THEN 'schuko'
        WHEN wm.source_connector_type ILIKE '%CCS%' THEN 'ccs2'
        ELSE 'type2'
    END AS "connector_type",
    
    -- Connector format (cable for DC, socket for AC)
    CASE 
        WHEN wm.power_type = 'DC' THEN 'cable'
        ELSE 'socket'
    END AS "connector_format",
    
    -- Connector status (enum: enabled, disabled)
    CASE 
        WHEN wm.connector_status = 'Inactive' THEN 'disabled'
        WHEN wm.charger_status = 'Inactive' THEN 'disabled'
        ELSE 'enabled'
    END::TEXT AS "connector_status",
    
    -- Source IDs for debugging/mapping (snake_case for Sleet detection in update_mapping)
    wm.connector_guid::TEXT AS "source_connector_guid",
    wm.charger_id AS "source_charger_id",
    wm.connector_level AS "connector_level"

FROM with_dedup wm
WHERE wm._rn = 1;
