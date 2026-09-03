-- ============================================================================
-- View: target.report_data_quality_issues
-- Report / quality view (4xx). Drop-and-recreate. Reads from the read-only
-- `laddel` source database plus the shared `target.facility_migration_eligibility`
-- (201) view. Not an Ampeco/SiteTracker payload view — no `mapping_key`, no
-- target-id columns. Entities are described purely via SOURCE table + source
-- primary key (`facility`/`address`/`customer` + their `laddel` id) — never
-- via target-system view/field names.
--
-- One row per (entity, issue_type) — see docs/MigrationPatternGuide reference
-- pattern "long/narrow quality issues view". New issue types are additive
-- (another UNION ALL branch), no schema change needed.
--
-- Scope (`eligible_facilities` CTE): `laddel.facility` whose organization has
-- `migration_status IN ('MIGRATE', 'INVESTIGATE', 'READY')` AND is
-- migration-eligible per `target.facility_migration_eligibility` (201)
-- (`should_not_migrate = 0`). This is deliberately WIDER than the 3xx target
-- views (which use only `('READY', 'MIGRATE')`): `INVESTIGATE` orgs are
-- included here so quality issues surface before an org is promoted, but
-- facilities/orgs already excluded from migration entirely (no chargers, all
-- inactive, dead) are skipped, same as every 3xx view.
--
-- Classification:
--   INFO    - Informational, no action required before migration
--   WARNING - Should be verified; may indicate bad/fallback data
--   ERROR   - Blocks a clean migration; needs a source-data fix
--
-- Issue Types:
--   has_missing_coordinates        [WARNING] - address.latitude/longitude NULL or (0,0)
--   has_coordinates_outside_norway [WARNING] - lat/lon outside Norway bounding box (likely swapped)
--   has_shared_placeholder_coordinates [WARNING] - lat/lon exactly matches >=5 unrelated
--                                               facilities (geocoding fallback constant, not a
--                                               real location); excluded from has_nearby_facility
--   has_invalid_address            [ERROR]   - address.address missing/blank or a junk placeholder
--   has_invalid_postcode           [WARNING] - address.postal_code is not exactly 4 digits
--   has_duplicate_facility_name    [ERROR]   - facility_name not unique among in-scope facilities
--                                               (SiteTracker Site Name must be unique)
--   has_invalid_org_number         [ERROR]   - customer.organization_number missing or not 9 digits
--   has_nearby_facility            [WARNING/ERROR] - another in-scope facility in the SAME
--                                               organization within ~50m (WARNING if 10-50m,
--                                               ERROR if <10m); excludes shared placeholder
--                                               coordinates (see above)
--   has_multiple_facilities        [INFO]    - customer linked to >1 in-scope facility
--                                               (settlement integration supports 1 location/partner today)
--
-- Norway bounding box is an approximation of the mainland extent
-- (lat 57.5-71.5, lon 4.0-31.5) — deliberately excludes Svalbard; revisit if
-- Svalbard facilities are ever in scope.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`report_data_quality_issues`;

