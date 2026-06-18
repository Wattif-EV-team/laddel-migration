-- ============================================================================
-- TARGET VIEW: Target.ElectricityMeters
-- ============================================================================
-- Generates Ampeco Electricity Meter API payloads for Project Sleet migration.
--
-- One electricity meter per Controller (1:1 relationship with root circuits).
-- Uses the same mapping_key format as root circuits: Sleet|Controller|{guid}
--
-- Meter ID format: {project_code}-M{n} where n is counter per project_code
-- Note: Meter IDs are generated BEFORE applying migrate filter to ensure
-- stable numbering when same project_code spans multiple locations.
--
-- API: POST/PATCH /resources/electricity-meters/v1.0
-- Required fields: name, integrationId, integrationParameters.device_id
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Target"."ElectricityMeters";

CREATE OR REPLACE VIEW "Target"."ElectricityMeters" AS
WITH all_controllers_with_meter_id AS (
    -- CTE 1: Generate meter_id for ALL controllers before filtering
    -- This ensures stable meter_id numbering regardless of migrate flag
    SELECT 
        ctrl."Id" AS controller_guid,
        ctrl.controller_name,
        ctrl.serial_number AS controller_serial,
        ctrl.location_nr,
        lm.project_code,
        lm.migrate,
        -- Generate meter_id: project_code + '-M' + counter per project_code
        lm.project_code || '-M' || ROW_NUMBER() OVER (
            PARTITION BY lm.project_code 
            ORDER BY ctrl.serial_number, ctrl.location_nr
        ) AS meter_id
    FROM "Source"."Controllers" ctrl
    JOIN "Source"."Locations" loc ON ctrl.location_nr = loc.location_nr
    JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    WHERE ctrl.serial_number IS NOT NULL 
      AND ctrl.serial_number != ''
)
SELECT 
    -- Mapping columns (for Python script to update circuit_mapping)
    'circuit_mapping'::TEXT AS mapping_table,
    'Sleet|Controller|' || ac.controller_guid::TEXT AS mapping_key,
    
    -- API payload columns
    ac.meter_id || ' - ' || ac.controller_name AS name,
    91 AS "integrationId",  -- Sleet integration ID (hardcoded for this project)
    ac.meter_id AS "integrationParameters_device_id",
    
    -- Target ID (populated after creation in Ampeco)
    cm.target_electricity_meter_id,
    
    -- Additional context columns (for logging/debugging)
    ac.project_code,
    ac.controller_serial,
    ac.location_nr
FROM all_controllers_with_meter_id ac
LEFT JOIN "Mapping"."circuit_mapping" cm 
    ON cm.mapping_key = 'Sleet|Controller|' || ac.controller_guid::TEXT
WHERE ac.migrate = TRUE
ORDER BY ac.project_code, ac.controller_serial;
