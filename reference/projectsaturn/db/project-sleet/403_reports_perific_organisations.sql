-- Perific Organisations Export Views
-- Two versions: Masked (anonymized) and Unmasked (with real location names)

SET ROLE db_sleetmigration_owner;

-- =============================================================================
-- Masked version: All locations use static Wattif EV address
-- Username and contact use project_code for identification
-- =============================================================================
DROP VIEW IF EXISTS "Reports"."PerificOrganisationsMasked";

CREATE OR REPLACE VIEW "Reports"."PerificOrganisationsMasked" AS
SELECT
    lm.project_code AS username,
    lm.project_code || ' Wattif EV as' AS organisation_name,
    'Tollbodalmenningen 5' AS address,
    '5004' AS zip_code,
    'BERGEN' AS city,
    'PerificMax' AS contact_firstname,
    lm.project_code AS contact_lastname,
    'operations+' || lm.project_code || '@wattifev.com' AS contact_email_address,
    '+47' AS contact_telephone_number
FROM "Mapping"."location_mapping" lm
WHERE lm.exclude = FALSE;


-- =============================================================================
-- Unmasked version: Uses real location name from Source.Locations
-- =============================================================================
DROP VIEW IF EXISTS "Reports"."PerificOrganisationsUnmasked";

CREATE OR REPLACE VIEW "Reports"."PerificOrganisationsUnmasked" AS
SELECT
    lm.project_code AS username,
    lm.project_code || ' ' || loc.location_name AS organisation_name,
    'Tollbodalmenningen 5' AS address,
    '5004' AS zip_code,
    'BERGEN' AS city,
    'PerificMax' AS contact_firstname,
    lm.project_code AS contact_lastname,
    'operations+' || lm.project_code || '@wattifev.com' AS contact_email_address,
    '+47' AS contact_telephone_number
FROM "Mapping"."location_mapping" lm
JOIN "Source"."Locations" loc ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
WHERE lm.exclude = FALSE;
