-- ============================================================================
-- MAPPING TABLE: zone_mapping (snake_case, key-based)
-- ============================================================================
-- WARNING: NEVER DROP THIS TABLE - stores migration state and target system IDs
--
-- This is a PERSISTENT mapping table. Data is preserved across migrations.
-- - Use CREATE TABLE IF NOT EXISTS for initial creation
-- - To add new columns, use ALTER TABLE ... ADD COLUMN IF NOT EXISTS syntax
--
-- Key format: Sleet|Zone|{location_guid}|{zone_name}
--
-- Supports multi-zone locations where the default 1:1 location→zone pattern
-- does not apply (e.g., Gardermoen Leiebilservice with 5 rental company zones).
--
-- CreateOrUpdateChargingZone.py writes back target_charge_zone_id via the
-- generic mapping_table/mapping_key writeback pattern.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Create zone_mapping table (persistent — never dropped)
CREATE TABLE IF NOT EXISTS "Mapping"."zone_mapping" (
    -- Primary key (format: Sleet|Zone|{location_guid}|{zone_name})
    mapping_key TEXT PRIMARY KEY,

    -- FK reference to location_mapping.mapping_key
    location_mapping_key TEXT NOT NULL,

    -- Zone name (title-cased: Hertz, Sixt, Avis, Europcar, Enterprise)
    zone_name TEXT NOT NULL,

    -- Target IDs
    target_location_id INTEGER,           -- Ampeco location ID (copied from location_mapping)
    target_charge_zone_id INTEGER         -- Populated by CreateOrUpdateChargingZone.py writeback
);

-- Idempotent seed: insert one row per distinct zone from GardermoenZones source table.
-- ON CONFLICT DO NOTHING ensures reruns are safe and won't overwrite writeback values.
INSERT INTO "Mapping"."zone_mapping" (mapping_key, location_mapping_key, zone_name, target_location_id)
SELECT DISTINCT
    'Sleet|Zone|cbdd1089-8965-ed11-9561-6045bd905ded|' || gz.zone_name AS mapping_key,
    'Sleet|Location|cbdd1089-8965-ed11-9561-6045bd905ded' AS location_mapping_key,
    gz.zone_name,
    lm.target_location_id
FROM "Source"."GardermoenZones" gz
JOIN "Mapping"."location_mapping" lm
    ON lm.mapping_key = 'Sleet|Location|cbdd1089-8965-ed11-9561-6045bd905ded'
ON CONFLICT (mapping_key) DO NOTHING;
