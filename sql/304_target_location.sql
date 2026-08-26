-- ============================================================================
-- View: target.location
-- Depends on: target.location_mapping (003), read-only `laddel` source.
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
--
-- Grain: one location per facility. Maps `laddel` onto the Ampeco
-- "create location" payload (POST /public-api/resources/locations/v2.0,
-- schema locationV2Create). See docs/fieldmapping/location.md.
--
-- Batch gate: facilities whose organization is migration_status = 'READY', AND
-- the facility is migration-eligible per target.facility_migration_eligibility
-- (201) — excludes facilities with no chargers, all chargers inactive, no
-- sessions ever, or no sessions in the last 6 months.
-- externalId comes from target.facility_external_id (202) — the shared
-- W047L + zero-padded facility_id scheme, also reused by the migration
-- status report. The source migration_project_code column is deliberately
-- NOT used.
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
    CONCAT('Laddel|Facility|', f.facility_id)                          AS mapping_key,
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
    fei.external_id                                                    AS `externalId`,

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
JOIN `target`.`facility_migration_eligibility` fme
    ON fme.facility_id = f.facility_id
JOIN `target`.`facility_external_id` fei
    ON fei.facility_id = f.facility_id
LEFT JOIN `target`.`location_mapping` lm
    ON lm.mapping_key = CONCAT('Laddel|Facility|', f.facility_id)
WHERE o.migration_status = 'READY'
  AND fme.should_not_migrate = 0;
