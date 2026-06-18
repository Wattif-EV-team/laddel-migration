-- ============================================================================
-- LOOKUP TABLE: ChargerProductLookup
-- ============================================================================
-- This is a MANAGED lookup table. It is dropped and recreated on each DDL run.
-- Contains charger product metadata for connector type and status derivation.
-- 
-- Key use case: Schneider Smart Wallbox models have a schuko connector on
-- connector_level 2 which should be set to type='schuko' and status='disabled'.
--
-- Also maps charger_product → SiteTracker Item Name for Field Asset creation.
-- The sitetracker_item_name is the lookup key used at runtime to resolve the
-- SiteTracker Item SF ID (environment-agnostic: works sandbox + production).
-- Multiple charger_product variants (e.g. "NON WARRANTY") map to same Item.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Drop and recreate (managed lookup table)
-- CASCADE required because Target.EvseAndConnectors view depends on this table
DROP TABLE IF EXISTS "Mapping"."ChargerProductLookup" CASCADE;

CREATE TABLE "Mapping"."ChargerProductLookup" (
    -- Charger product name (case-insensitive matching via LOWER())
    "charger_product_lower" TEXT PRIMARY KEY,
    
    -- Original charger product name for reference
    "charger_product" TEXT NOT NULL,
    "has_schuko" BOOLEAN NOT NULL DEFAULT FALSE,
    -- Which connector_level is schuko (NULL if no schuko)
    "schuko_connector_level" INTEGER,

    -- Max current per phase (A), from vendor technical data ("Märkström")
    "max_charger_current" INTEGER NOT NULL,

    -- SiteTracker Item Name for Field Asset mapping.
    -- Resolved at runtime via SOQL: SELECT Id FROM sitetracker__Item__c WHERE Name = ?
    -- Items that don't exist yet must be created manually in SiteTracker before migration.
    "sitetracker_item_name" TEXT NOT NULL
);

-- Insert all known charger products
INSERT INTO "Mapping"."ChargerProductLookup"
    ("charger_product_lower", "charger_product", "has_schuko", "schuko_connector_level", "max_charger_current", "sitetracker_item_name")
VALUES
    -- Standard chargers (no schuko)
    ('ebg', 'EBG', FALSE, NULL, 64, 'EBG'),
    ('schneider evlink parking', 'Schneider EVlink Parking', FALSE, NULL, 32, 'Schneider EvLink Pro AC'),
    ('proxll wb2m', 'Proxll WB2M', FALSE, NULL, 64, 'Proxll WB2M'),
    ('vestel evc04-ac22', 'Vestel EVC04-AC22', FALSE, NULL, 32, 'Vestel EVC04-AC22'),
    ('schneider evlink parking non warranty', 'Schneider EVlink Parking NON WARRANTY', FALSE, NULL, 32, 'Schneider EvLink Pro AC'),
    ('ensto evb200', 'Ensto EVB200', FALSE, NULL, 32, 'Ensto EVB200'),
    ('ensto evb200 gen 2', 'Ensto EVB200 Gen 2', FALSE, NULL, 32, 'Ensto EVB200'),
    ('vestel evc04-ac22 iso15118', 'Vestel EVC04-AC22 ISO15118', FALSE, NULL, 32, 'Vestel EVC04-AC22'),

    -- DC fast chargers (no schuko)
    ('alpitronic hyc400 - 400 kw ccs/ccs', 'Alpitronic HYC400 - 400 kW CCS/CCS', FALSE, NULL, 630, 'Alpitronic HYC 200 (100kW and 200kW)'),
    ('kempower c503p480nd6, 600 kw, 4 x ccs', 'Kempower C503P480ND6, 600 kW, 4 x CCS', FALSE, NULL, 1000, 'Kempower C503P480ND6'),
    ('alpitronic hyc50, 2 x ccs', 'Alpitronic HYC50, 2 x CCS', FALSE, NULL, 90, 'Alpitronic HYC 50kW'),
    ('alpitronic hyc400 - 200kw ccs/ccs', 'Alpitronic HYC400 - 200kW CCS/CCS', FALSE, NULL, 320, 'Alpitronic HYC 200 (100kW and 200kW)'),

    -- Schneider Smart Wallbox models (schuko on connector_level 2)
    ('schneider evlink smart wallbox t2 rfid', 'Schneider EvLink Smart Wallbox T2 RFID', TRUE, 2, 32, 'Schneider EvLink Smart Wallbox'),
    ('schneider evlink smart wallbox t2 rfid non warranty', 'Schneider EvLink Smart Wallbox T2 RFID NON WARRANTY', TRUE, 2, 32, 'Schneider EvLink Smart Wallbox'),
    ('schneider evlink smart wallbox t2 te rfid', 'Schneider EVLink Smart Wallbox T2 TE RFID', TRUE, 2, 32, 'Schneider EvLink Smart Wallbox'),
    ('schneider evlink smart wallbox t2 nøkkel non warranty', 'Schneider EVlink Smart Wallbox T2 nøkkel NON WARRANTY', TRUE, 2, 32, 'Schneider EvLink Smart Wallbox'),
    ('schneider evlink smart wallbox t2 te rfid non warranty', 'Schneider EVLink Smart Wallbox T2 TE RFID NON WARRANTY', TRUE, 2, 32, 'Schneider EvLink Smart Wallbox');
