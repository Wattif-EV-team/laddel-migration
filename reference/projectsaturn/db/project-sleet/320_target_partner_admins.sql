-- ============================================================================
-- TARGET VIEW: PartnerAdmins
-- ============================================================================
-- One row per distinct (email, resolved_partner) pair.
--
-- Splits partner_admin_emails (comma/semicolon/space-separated) from
-- location_mapping, resolves each email to a master_target_partner_id via
-- MasterPartnerResolution.
--
-- When an email appears under multiple locations in the SAME org group,
-- it collapses to one row with the master_target_partner_id.
--
-- When an email spans DIFFERENT org groups, target_partner_id is set to NULL
-- and all_target_partner_ids lists the conflicting IDs. The Python script
-- handles these as errors.
--
-- Only includes emails from locations whose partner has been created
-- (master_target_partner_id IS NOT NULL in MasterPartnerResolution).
-- This prevents the script from reporting false cross-org conflicts for
-- emails tied to partners that have not yet been mapped/migrated.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Target"."PartnerAdmins";

CREATE OR REPLACE VIEW "Target"."PartnerAdmins" AS

-- Step 1: Split email strings per location, join to MasterPartnerResolution
WITH raw_emails AS (
    SELECT
        lower(btrim(regexp_replace(token.email_token, '^mailto:', '', 'i'))) AS email,
        mpr.grouping_key,
        mpr.master_target_partner_id,
        loc.location_owner
    FROM "Mapping"."location_mapping" lm
    JOIN "Source"."Locations" loc
        ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    LEFT JOIN "Mapping"."MasterPartnerResolution" mpr
        ON mpr.mapping_key = lm.mapping_key
    CROSS JOIN LATERAL (
        SELECT regexp_split_to_table(lm.partner_admin_emails, '[,;\s]+') AS email_token
    ) token
    WHERE lm.partner_admin_emails IS NOT NULL
      AND lm.exclude = FALSE
      AND btrim(token.email_token) <> ''
      -- Only include locations whose partner has been created (has mapping)
      AND mpr.master_target_partner_id IS NOT NULL
),

-- Step 2: Aggregate per email — resolve partner, detect cross-org conflicts
email_orgs AS (
    SELECT
        email,
        COUNT(DISTINCT grouping_key) AS org_count,
        -- If all locations are in the same org → use the single master partner
        -- If cross-org → NULL (script will error)
        CASE
            WHEN COUNT(DISTINCT grouping_key) = 1
            THEN MIN(master_target_partner_id)
            ELSE NULL
        END AS target_partner_id,
        -- All distinct partner IDs for error reporting
        array_agg(DISTINCT master_target_partner_id ORDER BY master_target_partner_id)
            FILTER (WHERE master_target_partner_id IS NOT NULL) AS all_target_partner_ids
    FROM raw_emails
    GROUP BY email
),

-- Step 3: Resolve display name using location_owner from the master partner's location
-- Pick the location_owner associated with the master partner (stable — same row Target.Partners uses)
email_names AS (
    SELECT DISTINCT ON (re.email)
        re.email,
        re.location_owner
    FROM raw_emails re
    JOIN email_orgs eo ON eo.email = re.email
    WHERE re.master_target_partner_id = eo.target_partner_id
    ORDER BY re.email, re.location_owner
)

SELECT
    -- Mapping columns
    'partner_admin_mapping'::TEXT AS mapping_table,
    'Sleet|PartnerAdmin|' || eo.email AS mapping_key,

    -- Target ID (NULL = needs processing)
    pam.target_partner_admin_id AS "TargetPartnerAdminID",

    -- Partner assignment
    eo.target_partner_id,
    eo.all_target_partner_ids::TEXT AS all_target_partner_ids,

    -- API payload fields
    COALESCE(
        initcap(replace(split_part(eo.email, '@', 1), '.', ' '))
            || ' (' || en.location_owner || ')',
        initcap(replace(split_part(eo.email, '@', 1), '.', ' '))
    ) AS name,
    eo.email,
    'partner'::TEXT AS "adminType",
    21 AS "roleId",
    'nb-NO'::TEXT AS locale

FROM email_orgs eo
LEFT JOIN email_names en ON en.email = eo.email
LEFT JOIN "Mapping"."partner_admin_mapping" pam
    ON pam.mapping_key = 'Sleet|PartnerAdmin|' || eo.email

-- Only unprocessed rows
WHERE pam.target_partner_admin_id IS NULL;
