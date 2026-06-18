-- ============================================================================
-- TARGET VIEW: Target.Circuits
-- ============================================================================
-- Maps Source.Controllers (root circuits) and Source.Clusters (sub-circuits)
-- to Ampeco Circuit API payload format.
--
-- Join path:
--   Controllers → Locations → location_mapping (filter migrate=TRUE)
--   Clusters → Controllers (via controller_serial_number) → Locations → location_mapping
--
-- Hierarchy:
--   Root circuits: Created from Controllers (no parent)
--   Sub-circuits:  Created from Clusters (parent = resolved from parent_cluster or fallback to root)
--
-- Parent resolution logic:
--   - If parent_cluster is NULL, empty, or 'root' → parent is Controller's root circuit
--   - Else lookup sibling cluster by (controller_serial_number, parent_cluster = cluster_name)
--   - If sibling found → parent is that cluster's circuit
--   - If sibling not found → fallback to Controller's root circuit (known data quality issue)
--
-- Electrical settings (from Controller's nettype, case-insensitive):
--   - IT/TT nettype → maxVoltage=230, electricalConfiguration='delta'
--     (IT and TT networks both use delta configuration — no neutral conductor)
--   - TN/other → maxVoltage=400, electricalConfiguration='star'
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Target"."Circuits";

CREATE OR REPLACE VIEW "Target"."Circuits" AS
WITH controllers_in_scope AS (
    -- CTE 1: Get all controllers at locations marked for migration
    SELECT 
        ctrl."Id" AS controller_guid,
        ctrl.serial_number,
        ctrl.controller_name,
        ctrl.nettype,
        ctrl.fuse_size,
        ctrl.location_nr,
        lm.project_code,
        lm.target_location_id
    FROM "Source"."Controllers" ctrl
    JOIN "Source"."Locations" loc ON ctrl.location_nr = loc.location_nr
    JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    WHERE lm.migrate = TRUE
      AND ctrl.serial_number IS NOT NULL 
      AND ctrl.serial_number != ''
),
clusters_in_scope AS (
    -- CTE 2: Get all clusters for in-scope controllers
    SELECT 
        clus."Id" AS cluster_guid,
        clus.cluster_name,
        clus.controller_serial_number,
        clus.parent_cluster,
        clus."limit" AS cluster_limit,
        clus.nettype AS cluster_nettype,
        cis.controller_guid,
        cis.controller_name,
        cis.nettype AS controller_nettype,
        cis.project_code,
        cis.target_location_id,
        cis.location_nr
    FROM "Source"."Clusters" clus
    JOIN controllers_in_scope cis ON clus.controller_serial_number = cis.serial_number
),
clusters_with_parent_resolution AS (
    -- CTE 3: Resolve parent_cluster to actual parent mapping_key
    -- If parent_cluster matches a sibling cluster name → use that cluster's mapping_key
    -- Otherwise (NULL, empty, 'root', or invalid) → fallback to controller's root circuit
    SELECT 
        cis.*,
        CASE 
            -- Check if parent_cluster is NULL, empty, or 'root' → use controller root
            WHEN cis.parent_cluster IS NULL 
                 OR cis.parent_cluster = '' 
                 OR LOWER(cis.parent_cluster) = 'root'
            THEN 'Sleet|Controller|' || cis.controller_guid::TEXT
            
            -- Try to find sibling cluster with matching name
            WHEN sibling.cluster_guid IS NOT NULL 
            THEN 'Sleet|Cluster|' || sibling.cluster_guid::TEXT
            
            -- Fallback to controller root if sibling not found (data quality issue)
            ELSE 'Sleet|Controller|' || cis.controller_guid::TEXT
        END AS resolved_parent_mapping_key
    FROM clusters_in_scope cis
    LEFT JOIN clusters_in_scope sibling 
        ON cis.controller_serial_number = sibling.controller_serial_number
        AND cis.parent_cluster = sibling.cluster_name
        AND cis.cluster_guid != sibling.cluster_guid  -- Don't match self
),
-- Root circuits from Controllers
root_circuits AS (
    SELECT 
        'circuit_mapping'::TEXT AS mapping_table,
        'Sleet|Controller|' || cis.controller_guid::TEXT AS mapping_key,
        NULL::TEXT AS "parentMappingKey",  -- Root circuits have no parent
        cis.project_code || ' ' || cis.controller_name || ' | Root' AS name,
        COALESCE(cis.fuse_size, 0) AS "maxCurrent",
        CASE WHEN UPPER(cis.nettype) IN ('IT', 'TT') THEN '230' ELSE '400' END AS "maxVoltage",  -- IT and TT networks both use delta (no neutral)
        'three_phase'::TEXT AS phases,
        'RST'::TEXT AS "phaseRotation",
        6 AS "minChargePointCurrent",  -- Hardcoded minimum AC charging current
        CASE WHEN UPPER(cis.nettype) IN ('IT', 'TT') THEN 'delta' ELSE 'star' END AS "electricalConfiguration",
        cis.project_code,
        cis.target_location_id,
        cis.location_nr,
        'Controller'::TEXT AS source_type,
        cis.controller_guid::TEXT AS source_guid,
        cis.controller_name AS source_name
    FROM controllers_in_scope cis
),
-- Sub-circuits from Clusters
sub_circuits AS (
    SELECT 
        'circuit_mapping'::TEXT AS mapping_table,
        'Sleet|Cluster|' || cwp.cluster_guid::TEXT AS mapping_key,
        cwp.resolved_parent_mapping_key AS "parentMappingKey",
        cwp.project_code || ' ' || cwp.controller_name || ' | ' || cwp.cluster_name AS name,
        COALESCE(cwp.cluster_limit, 0) AS "maxCurrent",
        CASE WHEN UPPER(cwp.controller_nettype) IN ('IT', 'TT') THEN '230' ELSE '400' END AS "maxVoltage",  -- IT and TT networks both use delta (no neutral)
        'three_phase'::TEXT AS phases,
        'RST'::TEXT AS "phaseRotation",
        6 AS "minChargePointCurrent",
        CASE WHEN UPPER(cwp.controller_nettype) IN ('IT', 'TT') THEN 'delta' ELSE 'star' END AS "electricalConfiguration",
        cwp.project_code,
        cwp.target_location_id,
        cwp.location_nr,
        'Cluster'::TEXT AS source_type,
        cwp.cluster_guid::TEXT AS source_guid,
        cwp.cluster_name AS source_name
    FROM clusters_with_parent_resolution cwp
)
-- Final UNION: Root circuits first (ensures they're created before sub-circuits in Pass 1)
SELECT 
    rc.mapping_table,
    rc.mapping_key,
    rc.name,
    rc."maxCurrent",
    rc."maxVoltage",
    rc.phases,
    rc."phaseRotation",
    rc."minChargePointCurrent",
    rc."electricalConfiguration",
    rc.project_code,
    rc.target_location_id,
    rc.location_nr,
    rc.source_type,
    rc.source_guid,
    rc.source_name,
    cm.target_circuit_id,
    NULL::INTEGER AS "parentCircuitId",  -- Root circuits have no parent
    cm.target_electricity_meter_id AS "electricityMeterId"  -- Only root circuits have electricity meters
FROM root_circuits rc
LEFT JOIN "Mapping"."circuit_mapping" cm ON cm.mapping_key = rc.mapping_key

UNION ALL

SELECT 
    sc.mapping_table,
    sc.mapping_key,
    sc.name,
    sc."maxCurrent",
    sc."maxVoltage",
    sc.phases,
    sc."phaseRotation",
    sc."minChargePointCurrent",
    sc."electricalConfiguration",
    sc.project_code,
    sc.target_location_id,
    sc.location_nr,
    sc.source_type,
    sc.source_guid,
    sc.source_name,
    cm.target_circuit_id,
    parent_cm.target_circuit_id AS "parentCircuitId",  -- Resolved from parentMappingKey
    NULL::INTEGER AS "electricityMeterId"  -- Sub-circuits don't have electricity meters
FROM sub_circuits sc
LEFT JOIN "Mapping"."circuit_mapping" cm ON cm.mapping_key = sc.mapping_key
LEFT JOIN "Mapping"."circuit_mapping" parent_cm ON parent_cm.mapping_key = sc."parentMappingKey";