CREATE OR REPLACE VIEW `target`.`report_data_quality_issues` AS
WITH eligible_facilities AS (
    -- Text columns are coerced to utf8mb4_0900_ai_ci here (once) because they
    -- are later UNIONed alongside text from `laddel.customer`/`laddel.address`,
    -- which default to a different collation - see repo memory re: MySQL
    -- "Illegal mix of collations".
    SELECT
        f.facility_id                                                        AS facility_id,
        f.facility_name COLLATE utf8mb4_0900_ai_ci                           AS facility_name,
        REGEXP_REPLACE(f.facility_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')
            COLLATE utf8mb4_0900_ai_ci                                       AS facility_name_clean,
        f.organization_id                                                    AS organization_id,
        o.organization_name COLLATE utf8mb4_0900_ai_ci                      AS organization_name,
        o.migration_status COLLATE utf8mb4_0900_ai_ci                       AS migration_status,
        fi.address_id                                                        AS address_id
    FROM `laddel`.`facility` f
    JOIN `laddel`.`organization` o ON o.organization_id = f.organization_id
    JOIN `laddel`.`facility_information` fi ON fi.facility_id = f.facility_id
    JOIN `target`.`facility_migration_eligibility` fme ON fme.facility_id = f.facility_id
    WHERE o.migration_status IN ('MIGRATE', 'INVESTIGATE', 'READY')
      AND fme.should_not_migrate = 0
),

duplicate_facility_names AS (
    SELECT facility_name_clean, COUNT(*) AS name_count
    FROM eligible_facilities
    WHERE facility_name_clean != ''
    GROUP BY facility_name_clean
    HAVING COUNT(*) > 1
),

eligible_customers AS (
    SELECT DISTINCT
        fc.customer_id     AS customer_id,
        ef.facility_id      AS facility_id,
        ef.organization_id  AS organization_id,
        ef.organization_name AS organization_name,
        ef.migration_status AS migration_status
    FROM `laddel`.`facility_contact` fc
    JOIN eligible_facilities ef ON ef.facility_id = fc.facility_id
),

customer_facility_counts AS (
    SELECT
        customer_id,
        COUNT(DISTINCT facility_id)                          AS facility_count,
        GROUP_CONCAT(DISTINCT facility_id ORDER BY facility_id) AS facility_ids,
        MIN(organization_id)                                  AS organization_id,
        MIN(organization_name)                                AS organization_name,
        MIN(migration_status)                                 AS migration_status
    FROM eligible_customers
    GROUP BY customer_id
),

facility_coords AS (
    SELECT
        ef.facility_id, ef.facility_name, ef.organization_id, ef.organization_name, ef.migration_status,
        a.latitude AS latitude, a.longitude AS longitude
    FROM eligible_facilities ef
    JOIN `laddel`.`address` a ON a.address_id = ef.address_id
    WHERE a.latitude IS NOT NULL AND a.longitude IS NOT NULL
      AND NOT (a.latitude = 0 AND a.longitude = 0)
),

-- A (latitude, longitude) pair shared by an anomalous number of distinct
-- facilities is a geocoding-fallback constant, not a real physical location
-- (verified 2026-08-26: one such value was shared by 80 facilities across
-- unrelated cities). Threshold of 5 distinguishes that from a legitimate
-- small cluster (e.g. a housing association split into a few facility rows
-- at the same building). Excluded from the proximity check below so it isn't
-- flooded with false-positive "nearby facility" rows; flagged separately.
placeholder_coordinates AS (
    SELECT fc.latitude, fc.longitude
    FROM facility_coords fc
    GROUP BY fc.latitude, fc.longitude
    HAVING COUNT(DISTINCT fc.facility_id) >= 5
),

facility_coords_clean AS (
    SELECT fc.*
    FROM facility_coords fc
    LEFT JOIN placeholder_coordinates pc
        ON pc.latitude = fc.latitude AND pc.longitude = fc.longitude
    WHERE pc.latitude IS NULL
),

-- Every ordered pair of distinct in-scope facilities with valid, non-placeholder
-- coordinates, with the great-circle distance between them (metres). Both
-- directions are kept (A->B and B->A) so each facility gets its own row for
-- the entity it is described as, rather than only the lower-numbered
-- facility_id.
--
-- Restricted to pairs within the SAME organization (`f2.organization_id =
-- f1.organization_id`): cross-organization "overlap" isn't actionable here
-- (different customers/partners), and the equality join collapses what was
-- an unrestricted N^2 cross join (all in-scope facilities x all in-scope
-- facilities, ~1min runtime) down to just the handful of facilities that
-- share an organization - most organizations have only 1 in-scope facility.
overlap_pairs_raw AS (
    SELECT
        f1.facility_id AS facility_id, f1.facility_name AS facility_name,
        f1.organization_id AS organization_id, f1.organization_name AS organization_name,
        f1.migration_status AS migration_status,
        f2.facility_id AS other_facility_id, f2.facility_name AS other_facility_name,
        6371000 * ACOS(LEAST(1, GREATEST(-1,
            COS(RADIANS(f1.latitude)) * COS(RADIANS(f2.latitude)) * COS(RADIANS(f2.longitude - f1.longitude))
            + SIN(RADIANS(f1.latitude)) * SIN(RADIANS(f2.latitude))
        ))) AS distance_m
    FROM facility_coords_clean f1
    JOIN facility_coords_clean f2
        ON f2.facility_id <> f1.facility_id
       AND f2.organization_id = f1.organization_id
),

overlap_pairs AS (
    SELECT * FROM overlap_pairs_raw WHERE distance_m < 50
)

-- ============================================================================
-- has_missing_coordinates (WARNING)
-- ============================================================================
SELECT
    'WARNING'                                              AS classification,
    'address'                                               AS entity_type,
    CAST(a.address_id AS CHAR)                              AS entity_id,
    ef.facility_name                                         AS entity_name,
    'has_missing_coordinates'                                AS issue_type,
    'Latitude/longitude is NULL or the (0,0) placeholder'    AS issue_reason,
    CONCAT('lat=', COALESCE(CAST(a.latitude AS CHAR), 'NULL'),
           ', lon=', COALESCE(CAST(a.longitude AS CHAR), 'NULL')) AS referenced_value,
    CAST(ef.facility_id AS CHAR)                             AS facility_id,
    ef.organization_id                                       AS organization_id,
    ef.organization_name                                     AS organization_name,
    ef.migration_status                                      AS migration_status
FROM eligible_facilities ef
JOIN `laddel`.`address` a ON a.address_id = ef.address_id
WHERE a.latitude IS NULL OR a.longitude IS NULL OR (a.latitude = 0 AND a.longitude = 0)

UNION ALL

-- ============================================================================
-- has_coordinates_outside_norway (WARNING) - only for coords that already
-- passed the missing/placeholder check above
-- ============================================================================
SELECT
    'WARNING'                                              AS classification,
    'address'                                               AS entity_type,
    CAST(a.address_id AS CHAR)                              AS entity_id,
    ef.facility_name                                         AS entity_name,
    'has_coordinates_outside_norway'                         AS issue_type,
    'Latitude/longitude fall outside the Norway bounding box - possible lat/lon swap' AS issue_reason,
    CONCAT('lat=', CAST(a.latitude AS CHAR), ', lon=', CAST(a.longitude AS CHAR))      AS referenced_value,
    CAST(ef.facility_id AS CHAR)                             AS facility_id,
    ef.organization_id                                       AS organization_id,
    ef.organization_name                                     AS organization_name,
    ef.migration_status                                      AS migration_status
FROM eligible_facilities ef
JOIN `laddel`.`address` a ON a.address_id = ef.address_id
WHERE a.latitude IS NOT NULL AND a.longitude IS NOT NULL
  AND NOT (a.latitude = 0 AND a.longitude = 0)
  AND (a.latitude NOT BETWEEN 57.5 AND 71.5 OR a.longitude NOT BETWEEN 4.0 AND 31.5)

UNION ALL

-- ============================================================================
-- has_shared_placeholder_coordinates (WARNING) - exact lat/lon reused across
-- an anomalous number of unrelated facilities (geocoding fallback constant,
-- same failure mode as the (0,0) placeholder, just a different value)
-- ============================================================================
SELECT
    'WARNING'                                              AS classification,
    'address'                                               AS entity_type,
    CAST(a.address_id AS CHAR)                              AS entity_id,
    ef.facility_name                                         AS entity_name,
    'has_shared_placeholder_coordinates'                     AS issue_type,
    'Latitude/longitude exactly matches several other unrelated facilities - likely a geocoding fallback default, not this facility''s real location' AS issue_reason,
    CONCAT('lat=', CAST(a.latitude AS CHAR), ', lon=', CAST(a.longitude AS CHAR))      AS referenced_value,
    CAST(ef.facility_id AS CHAR)                             AS facility_id,
    ef.organization_id                                       AS organization_id,
    ef.organization_name                                     AS organization_name,
    ef.migration_status                                      AS migration_status
FROM eligible_facilities ef
JOIN `laddel`.`address` a ON a.address_id = ef.address_id
JOIN placeholder_coordinates pc ON pc.latitude = a.latitude AND pc.longitude = a.longitude

UNION ALL

-- ============================================================================
-- has_invalid_address (ERROR)
-- ============================================================================
SELECT
    'ERROR'                                                  AS classification,
    'address'                                                 AS entity_type,
    CAST(a.address_id AS CHAR)                                AS entity_id,
    ef.facility_name                                           AS entity_name,
    'has_invalid_address'                                      AS issue_type,
    'Street address is missing or a junk placeholder value'    AS issue_reason,
    CONCAT('address=', COALESCE(a.address COLLATE utf8mb4_0900_ai_ci, 'NULL')) AS referenced_value,
    CAST(ef.facility_id AS CHAR)                                AS facility_id,
    ef.organization_id                                         AS organization_id,
    ef.organization_name                                       AS organization_name,
    ef.migration_status                                        AS migration_status
FROM eligible_facilities ef
JOIN `laddel`.`address` a ON a.address_id = ef.address_id
WHERE a.address IS NULL
   OR TRIM(a.address) = ''
   OR LOWER(TRIM(a.address)) IN ('-', '0', 'null')

UNION ALL

-- ============================================================================
-- has_invalid_postcode (WARNING)
-- ============================================================================
SELECT
    'WARNING'                                                AS classification,
    'address'                                                 AS entity_type,
    CAST(a.address_id AS CHAR)                                AS entity_id,
    ef.facility_name                                           AS entity_name,
    'has_invalid_postcode'                                     AS issue_type,
    'Postal code is not exactly 4 digits'                       AS issue_reason,
    CONCAT('postal_code=', COALESCE(a.postal_code COLLATE utf8mb4_0900_ai_ci, 'NULL')) AS referenced_value,
    CAST(ef.facility_id AS CHAR)                                AS facility_id,
    ef.organization_id                                         AS organization_id,
    ef.organization_name                                       AS organization_name,
    ef.migration_status                                        AS migration_status
FROM eligible_facilities ef
JOIN `laddel`.`address` a ON a.address_id = ef.address_id
WHERE a.postal_code IS NULL OR TRIM(a.postal_code) NOT REGEXP '^[0-9]{4}$'

UNION ALL

-- ============================================================================
-- has_duplicate_facility_name (ERROR)
-- ============================================================================
SELECT
    'ERROR'                                                  AS classification,
    'facility'                                                 AS entity_type,
    CAST(ef.facility_id AS CHAR)                               AS entity_id,
    ef.facility_name                                           AS entity_name,
    'has_duplicate_facility_name'                              AS issue_type,
    'facility_name is not unique among in-scope facilities - SiteTracker Site Name must be unique' AS issue_reason,
    CONCAT('facility_name=', ef.facility_name_clean, ', duplicate_count=', dfn.name_count) AS referenced_value,
    CAST(ef.facility_id AS CHAR)                                AS facility_id,
    ef.organization_id                                         AS organization_id,
    ef.organization_name                                       AS organization_name,
    ef.migration_status                                        AS migration_status
FROM eligible_facilities ef
JOIN duplicate_facility_names dfn ON dfn.facility_name_clean = ef.facility_name_clean

UNION ALL

-- ============================================================================
-- has_invalid_org_number (ERROR)
-- ============================================================================
SELECT
    'ERROR'                                                  AS classification,
    'customer'                                                 AS entity_type,
    CAST(c.customer_id AS CHAR)                                AS entity_id,
    c.name COLLATE utf8mb4_0900_ai_ci                          AS entity_name,
    'has_invalid_org_number'                                   AS issue_type,
    'organization_number is missing or is not exactly 9 digits (after trim/space removal)' AS issue_reason,
    CONCAT('organization_number=', COALESCE(c.organization_number COLLATE utf8mb4_0900_ai_ci, 'NULL')) AS referenced_value,
    cfc.facility_ids                                           AS facility_id,
    cfc.organization_id                                        AS organization_id,
    cfc.organization_name                                      AS organization_name,
    cfc.migration_status                                       AS migration_status
FROM `laddel`.`customer` c
JOIN customer_facility_counts cfc ON cfc.customer_id = c.customer_id
WHERE c.organization_number IS NULL
   OR REPLACE(TRIM(c.organization_number), ' ', '') NOT REGEXP '^[0-9]{9}$'

UNION ALL

-- ============================================================================
-- has_nearby_facility (WARNING if 10-50m, ERROR if <10m)
-- ============================================================================
SELECT
    CASE WHEN op.distance_m < 10 THEN 'ERROR' ELSE 'WARNING' END AS classification,
    'facility'                                                   AS entity_type,
    CAST(op.facility_id AS CHAR)                                 AS entity_id,
    op.facility_name                                             AS entity_name,
    'has_nearby_facility'                                         AS issue_type,
    CASE
        WHEN op.distance_m < 10 THEN
            CONCAT('ERROR: another in-scope facility is only ', ROUND(op.distance_m, 1),
                   'm away (<10m) - almost certainly the same physical location, duplicate/overlap')
        ELSE
            CONCAT('WARNING: another in-scope facility is ', ROUND(op.distance_m, 1),
                   'm away (<50m) - verify these are not the same physical location')
    END                                                           AS issue_reason,
    CONCAT('other_facility_id=', op.other_facility_id,
           ', other_facility_name=', op.other_facility_name,
           ', distance_m=', ROUND(op.distance_m, 1))            AS referenced_value,
    CAST(op.facility_id AS CHAR)                                AS facility_id,
    op.organization_id                                          AS organization_id,
    op.organization_name                                        AS organization_name,
    op.migration_status                                         AS migration_status
FROM overlap_pairs op

UNION ALL

-- ============================================================================
-- has_multiple_facilities (INFO)
-- ============================================================================
SELECT
    'INFO'                                                    AS classification,
    'customer'                                                  AS entity_type,
    CAST(c.customer_id AS CHAR)                                 AS entity_id,
    c.name COLLATE utf8mb4_0900_ai_ci                           AS entity_name,
    'has_multiple_facilities'                                   AS issue_type,
    'Customer is linked to more than one in-scope facility - settlement integration does not yet support multiple locations per partner' AS issue_reason,
    CONCAT('facility_count=', cfc.facility_count, ', facility_ids=', cfc.facility_ids) AS referenced_value,
    cfc.facility_ids                                            AS facility_id,
    cfc.organization_id                                         AS organization_id,
    cfc.organization_name                                       AS organization_name,
    cfc.migration_status                                        AS migration_status
FROM `laddel`.`customer` c
JOIN customer_facility_counts cfc ON cfc.customer_id = c.customer_id
WHERE cfc.facility_count > 1;
