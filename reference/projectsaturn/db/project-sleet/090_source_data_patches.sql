SET ROLE db_sleetmigration_owner;

-- ============================================================================
-- SOURCE DATA PATCHES
-- ============================================================================
-- Corrects missing or incorrect values in imported source tables.
-- Applied during every database build (after source imports, before views).
--
-- Each patch should:
--   1. Have a comment explaining WHY the data is wrong/missing
--   2. Target a specific row by primary key
--   3. Be idempotent (safe to re-run)
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Location: Byhaven Parkering Skedsmo (W047964)
-- GUID: c91d8dbe-cfd5-e811-a95e-000d3a29ba60
-- ----------------------------------------------------------------------------
-- Source export has no owner account data for this location.
-- Company details from Brønnøysundregistrene (org nr 986 304 185):
--   Lillestrøm Parkering AS, Storgata 16, 2000 Lillestrøm
--   VAT registered
-- Without this patch, Target.Partners produces an empty "name" field
-- and the Ampeco API rejects the partner creation (HTTP 422).
-- ----------------------------------------------------------------------------
UPDATE "Source"."Locations"
SET
    location_owner              = 'Lillestrøm Parkering AS',
    org_number                  = '986304185',
    ehf_org_number              = '986304185',
    email                       = 'post@lillestrom-parkering.no',
    address_2_street_1          = 'Storgata 16',
    address_2_zip_postal_code   = '2000',
    address_2_city              = 'LILLESTRØM'
WHERE "Id" = 'C91D8DBE-CFD5-E811-A95E-000D3A29BA60';


-- ----------------------------------------------------------------------------
-- Charger Status: Restore Active status for chargers deactivated by source refresh
-- ----------------------------------------------------------------------------
-- The charger Excel export (Sleet Active Chargers NO 13_02_2026) was generated
-- AFTER these locations had been migrated to Wattif. Migration deactivates
-- chargers in the source system (Mer/Current), so the export captured them
-- as Inactive. In reality, these chargers are operational at Wattif.
--
-- Affected sites (all migrated before 2026-02-13 export date):
--   W047100  Stoaveien 14                 (migrated 2026-01-30)  10 chargers
--   W047928  Valle Wood                   (migrated 2026-02-04)  24 chargers
--   W047896  Drengsrudhagen 4             (migrated 2026-02-09)   3 chargers
--   W047942  Å Energi Drammen             (migrated 2026-02-09)  19 chargers
--   W047952  Viken Innkvartering AS       (migrated 2026-02-09)   6 chargers
--   W047781  Arbeids- og velferdsdirekt.  (migrated 2026-02-10)  47 chargers
--   W047752  Økernveien 94 AS             (migrated 2026-02-10)  40 chargers
--   W047772  FRO-GA-MO AS                 (migrated 2026-02-11)  25 chargers
--   W047131  VIA Ruseløkkveien 26         (migrated 2026-02-12) 101 chargers
--
-- Total: 275 chargers restored to Active
-- ----------------------------------------------------------------------------
UPDATE "Source"."Chargers"
SET status = 'Active'
WHERE location_nr IN (
    SELECT loc.location_nr
    FROM "Source"."Locations" loc
    JOIN "Mapping"."location_mapping" lm
        ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    WHERE lm.project_code IN (
        'W047100', 'W047928', 'W047896', 'W047942', 'W047952',
        'W047781', 'W047752', 'W047772', 'W047131'
    )
)
AND LOWER(status) != 'active';


-- ----------------------------------------------------------------------------
-- Charger: Rosenholm_02 (W047109) — incorrect serial number
-- GUID: a49002c9-cfd5-e811-a961-000d3a28d891
-- ----------------------------------------------------------------------------
-- Source data has serial '3N 16421 05 007 007' for both Rosenholm_02 and
-- Rosenholm_20. This caused `find_field_asset_by_serial` to map both chargers
-- to the same SiteTracker Field Asset, creating a duplicate mapping.
--
-- Correct serial derived from OCPP Boot Notification charge_box_serial_number:
--   Rosenholm_02: EVB1A22P2RI3N1640505004007502CD38 → 3N 16405 05 004 007
--   Rosenholm_20: EVB1A22P2RI3N1642105007007502DCC7 → 3N 16421 05 007 007
-- ----------------------------------------------------------------------------
UPDATE "Source"."Chargers"
SET controller_serial_number_2 = '3N 16405 05 004 007'
WHERE "Id" = 'A49002C9-CFD5-E811-A961-000D3A28D891';
