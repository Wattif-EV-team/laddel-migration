-- ============================================================================
-- View: target.location
-- Depends on: target.location_mapping (003), read-only `laddel` source.
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
--
-- Grain: one location per facility. Maps `laddel` onto the Ampeco
-- "create location" payload (POST /public-api/resources/locations/v2.0,
-- schema locationV2Create). See docs/fieldmapping/location.md.
--
-- Batch gate: facilities whose organization is migration_status = 'READY'.
-- externalId is derived from facility_id (W047L + zero-padded id) — the source
-- migration_project_code column is deliberately NOT used.
--
-- Layout: SOURCE -> TARGET ID -> PAYLOAD (Ampeco field names, underscores for
-- nesting; translated fields emit one column per locale, folded to
-- [{locale, translation}] by the step). `nb-NO` columns need backtick quoting.
--
-- Robust trim: source free-text carries stray Unicode separators/control chars
-- (e.g. U+2028 LINE SEPARATOR) that plain TRIM() does not remove. We strip any
-- leading/trailing run of separator (\p{Z}) or control/format (\p{C}) chars with
-- REGEXP_REPLACE while preserving internal spaces.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`location`;

CREATE OR REPLACE VIEW `target`.`location` AS
SELECT
    -- -- SOURCE ----------------------------------------------------------------
    CONCAT('Laddel|Location|', f.facility_id)                          AS mapping_key,
    CONCAT(
        REGEXP_REPLACE(f.facility_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''),
        ' (fac=', f.facility_id, ')'
    )                                                                  AS source_label,

    -- -- TARGET ID(S) -----------------------------------------------------------
    lm.target_location_id                                              AS target_location_id,

    -- -- PAYLOAD (Ampeco field names, 1:1, in API order) ----------------------
    -- Identity
    REGEXP_REPLACE(f.facility_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '') AS `name_en`,
    REGEXP_REPLACE(f.facility_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '') AS `name_nb-NO`,
    CONCAT('W047L', LPAD(f.facility_id, 4, '0'))                       AS `externalId`,

    -- Geoposition (required; 0,0 placeholders sent as-is for the first iteration)
    a.latitude                                                         AS `geoposition_latitude`,
    a.longitude                                                        AS `geoposition_longitude`,

    -- Address
    CONCAT(
        REGEXP_REPLACE(a.address, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), ', ',
        REGEXP_REPLACE(a.postal_code, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), ' ',
        REGEXP_REPLACE(a.city, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')
    )                                                                  AS `address_en`,
    CONCAT(
        REGEXP_REPLACE(a.address, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), ', ',
        REGEXP_REPLACE(a.postal_code, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), ' ',
        REGEXP_REPLACE(a.city, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')
    )                                                                  AS `address_nb-NO`,
    REGEXP_REPLACE(a.address, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')  AS `streetAddress_en`,
    REGEXP_REPLACE(a.address, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')  AS `streetAddress_nb-NO`,
    REGEXP_REPLACE(a.city, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')     AS `city`,
    REGEXP_REPLACE(a.postal_code, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '') AS `postCode`,
    'NO'                                                               AS `country`,
    ''                                                                 AS `region`,

    -- Descriptions
    CONCAT(
        REGEXP_REPLACE(a.address, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), ', ',
        REGEXP_REPLACE(a.postal_code, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), ' ',
        REGEXP_REPLACE(a.city, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')
    )                                                                  AS `shortDescription_en`,
    CONCAT(
        REGEXP_REPLACE(a.address, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), ', ',
        REGEXP_REPLACE(a.postal_code, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), ' ',
        REGEXP_REPLACE(a.city, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')
    )                                                                  AS `shortDescription_nb-NO`,
    NULLIF(REGEXP_REPLACE(fi.information, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), '') AS `description_en`,
    NULLIF(REGEXP_REPLACE(fi.information, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), '') AS `description_nb-NO`,

    -- Working hours / misc
    1                                                                  AS `workingHours_isAlwaysOpen`,
    '["Owner:Customer","Source:Laddel"]'                               AS `tags`

FROM `laddel`.`facility` f
JOIN `laddel`.`facility_information` fi
    ON fi.facility_id = f.facility_id
JOIN `laddel`.`address` a
    ON a.address_id = fi.address_id
JOIN `laddel`.`organization` o
    ON o.organization_id = f.organization_id
LEFT JOIN `target`.`location_mapping` lm
    ON lm.mapping_key = CONCAT('Laddel|Location|', f.facility_id)
WHERE o.migration_status = 'READY';
