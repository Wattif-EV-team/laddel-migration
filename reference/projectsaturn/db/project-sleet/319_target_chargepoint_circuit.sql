-- ============================================================================
-- TARGET VIEW: Target.ChargePointCircuitAttachment
-- ============================================================================
-- Represents the DESIRED STATE for all active charge points at migrated locations.
-- Used by AttachChargePointToCircuit.py to reconcile against live Ampeco state.
--
-- Join path:
--   Chargers → Controllers (LEFT JOIN via controller_serial_number) → Locations → location_mapping
--   Chargers → Clusters (optional, via name = cluster_name AND same controller_serial_number)
--   Chargers → charger_mapping (for target_charge_point_id)
--   Circuit resolution → circuit_mapping (for target_circuit_id)
--
-- Circuit resolution logic:
--   - If charger has no controller → target_circuit_id = NULL (should NOT be on a circuit)
--   - If charger has controller but no/invalid cluster → attach to controller's root circuit
--   - If charger has valid cluster reference → attach to cluster's circuit
--
-- Rows where target_circuit_id IS NULL signal "this charge point should not be attached
-- to any circuit." The Python reconciliation script uses this to detach charge points
-- that are currently attached but should not be.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Target"."ChargePointCircuitAttachment";

CREATE OR REPLACE VIEW "Target"."ChargePointCircuitAttachment" AS
WITH chargers_in_scope AS (
    -- CTE 1: Get all active chargers at locations marked for migration
    -- LEFT JOIN to Controllers so chargers without a controller still appear
    -- (with NULL controller columns → target_circuit_id will resolve to NULL)
    SELECT 
        chr."Id" AS charger_guid,
        chr.charger_id,
        chr.charger_name,
        chr.controller_serial_number,
        chr."name" AS cluster_ref,  -- This is the cluster name reference from Chargers table
        chr.location_nr,
        ctrl."Id" AS controller_guid,
        ctrl.controller_name,
        ctrl.nettype,
        lm.project_code,
        lm.target_location_id
    FROM "Source"."Chargers" chr
    LEFT JOIN "Source"."Controllers" ctrl
        ON chr.controller_serial_number = ctrl.serial_number
        AND NULLIF(TRIM(chr.controller_serial_number), '') IS NOT NULL
    JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
    JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    WHERE lm.migrate = TRUE
),
with_circuit_resolution AS (
    -- CTE 2: Resolve charger to appropriate circuit
    -- If cluster_ref matches a valid cluster → use cluster's circuit
    -- Otherwise → use controller's root circuit
    SELECT 
        cis.*,
        clus."Id" AS cluster_guid,
        clus.cluster_name,
        CASE 
            -- Valid cluster reference found → use cluster circuit
            WHEN clus."Id" IS NOT NULL 
            THEN 'Sleet|Cluster|' || clus."Id"::TEXT
            -- No valid cluster → fallback to controller root circuit
            ELSE 'Sleet|Controller|' || cis.controller_guid::TEXT
        END AS resolved_circuit_mapping_key
    FROM chargers_in_scope cis
    LEFT JOIN "Source"."Clusters" clus 
        ON cis.cluster_ref = clus.cluster_name 
        AND cis.controller_serial_number = clus.controller_serial_number
)
-- Final SELECT: Only columns needed for circuit attachment API
-- Smart charging configuration is now handled in Target.ChargePoints
SELECT 
    -- Charger identification (for logging)
    'Sleet|Charger|' || wcr.charger_guid::TEXT AS charger_mapping_key,
    wcr.charger_name,
    
    -- Target IDs (from mapping tables)
    cm.target_charge_point_id,
    cirm.target_circuit_id,
    
    -- API payload columns (JSON camelCase)
    cm.target_charge_point_id AS "chargePointId",
    1 AS "priority",
    
    -- Circuit resolution info (for logging)
    wcr.resolved_circuit_mapping_key,
    CASE 
        WHEN wcr.cluster_guid IS NOT NULL THEN 'Cluster'
        WHEN wcr.controller_guid IS NOT NULL THEN 'Controller'
        ELSE NULL
    END AS attached_to_type,
    CASE
        WHEN wcr.cluster_guid IS NOT NULL THEN wcr.cluster_name
        WHEN wcr.controller_guid IS NOT NULL THEN wcr.controller_name || ' (Root)'
        ELSE NULL
    END AS attached_to_name,
    
    -- Context columns
    wcr.project_code,
    wcr.location_nr

FROM with_circuit_resolution wcr
LEFT JOIN "Mapping"."charger_mapping" cm 
    ON cm.mapping_key = 'Sleet|Charger|' || wcr.charger_guid::TEXT
LEFT JOIN "Mapping"."circuit_mapping" cirm 
    ON cirm.mapping_key = wcr.resolved_circuit_mapping_key;
