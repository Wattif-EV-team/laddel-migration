SET ROLE db_sleetmigration_owner;

-- Target.ChargingZones view for Project Sleet
-- Default: one charging zone per location (from location_mapping)
-- Override: multi-zone locations use zone_mapping rows instead (e.g., Gardermoen)
DROP VIEW IF EXISTS "Target"."ChargingZones";

CREATE OR REPLACE VIEW "Target"."ChargingZones" AS

-- Default 1:1 location→zone pattern (excludes locations with zone_mapping overrides)
SELECT 
    -- Mapping columns (no merge for charging zones - one per source location)
    'location_mapping'::TEXT AS mapping_table,
    lm.mapping_key,
    NULL::TEXT AS merge_with_mapping_key,
    
    -- Source identifiers for debugging
    SUBSTRING(lm.mapping_key FROM 'Sleet\\|Location\\|(.*)') AS source_location_guid,
    
    -- Target IDs
    lm.target_location_id AS "TargetLocationID",
    lm.target_charge_zone_id AS "TargetChargeZoneID",
    
    -- Zone status
    'enabled'::TEXT AS "status",
    
    -- Zone name
    lm.charging_zone_name AS "name",
    
    -- Additional info
    TRUE AS "additionalInfo_enabled",
    lm.charging_zone_name AS "additionalInfo_title_en",
    lm.charging_zone_name AS "additionalInfo_title_nb-NO"

FROM "Mapping"."location_mapping" lm
WHERE lm.migrate = TRUE
  AND lm.charging_zone_name IS NOT NULL
  AND lm.target_location_id IS NOT NULL
  -- Exclude locations that have per-zone overrides in zone_mapping
  AND lm.mapping_key NOT IN (SELECT location_mapping_key FROM "Mapping"."zone_mapping")

UNION ALL

-- Multi-zone overrides (one row per zone from zone_mapping)
SELECT 
    'zone_mapping'::TEXT AS mapping_table,
    zm.mapping_key,
    NULL::TEXT AS merge_with_mapping_key,
    
    SUBSTRING(zm.location_mapping_key FROM 'Sleet\\|Location\\|(.*)') AS source_location_guid,
    
    zm.target_location_id AS "TargetLocationID",
    zm.target_charge_zone_id AS "TargetChargeZoneID",
    
    'enabled'::TEXT AS "status",
    
    zm.zone_name AS "name",
    
    TRUE AS "additionalInfo_enabled",
    zm.zone_name AS "additionalInfo_title_en",
    zm.zone_name AS "additionalInfo_title_nb-NO"

FROM "Mapping"."zone_mapping" zm
WHERE zm.target_location_id IS NOT NULL;
