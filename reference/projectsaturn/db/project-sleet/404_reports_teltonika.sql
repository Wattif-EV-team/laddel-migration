-- Teltonika Routers Report
-- One router per controller, plus one row per location without controllers
-- Router ID format: {project_code}-R{n} where n is counter per project_code

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Reports"."TeltonikaRouters";

CREATE OR REPLACE VIEW "Reports"."TeltonikaRouters" AS
WITH location_controllers AS (
    -- All locations with their controllers (LEFT JOIN to include locations without controllers)
    SELECT 
        loc."Id" AS location_guid,
        ctrl."Id" AS controller_guid,
        lm.project_code,
        loc.location_owner AS partner_name,
        loc.location_nr,
        loc.location_name,
        loc.address,
        loc.postal_code,
        loc.city,
        loc.installer_company AS installer_company_name,
        ctrl.controller_name,
        ctrl.serial_number AS controller_serial
    FROM "Mapping"."location_mapping" lm
    JOIN "Source"."Locations" loc ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    LEFT JOIN "Source"."Controllers" ctrl ON ctrl.location_nr = loc.location_nr
    WHERE lm.exclude = FALSE
)
SELECT
    -- GUID columns first (for re-import)
    location_guid,
    controller_guid,
    
    -- Router ID: project_code + '-R' + counter per project_code
    project_code || '-R' || ROW_NUMBER() OVER (
        PARTITION BY project_code 
        ORDER BY controller_serial, location_nr
    ) AS router_id,
    
    -- Report columns
    project_code,
    partner_name,
    location_nr,
    location_name,
    address,
    postal_code,
    city,
    installer_company_name,
    controller_name,
    controller_serial
FROM location_controllers
ORDER BY project_code, controller_serial, location_nr;
