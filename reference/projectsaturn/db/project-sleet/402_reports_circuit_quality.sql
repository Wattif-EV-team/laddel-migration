-- ============================================================================
-- QUALITY CHECK VIEW: CircuitQualityIssues
-- ============================================================================
-- Reports data quality issues that may affect circuit migration.
-- Scope: Entities joined to a Location where exclude = FALSE (includes both
--        migrate and non-migrate locations; excludes only explicitly excluded).
--
-- Classification:
--   INFO    - Data is missing but expected given the context, or auto-corrected
--   WARNING - Data may be corrupt/malformed, using fallback values - verify
--   ERROR   - Major problems requiring source data adjustment before migration
--
-- Severity Downgrade (DD#8):
--   For electrical settings issues (Section A), when a location has NO load
--   balancing controller (no DLM), all ERROR/WARNING flags are downgraded to
--   INFO. Without a controller, electrical settings are purely informational
--   and have no practical effect on the target CSMS.
--
-- Severity Downgrade (Inactive Entities):
--   For Section A: if the charger or connector is inactive, classification
--   is set to INFO regardless of DLM or original severity.
--   For Section B charger-level issues: if charger is inactive → INFO.
--
-- Issue Types - Electrical Settings (from Source.ElectricalSettingsNormalized):
--   has_unknown_electrical_configuration     [WARNING]    - No controller nettype, defaults to star
--   has_default_value_on_connected_phase     [WARNING]    - Connected phase set to default
--   has_unknown_phases                       [WARNING]    - Phases derived from power_type fallback
--   has_invalid_charger_phases               [WARNING]    - Charger N/L1/L2/L3 invalid combination
--   has_fallback_phase_rotation              [WARNING]    - Three-phase rotation fell back to RST
--   has_missing_current_on_connector         [WARNING]    - Connector amperage null or zero
--   has_charger_connector_phase_mismatch     [WARNING]    - Connector vs charger phase mapping disagree
--   has_power_effect_phase_mismatch           [WARNING]    - kW/A ratio disagrees with derived phases
--   has_invalid_phase_mapping_for_it_tt      [ERROR]      - IT/TT phase_mapping invalid for delta
--   has_phase_mapping_without_neutral_on_tn  [ERROR]      - TN phase_mapping missing neutral
--   has_duplicate_connector_identity         [ERROR/WARN] - charger_name + connector_level not unique
--   has_power_type_override_on_it_tt         [INFO]       - Delta connector AC_3_PHASE auto-corrected
--
-- Issue Types - Circuit Topology (direct source table queries):
--   has_charger_without_controller           [ERROR/INFO] - Charger has no controller assignment
--   has_charger_without_cluster              [WARNING/INFO] - Charger has controller but no cluster
--   has_invalid_cluster_reference            [ERROR]      - Charger references non-existent cluster
--   invalid_parent_cluster                   [ERROR]      - Cluster references non-existent parent
--   invalid_cluster_controller               [ERROR]      - Cluster references non-existent controller
--   missing_fuse_size                        [ERROR]      - Controller has no fuse_size
--   missing_limit                            [ERROR]      - Cluster has no limit
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Reports"."CircuitQualityIssues";

CREATE OR REPLACE VIEW "Reports"."CircuitQualityIssues" AS

-- Helper CTE: Locations with load balancing (DLM)
-- A location has DLM if at least one controller with a real serial number exists.
-- Used for: (1) severity downgrade on electrical settings issues,
--           (2) ERROR/INFO split on has_charger_without_controller.
WITH locations_with_dlm AS (
    SELECT DISTINCT location_nr
    FROM "Source"."Controllers"
    WHERE serial_number IS NOT NULL AND serial_number != ''
)

-- ============================================================================
-- SECTION A: ELECTRICAL SETTINGS ISSUES
-- Source: Quality flags from Source.ElectricalSettingsNormalized (099)
-- Join: ESN → Chargers → Locations → location_mapping (exclude = FALSE)
-- Charger-level flags: is_first_connector = TRUE to avoid duplicates
-- Connector-level flags: all rows
-- ============================================================================

-- Charger-level: has_unknown_electrical_configuration (WARNING, downgrade to INFO without DLM or inactive charger)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE WHEN esn.charger_status = 'Inactive' THEN 'INFO'
         WHEN chr.location_nr IN (SELECT location_nr FROM locations_with_dlm)
         THEN 'WARNING' ELSE 'INFO' END::TEXT AS classification,
    'Charger'::TEXT AS entity_type,
    chr."Id"::TEXT AS entity_id,
    chr.charger_name AS entity_name,
    'has_unknown_electrical_configuration'::TEXT AS issue_type,
    'Controller nettype is NULL or empty - electrical configuration defaults to star'::TEXT AS issue_reason,
    ('nettype=' || COALESCE(esn.controller_nettype, 'NULL'))::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."ElectricalSettingsNormalized" esn
JOIN "Source"."Chargers" chr ON chr.charger_id = esn.charger_id
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND esn.has_unknown_electrical_configuration = TRUE
  AND esn.is_first_connector = TRUE

UNION ALL

-- Charger-level: has_default_value_on_connected_phase (WARNING, downgrade to INFO without DLM or inactive charger)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE WHEN esn.charger_status = 'Inactive' THEN 'INFO'
         WHEN chr.location_nr IN (SELECT location_nr FROM locations_with_dlm)
         THEN 'WARNING' ELSE 'INFO' END::TEXT AS classification,
    'Charger'::TEXT AS entity_type,
    chr."Id"::TEXT AS entity_id,
    chr.charger_name AS entity_name,
    'has_default_value_on_connected_phase'::TEXT AS issue_type,
    'Connected phase set to default value - phase mapping data insufficient for derivation'::TEXT AS issue_reason,
    ('phases=' || COALESCE(esn.target_chargepoint_phases, 'NULL') ||
     ', connected_phase=' || COALESCE(esn.target_chargepoint_connected_phase, 'NULL'))::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."ElectricalSettingsNormalized" esn
JOIN "Source"."Chargers" chr ON chr.charger_id = esn.charger_id
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND esn.has_default_value_on_connected_phase = TRUE
  AND esn.is_first_connector = TRUE

UNION ALL

-- Charger-level: has_unknown_phases (WARNING, downgrade to INFO without DLM or inactive charger)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE WHEN esn.charger_status = 'Inactive' THEN 'INFO'
         WHEN chr.location_nr IN (SELECT location_nr FROM locations_with_dlm)
         THEN 'WARNING' ELSE 'INFO' END::TEXT AS classification,
    'Charger'::TEXT AS entity_type,
    chr."Id"::TEXT AS entity_id,
    chr.charger_name AS entity_name,
    'has_unknown_phases'::TEXT AS issue_type,
    'Phase count derived from power_type fallback - no phase mapping data available'::TEXT AS issue_reason,
    ('power_type=' || COALESCE(esn.connector_power_type, 'NULL') ||
     ', phase_mapping=' || COALESCE(esn.connector_phase_mapping, 'NULL'))::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."ElectricalSettingsNormalized" esn
JOIN "Source"."Chargers" chr ON chr.charger_id = esn.charger_id
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND esn.has_unknown_phases = TRUE
  AND esn.is_first_connector = TRUE

UNION ALL

-- Charger-level: has_invalid_charger_phases (WARNING, downgrade to INFO without DLM or inactive charger)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE WHEN esn.charger_status = 'Inactive' THEN 'INFO'
         WHEN chr.location_nr IN (SELECT location_nr FROM locations_with_dlm)
         THEN 'WARNING' ELSE 'INFO' END::TEXT AS classification,
    'Charger'::TEXT AS entity_type,
    chr."Id"::TEXT AS entity_id,
    chr.charger_name AS entity_name,
    'has_invalid_charger_phases'::TEXT AS issue_type,
    'Charger N/L1/L2/L3 columns form invalid combination - using fallback'::TEXT AS issue_reason,
    ('n=' || COALESCE(esn.charger_n, 'NULL') ||
     ', l1=' || COALESCE(esn.charger_l1, 'NULL') ||
     ', l2=' || COALESCE(esn.charger_l2, 'NULL') ||
     ', l3=' || COALESCE(esn.charger_l3, 'NULL'))::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."ElectricalSettingsNormalized" esn
JOIN "Source"."Chargers" chr ON chr.charger_id = esn.charger_id
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND esn.has_invalid_charger_phases = TRUE
  AND esn.is_first_connector = TRUE

UNION ALL

-- Charger-level: has_fallback_phase_rotation (WARNING, downgrade to INFO without DLM or inactive charger)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE WHEN esn.charger_status = 'Inactive' THEN 'INFO'
         WHEN chr.location_nr IN (SELECT location_nr FROM locations_with_dlm)
         THEN 'WARNING' ELSE 'INFO' END::TEXT AS classification,
    'Charger'::TEXT AS entity_type,
    chr."Id"::TEXT AS entity_id,
    chr.charger_name AS entity_name,
    'has_fallback_phase_rotation'::TEXT AS issue_type,
    'Three-phase rotation fell back to default RST - connector phase_mapping not in recognized patterns'::TEXT AS issue_reason,
    ('phase_mapping=' || COALESCE(esn.connector_phase_mapping, 'NULL') ||
     ', phases=' || COALESCE(esn.target_chargepoint_phases, 'NULL'))::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."ElectricalSettingsNormalized" esn
JOIN "Source"."Chargers" chr ON chr.charger_id = esn.charger_id
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND esn.has_fallback_phase_rotation = TRUE
  AND esn.is_first_connector = TRUE

UNION ALL

-- Connector-level: has_missing_current_on_connector (WARNING, downgrade to INFO without DLM or inactive entity)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE WHEN esn.charger_status = 'Inactive' OR esn.connector_status_reason = 'Inactive' THEN 'INFO'
         WHEN chr.location_nr IN (SELECT location_nr FROM locations_with_dlm)
         THEN 'WARNING' ELSE 'INFO' END::TEXT AS classification,
    'Connector'::TEXT AS entity_type,
    esn.charger_id::TEXT AS entity_id,
    chr.charger_name || ' / connector ' || esn.connector_level AS entity_name,
    'has_missing_current_on_connector'::TEXT AS issue_type,
    'Connector amperage is null or zero - using fallback value'::TEXT AS issue_reason,
    ('amperage=' || COALESCE(esn.connector_amperage::TEXT, 'NULL') ||
     ', max_effect=' || COALESCE(esn.connector_max_effect::TEXT, 'NULL'))::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."ElectricalSettingsNormalized" esn
JOIN "Source"."Chargers" chr ON chr.charger_id = esn.charger_id
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND esn.has_missing_current_on_connector = TRUE

UNION ALL

-- Connector-level: has_charger_connector_phase_mismatch (WARNING, downgrade to INFO without DLM or inactive entity)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE WHEN esn.charger_status = 'Inactive' OR esn.connector_status_reason = 'Inactive' THEN 'INFO'
         WHEN chr.location_nr IN (SELECT location_nr FROM locations_with_dlm)
         THEN 'WARNING' ELSE 'INFO' END::TEXT AS classification,
    'Connector'::TEXT AS entity_type,
    esn.charger_id::TEXT AS entity_id,
    chr.charger_name || ' / connector ' || esn.connector_level AS entity_name,
    'has_charger_connector_phase_mismatch'::TEXT AS issue_type,
    'Connector phase_mapping disagrees with charger-derived phase mapping'::TEXT AS issue_reason,
    ('connector=' || COALESCE(esn.connector_phase_mapping, 'NULL') ||
     ', charger_derived=' || COALESCE(esn.charger_phase_mapping_derived, 'NULL'))::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."ElectricalSettingsNormalized" esn
JOIN "Source"."Chargers" chr ON chr.charger_id = esn.charger_id
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND esn.has_charger_connector_phase_mismatch = TRUE

UNION ALL

-- Connector-level: has_power_effect_phase_mismatch (WARNING, downgrade to INFO without DLM or inactive entity)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE WHEN esn.charger_status = 'Inactive' OR esn.connector_status_reason = 'Inactive' THEN 'INFO'
         WHEN chr.location_nr IN (SELECT location_nr FROM locations_with_dlm)
         THEN 'WARNING' ELSE 'INFO' END::TEXT AS classification,
    'Connector'::TEXT AS entity_type,
    esn.charger_id::TEXT AS entity_id,
    chr.charger_name || ' / connector ' || esn.connector_level AS entity_name,
    'has_power_effect_phase_mismatch'::TEXT AS issue_type,
    'Amperage and max effect imply different phase configuration than derived - verify source data'::TEXT AS issue_reason,
    ('phases=' || COALESCE(esn.target_chargepoint_phases, 'NULL') ||
     ', config=' || COALESCE(esn.target_chargepoint_electrical_configuration, 'NULL') ||
     ', amperage=' || COALESCE(esn.connector_amperage::TEXT, 'NULL') ||
     ', max_effect=' || COALESCE(esn.connector_max_effect, 'NULL') ||
     ', implied_V_per_A=' || COALESCE((esn.connector_max_effect::numeric * 1000 / NULLIF(esn.connector_amperage, 0))::int::TEXT, 'NULL'))::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."ElectricalSettingsNormalized" esn
JOIN "Source"."Chargers" chr ON chr.charger_id = esn.charger_id
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND esn.has_power_effect_phase_mismatch = TRUE

UNION ALL

-- Connector-level: has_invalid_phase_mapping_for_it_tt (ERROR, downgrade to INFO without DLM or inactive entity)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE WHEN esn.charger_status = 'Inactive' OR esn.connector_status_reason = 'Inactive' THEN 'INFO'
         WHEN chr.location_nr IN (SELECT location_nr FROM locations_with_dlm)
         THEN 'ERROR' ELSE 'INFO' END::TEXT AS classification,
    'Connector'::TEXT AS entity_type,
    esn.charger_id::TEXT AS entity_id,
    chr.charger_name || ' / connector ' || esn.connector_level AS entity_name,
    'has_invalid_phase_mapping_for_it_tt'::TEXT AS issue_type,
    'IT/TT connector phase_mapping does not match expected pattern for delta network'::TEXT AS issue_reason,
    ('nettype=' || COALESCE(esn.controller_nettype, 'NULL') ||
     ', phase_mapping=' || COALESCE(esn.connector_phase_mapping, 'NULL'))::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."ElectricalSettingsNormalized" esn
JOIN "Source"."Chargers" chr ON chr.charger_id = esn.charger_id
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND esn.has_invalid_phase_mapping_for_it_tt = TRUE

UNION ALL

-- Connector-level: has_phase_mapping_without_neutral_on_tn (ERROR, downgrade to INFO without DLM or inactive entity)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE WHEN esn.charger_status = 'Inactive' OR esn.connector_status_reason = 'Inactive' THEN 'INFO'
         WHEN chr.location_nr IN (SELECT location_nr FROM locations_with_dlm)
         THEN 'ERROR' ELSE 'INFO' END::TEXT AS classification,
    'Connector'::TEXT AS entity_type,
    esn.charger_id::TEXT AS entity_id,
    chr.charger_name || ' / connector ' || esn.connector_level AS entity_name,
    'has_phase_mapping_without_neutral_on_tn'::TEXT AS issue_type,
    'TN connector phase_mapping missing neutral (N) - expected for star configuration'::TEXT AS issue_reason,
    ('nettype=' || COALESCE(esn.controller_nettype, 'NULL') ||
     ', phase_mapping=' || COALESCE(esn.connector_phase_mapping, 'NULL'))::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."ElectricalSettingsNormalized" esn
JOIN "Source"."Chargers" chr ON chr.charger_id = esn.charger_id
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND esn.has_phase_mapping_without_neutral_on_tn = TRUE

UNION ALL

-- Connector-level: has_duplicate_connector_identity
-- ERROR  when duplicates are all active (unresolvable conflict)
-- WARNING when duplicate group has exactly one active connector (resolvable;
--          the EVSE view de-duplicates by preferring Active over Inactive)
-- INFO   when this specific row is inactive, or location has no DLM
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE
        -- This row is inactive → INFO
        WHEN esn.charger_status = 'Inactive' OR esn.connector_status_reason = 'Inactive' THEN 'INFO'
        -- Location has no DLM → INFO
        WHEN chr.location_nr NOT IN (SELECT location_nr FROM locations_with_dlm) THEN 'INFO'
        -- Duplicate group has exactly one active connector → resolvable → WARNING
        WHEN (
            SELECT COUNT(*) FROM "Source"."ElectricalSettingsNormalized" dup
            WHERE dup.charger_name = esn.charger_name
              AND dup.connector_level = esn.connector_level
              AND dup.connector_status_reason = 'Active'
              AND (dup.charger_status IS NULL OR dup.charger_status = 'Active')
        ) = 1 THEN 'WARNING'
        -- All duplicates are active → unresolvable → ERROR
        ELSE 'ERROR'
    END::TEXT AS classification,
    'Connector'::TEXT AS entity_type,
    esn.charger_id::TEXT AS entity_id,
    chr.charger_name || ' / connector ' || esn.connector_level AS entity_name,
    'has_duplicate_connector_identity'::TEXT AS issue_type,
    'Charger name + connector level combination is not unique'::TEXT AS issue_reason,
    ('charger_name=' || esn.charger_name || ', connector_level=' || esn.connector_level::TEXT)::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."ElectricalSettingsNormalized" esn
JOIN "Source"."Chargers" chr ON chr.charger_id = esn.charger_id
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND esn.has_duplicate_connector_identity = TRUE

UNION ALL

-- Connector-level: has_power_type_override_on_it_tt (INFO - no downgrade needed, already INFO)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    'INFO'::TEXT AS classification,
    'Connector'::TEXT AS entity_type,
    esn.charger_id::TEXT AS entity_id,
    chr.charger_name || ' / connector ' || esn.connector_level AS entity_name,
    'has_power_type_override_on_it_tt'::TEXT AS issue_type,
    'Delta network connector has AC_3_PHASE power type - auto-corrected to single-phase'::TEXT AS issue_reason,
    ('power_type=' || COALESCE(esn.connector_power_type, 'NULL') ||
     ', nettype=' || COALESCE(esn.controller_nettype, 'NULL') ||
     ', config=' || COALESCE(esn.target_chargepoint_electrical_configuration, 'NULL'))::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."ElectricalSettingsNormalized" esn
JOIN "Source"."Chargers" chr ON chr.charger_id = esn.charger_id
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND esn.has_power_type_override_on_it_tt = TRUE

UNION ALL

-- ============================================================================
-- SECTION B: CIRCUIT TOPOLOGY ISSUES
-- Source: Direct queries against Source tables (no ESN dependency)
-- Join: Chargers/Controllers/Clusters → Locations → location_mapping (exclude = FALSE)
-- ============================================================================

-- has_charger_without_controller: ERROR when location has DLM, INFO when no DLM or inactive charger
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE WHEN chr.status = 'Inactive' THEN 'INFO'
         WHEN chr.location_nr IN (SELECT location_nr FROM locations_with_dlm)
         THEN 'ERROR' ELSE 'INFO' END::TEXT AS classification,
    'Charger'::TEXT AS entity_type,
    chr."Id"::TEXT AS entity_id,
    chr.charger_name AS entity_name,
    'has_charger_without_controller'::TEXT AS issue_type,
    CASE WHEN chr.location_nr IN (SELECT location_nr FROM locations_with_dlm)
         THEN 'Charger has no controller assignment but location has DLM controllers - verify assignment'
         ELSE 'Charger has no controller assignment - location has no DLM controllers'
    END::TEXT AS issue_reason,
    COALESCE(chr.controller_serial_number, '[NULL]')::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."Chargers" chr
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND (chr.controller_serial_number IS NULL OR chr.controller_serial_number = '')

UNION ALL

-- has_charger_without_cluster: WARNING when siblings on same controller have clusters (MIXED pattern),
-- INFO otherwise (all chargers on controller lack cluster, or no clusters defined, or inactive charger)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE WHEN chr.status = 'Inactive' THEN 'INFO'
         WHEN EXISTS (
            SELECT 1 FROM "Source"."Chargers" sibling
            WHERE sibling.controller_serial_number = chr.controller_serial_number
              AND sibling."name" IS NOT NULL AND sibling."name" != ''
         )
         THEN 'WARNING' ELSE 'INFO' END::TEXT AS classification,
    'Charger'::TEXT AS entity_type,
    chr."Id"::TEXT AS entity_id,
    chr.charger_name AS entity_name,
    'has_charger_without_cluster'::TEXT AS issue_type,
    CASE WHEN EXISTS (
            SELECT 1 FROM "Source"."Chargers" sibling
            WHERE sibling.controller_serial_number = chr.controller_serial_number
              AND sibling."name" IS NOT NULL AND sibling."name" != ''
         )
         THEN 'Charger has no cluster but other chargers on same controller do - likely data entry error'
         ELSE 'Charger has controller but no cluster assignment - will attach to root circuit'
    END::TEXT AS issue_reason,
    COALESCE(chr."name", '[NULL]')::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."Chargers" chr
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND chr.controller_serial_number IS NOT NULL AND chr.controller_serial_number != ''
  AND (chr."name" IS NULL OR chr."name" = '')

UNION ALL

-- has_invalid_cluster_reference: ERROR (charger references cluster not found under same controller, INFO if inactive)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    CASE WHEN chr.status = 'Inactive' THEN 'INFO' ELSE 'ERROR' END::TEXT AS classification,
    'Charger'::TEXT AS entity_type,
    chr."Id"::TEXT AS entity_id,
    chr.charger_name AS entity_name,
    'has_invalid_cluster_reference'::TEXT AS issue_type,
    'Charger references cluster that does not exist under the same controller'::TEXT AS issue_reason,
    chr."name"::TEXT AS referenced_value,
    chr.location_nr
FROM "Source"."Chargers" chr
JOIN "Source"."Locations" loc ON chr.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
LEFT JOIN "Source"."Clusters" clus
    ON chr."name" = clus.cluster_name
    AND chr.controller_serial_number = clus.controller_serial_number
WHERE lm.exclude = FALSE
  AND chr."name" IS NOT NULL
  AND chr."name" != ''
  AND chr.controller_serial_number IS NOT NULL
  AND chr.controller_serial_number != ''
  AND clus."Id" IS NULL

UNION ALL

-- invalid_parent_cluster: ERROR (cluster references non-existent parent)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    'ERROR'::TEXT AS classification,
    'Cluster'::TEXT AS entity_type,
    clus."Id"::TEXT AS entity_id,
    clus.cluster_name AS entity_name,
    'invalid_parent_cluster'::TEXT AS issue_type,
    'Cluster references parent that does not exist under same controller'::TEXT AS issue_reason,
    clus.parent_cluster::TEXT AS referenced_value,
    ctrl.location_nr
FROM "Source"."Clusters" clus
JOIN "Source"."Controllers" ctrl ON clus.controller_serial_number = ctrl.serial_number
JOIN "Source"."Locations" loc ON ctrl.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
LEFT JOIN "Source"."Clusters" parent_clus
    ON clus.parent_cluster = parent_clus.cluster_name
    AND clus.controller_serial_number = parent_clus.controller_serial_number
WHERE lm.exclude = FALSE
  AND clus.parent_cluster IS NOT NULL
  AND clus.parent_cluster != ''
  AND LOWER(clus.parent_cluster) != 'root'
  AND parent_clus."Id" IS NULL

UNION ALL

-- invalid_cluster_controller: ERROR (cluster references non-existent controller)
-- Uses clus.location to find the Location (matches Locations.location_name)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    'ERROR'::TEXT AS classification,
    'Cluster'::TEXT AS entity_type,
    clus."Id"::TEXT AS entity_id,
    clus.cluster_name AS entity_name,
    'invalid_cluster_controller'::TEXT AS issue_type,
    'Cluster references controller serial number that does not exist'::TEXT AS issue_reason,
    clus.controller_serial_number::TEXT AS referenced_value,
    loc.location_nr
FROM "Source"."Clusters" clus
JOIN "Source"."Locations" loc ON clus.location = loc.location_name
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
LEFT JOIN "Source"."Controllers" ctrl ON clus.controller_serial_number = ctrl.serial_number
WHERE lm.exclude = FALSE
  AND clus.controller_serial_number IS NOT NULL
  AND clus.controller_serial_number != ''
  AND ctrl."Id" IS NULL

UNION ALL

-- missing_fuse_size: ERROR (controller has no fuse_size - cannot determine maxCurrent for root circuit)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    'ERROR'::TEXT AS classification,
    'Controller'::TEXT AS entity_type,
    ctrl."Id"::TEXT AS entity_id,
    ctrl.controller_name AS entity_name,
    'missing_fuse_size'::TEXT AS issue_type,
    'Controller has no fuse_size - cannot determine maxCurrent for root circuit'::TEXT AS issue_reason,
    '[NULL]'::TEXT AS referenced_value,
    ctrl.location_nr
FROM "Source"."Controllers" ctrl
JOIN "Source"."Locations" loc ON ctrl.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND ctrl.fuse_size IS NULL

UNION ALL

-- missing_limit: ERROR (cluster has no limit - cannot determine maxCurrent for sub-circuit)
SELECT
    lm.project_code,
    loc.location_name,
    lm.migrate,
    lm.status,
    'ERROR'::TEXT AS classification,
    'Cluster'::TEXT AS entity_type,
    clus."Id"::TEXT AS entity_id,
    clus.cluster_name AS entity_name,
    'missing_limit'::TEXT AS issue_type,
    'Cluster has no limit - cannot determine maxCurrent for sub-circuit'::TEXT AS issue_reason,
    '[NULL]'::TEXT AS referenced_value,
    ctrl.location_nr
FROM "Source"."Clusters" clus
JOIN "Source"."Controllers" ctrl ON clus.controller_serial_number = ctrl.serial_number
JOIN "Source"."Locations" loc ON ctrl.location_nr = loc.location_nr
JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id" = lm.mapping_key
WHERE lm.exclude = FALSE
  AND clus."limit" IS NULL;

-- Verify view was created
SELECT 'CircuitQualityIssues view created successfully' AS status;
