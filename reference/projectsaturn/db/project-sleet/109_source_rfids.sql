-- ============================================================================
-- View: Source.RFIDs
-- Description: Physical RFID tags from two sources:
--              1. ChargerUsers export (RawChargerUsers) — the original source
--              2. Corporate RFID Tag files (CorporateRFIDTags) — dedicated
--                 files for corporate billing accounts not in RawChargerUsers
--
--              Note: ChargerUser points TO RFID (N:1). 118 RFIDs are shared
--              by multiple ChargerUsers (fleet cards within same Account).
--
--              When the same rfid_guid appears in both sources, the
--              RawChargerUsers row wins (priority = 0).
--
-- Grain: One row per rfid_guid (~2,637 expected: 2,455 + 182 corporate)
-- Source: Source.RawChargerUsers + Source.CorporateRFIDTags
-- ============================================================================
SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Source"."RFIDs" CASCADE;
CREATE OR REPLACE VIEW "Source"."RFIDs" AS
WITH all_rfids AS (
    -- Leg 1: Original RFID tags from ChargerUsers export
    SELECT
        rfid_guid,
        hex,
        uid,
        rfid_description,
        rfid_tag_type,
        rfid_tag_number,
        0 AS source_priority  -- wins on duplicate rfid_guid
    FROM "Source"."AllRawChargerUsers"
    WHERE rfid_guid IS NOT NULL

    UNION ALL

    -- Leg 2: Corporate RFID tags from dedicated files
    SELECT
        rfid_tag_guid   AS rfid_guid,
        hex,
        uid,
        rfid_description,
        rfid_tag_type,
        rfid_tag_number,
        1 AS source_priority  -- yields to RawChargerUsers on overlap
    FROM "Source"."CorporateRFIDTags"
    WHERE rfid_tag_guid IS NOT NULL
)
SELECT DISTINCT ON (rfid_guid)
    rfid_guid,
    hex,
    uid,
    rfid_description,
    rfid_tag_type,
    rfid_tag_number
FROM all_rfids
ORDER BY rfid_guid, source_priority;
