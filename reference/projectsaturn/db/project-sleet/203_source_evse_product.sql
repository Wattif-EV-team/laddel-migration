-- ============================================================================
-- SHARED VIEW: Source.EvseProduct
-- ============================================================================
-- Derives the pricing "Product" for each connector (EVSE) from its connector
-- type and power characteristics. This product is the filter key used on
-- PriceListItems to find matching tariff items.
--
-- Product derivation (from final_pricing_transform_algorithm.md Step 1):
--   AC connectors:
--     'AC'        → 'Flexi Lading'
--     'AC Gratis' → 'Flexi Lading Gratis'
--     'AC ex VAT' → 'Flexilading uten fradrag'
--   DC connectors (power_type = 'DC' or connector_type ILIKE '%CCS%'):
--     max_effect >= 150 kW → 'Superhurtig Lading'
--     max_effect <  150 kW → 'Hurtig Lading'
--
-- Grain: One row per connector (unfiltered — downstream views filter to
--        migrating locations).
--
-- Source: Source.Connectors → Source.Chargers
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Source"."EvseProduct" CASCADE;

CREATE OR REPLACE VIEW "Source"."EvseProduct" AS
SELECT
    conn."Id"       AS connector_guid,
    conn.id         AS charger_id,           -- FK to Chargers.charger_id
    conn.connector_level,
    conn.connector_type,
    conn.power_type,
    conn.max_effect,
    c."Id"          AS charger_guid,
    c.location_nr,

    -- Product derivation
    CASE
        WHEN conn.connector_type = 'AC'        THEN 'Flexi Lading'
        WHEN conn.connector_type = 'AC Gratis' THEN 'Flexi Lading Gratis'
        WHEN conn.connector_type = 'AC ex VAT' THEN 'Flexilading uten fradrag'
        WHEN conn.power_type = 'DC'
          OR conn.connector_type ILIKE '%CCS%'  THEN
            CASE
                WHEN conn.max_effect::NUMERIC >= 150 THEN 'Superhurtig Lading'
                ELSE 'Hurtig Lading'
            END
        ELSE 'Flexi Lading'  -- fallback for unexpected types
    END AS product

FROM "Source"."Connectors" conn
JOIN "Source"."Chargers" c ON c.charger_id = conn.id;
