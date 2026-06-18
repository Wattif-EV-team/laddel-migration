SET ROLE db_sleetmigration_owner;

-- Phase 0: Empty view structure for Reports.AllEvseWithPhysicalReference
DROP VIEW IF EXISTS "Reports"."AllEvseWithPhysicalReference";

CREATE OR REPLACE VIEW "Reports"."AllEvseWithPhysicalReference" AS
SELECT 
    NULL::TEXT AS mapping_table,
    NULL::TEXT AS mapping_key,
    NULL::TEXT AS merge_with_mapping_key,
    NULL::TEXT AS "ProjectCode",
    NULL::INTEGER AS "RowNumber",
    NULL::TEXT AS "PhysicalReference",
    NULL::TEXT AS "SourceChargePointID",
    NULL::TEXT AS "SourceChargerID",
    NULL::TEXT AS "SourceName"
WHERE 1=0;
