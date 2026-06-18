-- ============================================================================
-- REPORT VIEW: Reports.ProjectCodes
-- ============================================================================
-- Full enumeration of W047000–W047999 project codes for the Norwegian tenant,
-- enriched with location & partner from Ampeco (by externalId), project flag
-- from location_mapping, and EVSE counts.
--
-- Rows where EVSEs exist but no location or partner is found are marked with
-- "?" in the location/partner columns (data-quality / reserved codes).
--
-- All project codes are normalized to UPPER for safe matching.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Reports"."ProjectCodes";

CREATE OR REPLACE VIEW "Reports"."ProjectCodes" AS
WITH all_codes AS (
    -- Full range W047000 – W047999
    SELECT 'W047' || LPAD(n::TEXT, 3, '0') AS project_code
    FROM generate_series(0, 999) n
),
evse_counts AS (
    -- Count EVSEs per project code prefix (first 7 chars of physicalReference)
    SELECT
        UPPER(LEFT(TRIM("physicalReference"), 7)) AS project_code,
        COUNT(*)                                    AS evse_count
    FROM "Ampeco"."EVSEs"
    WHERE UPPER(TRIM("physicalReference")) LIKE 'W047%'
      AND LENGTH(TRIM("physicalReference")) >= 7
    GROUP BY UPPER(LEFT(TRIM("physicalReference"), 7))
),
loc AS (
    -- Ampeco locations by externalId, trimmed/uppercased, first 7 chars as
    -- project code (handles suffixes like W047629/1). Pick lowest Id on dups.
    SELECT DISTINCT ON (UPPER(LEFT(TRIM("externalId"), 7)))
        UPPER(LEFT(TRIM("externalId"), 7))     AS project_code,
        "Id"                                    AS location_id,
        COALESCE("name_en", "name_nb-NO")       AS location_name
    FROM "Ampeco"."Locations"
    WHERE UPPER(TRIM("externalId")) LIKE 'W047%'
    ORDER BY UPPER(LEFT(TRIM("externalId"), 7)), "Id"
),
part AS (
    -- Ampeco partners by externalId, trimmed/uppercased, first 7 chars.
    -- Pick lowest Id on duplicate codes (e.g. W047493 + W047493-1).
    SELECT DISTINCT ON (UPPER(LEFT(TRIM("externalId"), 7)))
        UPPER(LEFT(TRIM("externalId"), 7)) AS project_code,
        "Id"                                AS partner_id,
        "name"                              AS partner_name
    FROM "Ampeco"."Partners"
    WHERE UPPER(TRIM("externalId")) LIKE 'W047%'
    ORDER BY UPPER(LEFT(TRIM("externalId"), 7)), "Id"
),
lm AS (
    -- Project codes present in location_mapping (non-excluded only)
    SELECT DISTINCT
        UPPER(TRIM(project_code)) AS project_code
    FROM "Mapping"."location_mapping"
    WHERE project_code IS NOT NULL
      AND (exclude IS NULL OR exclude = FALSE)
)
SELECT
    ac.project_code,

    -- Location: "?" when EVSEs exist but neither location nor partner found
    CASE
        WHEN loc.location_id IS NULL AND part.partner_id IS NULL AND ec.evse_count IS NOT NULL
            THEN '?'
        ELSE COALESCE(loc.location_name, '')
    END AS location_name,

    CASE
        WHEN loc.location_id IS NULL AND part.partner_id IS NULL AND ec.evse_count IS NOT NULL
            THEN '?'
        ELSE COALESCE(loc.location_id::TEXT, '')
    END AS location_id,

    -- Partner: same "?" rule
    CASE
        WHEN loc.location_id IS NULL AND part.partner_id IS NULL AND ec.evse_count IS NOT NULL
            THEN '?'
        ELSE COALESCE(part.partner_name, '')
    END AS partner_name,

    CASE
        WHEN loc.location_id IS NULL AND part.partner_id IS NULL AND ec.evse_count IS NOT NULL
            THEN '?'
        ELSE COALESCE(part.partner_id::TEXT, '')
    END AS partner_id,

    -- Project flag
    CASE WHEN lm.project_code IS NOT NULL THEN 'Sleet' ELSE '' END AS project,

    -- EVSE count
    COALESCE(ec.evse_count, 0) AS evse_count

FROM all_codes ac
LEFT JOIN loc         ON loc.project_code  = ac.project_code
LEFT JOIN part        ON part.project_code = ac.project_code
LEFT JOIN lm          ON lm.project_code   = ac.project_code
LEFT JOIN evse_counts ec ON ec.project_code = ac.project_code
ORDER BY ac.project_code;
