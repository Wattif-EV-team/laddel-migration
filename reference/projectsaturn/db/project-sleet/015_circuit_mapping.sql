-- ============================================================================
-- MAPPING TABLE: circuit_mapping (snake_case, key-based)
-- ============================================================================
-- WARNING: NEVER DROP THIS TABLE - stores migration state and target system IDs
-- 
-- This is a PERSISTENT mapping table. Data is preserved across migrations.
-- - Use CREATE TABLE IF NOT EXISTS for initial creation
-- - To add new columns, use ALTER TABLE ... ADD COLUMN IF NOT EXISTS syntax:
--   ALTER TABLE "Mapping"."circuit_mapping" ADD COLUMN IF NOT EXISTS "new_column" TYPE;
--
-- Key format: 
--   Root circuits (Controllers): Sleet|Controller|{controller_guid}
--   Sub-circuits (Clusters):     Sleet|Cluster|{cluster_guid}
--
-- Note: Electricity meters also use this table with Controller keys (1:1 with root circuits)
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Create circuit_mapping table for Project Sleet
-- Maps Source.Controllers (root circuits) and Source.Clusters (sub-circuits) to Ampeco Circuits
-- Also stores electricity meter IDs for Controllers (1:1 relationship)
CREATE TABLE IF NOT EXISTS "Mapping"."circuit_mapping" (
    -- Primary key (format: Sleet|Controller|{guid} or Sleet|Cluster|{guid})
    mapping_key TEXT PRIMARY KEY,
    
    -- Target ID (populated after creation in target system)
    target_circuit_id INTEGER
);

-- Add electricity meter ID column (for Controller rows only)
ALTER TABLE "Mapping"."circuit_mapping" 
ADD COLUMN IF NOT EXISTS target_electricity_meter_id INTEGER;
