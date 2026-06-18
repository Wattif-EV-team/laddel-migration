-- ============================================================================
-- SOURCE TABLE: EvseId
-- ============================================================================
-- This table is populated by FetchFromSharePoint.py from the EVSE_ID sheet
-- in "Project Sleet Planning - Master.xlsx"
-- 
-- The table structure must match the sharepoint_sync.json configuration.
-- This DDL ensures the table exists before data import.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Create EvseId table for physical reference lookup
-- Join to Connectors using composite key: (charger_id, connector_level)
CREATE TABLE IF NOT EXISTS "Source"."EvseId" (
    -- Charger ID (FK to Source.Chargers.charger_id)
    "charger_id" INTEGER,
    
    -- Connector level within charger (1, 2, etc.)
    "connector_level" INTEGER,
    
    -- EVSE ID - the physical reference value (e.g., "W047100001")
    "evse_id" TEXT,
    
    -- Primary key on evse_id as per sharepoint_sync.json
    -- Note: "Id" column will be added by import tool
    PRIMARY KEY ("evse_id")
);

-- Create index for efficient composite key lookups
CREATE INDEX IF NOT EXISTS "ix_evseid_charger_connector" 
ON "Source"."EvseId" ("charger_id", "connector_level");
