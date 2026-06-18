SET ROLE db_sleetmigration_owner;

-- Target.Locations view joining Source.Locations with Mapping.LocationMapping
-- Includes coordinate validation/swap logic for Norway bounds
DROP VIEW IF EXISTS "Target"."Locations";

CREATE OR REPLACE VIEW "Target"."Locations" AS
WITH base_locations AS (
    -- CTE 1: Join location_mapping with Source.Locations
    SELECT 
        lm.mapping_key,
        lm.merge_with_mapping_key,
        lm.target_location_id,
        lm.project_code,
        lm.partner_model,
        COALESCE(NULLIF(lm.location_name, ''), loc.location_name) AS location_name,
        loc."Id"::TEXT AS source_location_guid,
        loc.location_nr,
        loc.latitude,
        loc.longitude,
        loc.city,
        loc.postal_code,
        loc.address
    FROM "Mapping"."location_mapping" lm
    JOIN "Source"."Locations" loc ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    WHERE lm.migrate = TRUE 
      AND (lm.merge_with_mapping_key IS NULL 
           OR lm.mapping_key = lm.merge_with_mapping_key)
),
with_coordinates AS (
    -- CTE 2: Coordinate validation with swap logic for Norway bounds
    -- Norway bounds: latitude 57–71°, longitude 4–31°
    -- Primary: CPO source coordinates (with swap correction)
    -- Fallback: Geoapify geocoded coordinates from Source.GeocodedLocations
    -- Last resort: 0 (should not happen — all 179 locations are geocoded)
    SELECT 
        bl.*,
        CASE 
            WHEN bl.latitude BETWEEN 57 AND 71 AND bl.longitude BETWEEN 4 AND 31 
                THEN bl.latitude
            WHEN bl.longitude BETWEEN 57 AND 71 AND bl.latitude BETWEEN 4 AND 31 
                THEN bl.longitude  -- swapped
            WHEN geo.latitude IS NOT NULL THEN geo.latitude  -- geocoded fallback
            ELSE 0
        END AS geoposition_latitude,
        CASE 
            WHEN bl.latitude BETWEEN 57 AND 71 AND bl.longitude BETWEEN 4 AND 31 
                THEN bl.longitude
            WHEN bl.longitude BETWEEN 57 AND 71 AND bl.latitude BETWEEN 4 AND 31 
                THEN bl.latitude  -- swapped
            WHEN geo.longitude IS NOT NULL THEN geo.longitude  -- geocoded fallback
            ELSE 0
        END AS geoposition_longitude,
        -- Tags: Source:MerB2B + Owner based on partner_model (CO* = Customer, WO* = Wattif)
        '["Source:MerB2B","Owner:' || 
            CASE 
                WHEN UPPER(bl.partner_model) LIKE 'WO%' THEN 'Wattif'
                ELSE 'Customer'
            END || '"]' AS tags
    FROM base_locations bl
    LEFT JOIN "Source"."GeocodedLocations" geo ON geo.location_guid = bl.source_location_guid
),
with_availability AS (
    -- CTE 3: Compute is_public flag based on charger availability
    SELECT 
        wc.*,
        EXISTS (
            SELECT 1 
            FROM "Source"."Chargers" c 
            WHERE c.location_nr = wc.location_nr 
              AND c.availability = 'Public'
        ) AS is_public
    FROM with_coordinates wc
)
-- Final SELECT: Map all columns per specification
SELECT 
    -- Mapping columns
    'location_mapping'::TEXT AS mapping_table,
    wa.mapping_key,
    wa.merge_with_mapping_key,
    
    -- Target location ID
    wa.target_location_id AS "TargetLocationID",
    
    -- Static values
    'enabled'::TEXT AS "status",
    
    -- Validated coordinates
    wa.geoposition_latitude,
    wa.geoposition_longitude,
    
    -- Location info
    'NO'::TEXT AS "country",
    wa.city,
    ''::TEXT AS "region",
    wa.postal_code::TEXT AS "postCode",
    wa.project_code AS "externalId",
    wa.tags::TEXT AS "tags",
    
    -- Name (same for both locales)
    wa.location_name AS "name_en",
    wa.location_name AS "name_nb-NO",
    
    -- Short description: address, city
    (wa.address || ', ' || wa.city) AS "shortDescription_en",
    (wa.address || ', ' || wa.city) AS "shortDescription_nb-NO",
    
    -- Description (depends on public/private)
    CASE 
        WHEN wa.is_public THEN 
            'Welcome! To charge at ' || wa.location_name || ', download the Wattif app.'
        ELSE 
            'Closed charging facility for ' || wa.location_name || '. Access requires an invitation and an active subscription. Contact the site administrator to receive an invitation. Charging is managed via the Wattif app.'
    END AS "description_en",
    CASE 
        WHEN wa.is_public THEN 
            'Velkommen! Last ned Wattif-appen for å få tilgang til å lade ved ' || wa.location_name || '.'
        ELSE 
            'Lukket ladeanlegg for ' || wa.location_name || '. Tilgang krever invitasjon og et aktivt abonnement. Kontakt eiendommens eier for å få invitasjon og tilgang. Lading administreres via Wattif-appen.'
    END AS "description_nb-NO",
    
    -- Additional description (static)
    'Don''t forget to check the parking rules and pay any applicable fee.'::TEXT AS "additionalDescription_en",
    'Ikke glem å sjekke parkeringsreglene og betale eventuell avgift.'::TEXT AS "additionalDescription_nb-NO",
    
    -- Full address: address, postal_code city
    (wa.address || ', ' || wa.postal_code || ' ' || wa.city) AS "address_en",
    (wa.address || ', ' || wa.postal_code || ' ' || wa.city) AS "address_nb-NO",
    
    -- Street address only
    wa.address AS "streetAddress_en",
    wa.address AS "streetAddress_nb-NO",
    
    -- Debug columns
    wa.source_location_guid,
    wa.location_nr

FROM with_availability wa;
