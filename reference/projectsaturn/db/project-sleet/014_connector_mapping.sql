-- ============================================================================
-- MAPPING TABLE: connector_mapping (snake_case, key-based)
-- ============================================================================
-- WARNING: NEVER DROP THIS TABLE - stores migration state and target system IDs
-- 
-- This is a PERSISTENT mapping table. Data is preserved across migrations.
-- - Use CREATE TABLE IF NOT EXISTS for initial creation
-- - To add new columns, use ALTER TABLE ... ADD COLUMN IF NOT EXISTS syntax:
--   ALTER TABLE "Mapping"."connector_mapping" ADD COLUMN IF NOT EXISTS "new_column" TYPE;
--
-- Key format: Sleet|Connector|{connector_guid}
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Create connector_mapping table for Project Sleet
-- Maps Source.Connectors to Target EVSE + Connector (1:1 relationship)
-- Note: One Charger can have many Connectors
CREATE TABLE IF NOT EXISTS "Mapping"."connector_mapping" (
    -- Primary key (format: Sleet|Connector|{connector_guid})
    mapping_key TEXT PRIMARY KEY,
    
    -- Target IDs (populated after creation in target system)
    target_evse_id INTEGER,
    target_connector_id INTEGER,
    
    -- Physical reference for EVSE label
    physical_reference TEXT
);
