-- ============================================================================
-- View: Source.AllRawChargerUsers
-- Description: Union of all RawChargerUsers source tables. Downstream 1xx
--              normalization views and 2xx+ business-logic views should
--              reference this view instead of individual source tables.
--
--              source_file column identifies the origin for traceability.
--
-- Sources:
--   - Source.RawChargerUsers            (main charger-user export)
--   - Source.RawChargerUsers_Glencore   (Glencore Nikkelverk supplement)
-- ============================================================================
SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Source"."AllRawChargerUsers" CASCADE;
CREATE OR REPLACE VIEW "Source"."AllRawChargerUsers" AS

SELECT *, 'main' AS source_file
FROM "Source"."RawChargerUsers"

UNION ALL

SELECT *, 'glencore' AS source_file
FROM "Source"."RawChargerUsers_Glencore";
