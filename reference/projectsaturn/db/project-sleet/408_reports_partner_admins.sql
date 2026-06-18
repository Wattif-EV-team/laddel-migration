-- ============================================================================
-- REPORT VIEW: PartnerAdmins
-- ============================================================================
-- Unfiltered report of all partner admin emails from the Master planning Excel.
-- Used for Excel export and human review — not for API calls.
--
-- Shows all emails (including already-created), their resolved partner,
-- org grouping info, and quality issue flags for manual resolution.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Reports"."PartnerAdmins";

CREATE OR REPLACE VIEW "Reports"."PartnerAdmins" AS

-- Step 1: Split email strings per location, join to MasterPartnerResolution
WITH raw_emails AS (
    SELECT
        lower(btrim(token.email_token)) AS email,
        mpr.grouping_key,
        mpr.master_target_partner_id,
        mpr.target_partner_id,
        mpr.partner_count,
        loc.location_owner,
        loc.org_number,
        lm.mapping_key AS location_mapping_key,
        lm.project_code,
        COALESCE(NULLIF(lm.location_name, ''), loc.location_name) AS location_name
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
),

-- Step 2: Aggregate per email
email_orgs AS (
    SELECT
        email,
        COUNT(DISTINCT grouping_key) AS org_count,
        COUNT(DISTINCT location_mapping_key) AS location_count,
        CASE
            WHEN COUNT(DISTINCT grouping_key) = 1
            THEN MIN(master_target_partner_id)
            ELSE NULL
        END AS target_partner_id,
        array_agg(DISTINCT master_target_partner_id ORDER BY master_target_partner_id)
            FILTER (WHERE master_target_partner_id IS NOT NULL) AS all_target_partner_ids,
        array_agg(DISTINCT target_partner_id ORDER BY target_partner_id)
            FILTER (WHERE target_partner_id IS NOT NULL) AS all_partner_ids,
        string_agg(DISTINCT project_code, ', ' ORDER BY project_code)
            FILTER (WHERE project_code IS NOT NULL) AS project_codes,
        -- Location names ordered by project_code: use sub-select to control sort
        (SELECT string_agg(sub.ln, ', ' ORDER BY sub.pc)
         FROM (
             SELECT DISTINCT re2.project_code AS pc, re2.location_name AS ln
             FROM raw_emails re2
             WHERE re2.email = raw_emails.email
         ) sub
        ) AS location_names,
        MIN(grouping_key) AS grouping_key,
        MAX(partner_count) AS partner_count,
        MIN(org_number) AS org_number
    FROM raw_emails
    GROUP BY email
),

-- Step 3: Resolve display name from master partner's location
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
    eo.email,
    COALESCE(
        initcap(replace(split_part(eo.email, '@', 1), '.', ' '))
            || ' (' || en.location_owner || ')',
        initcap(replace(split_part(eo.email, '@', 1), '.', ' '))
    ) AS name,
    eo.target_partner_id,
    eo.all_target_partner_ids::TEXT AS all_target_partner_ids,
    eo.all_partner_ids::TEXT AS all_partner_ids,
    eo.project_codes,
    eo.location_names,
    eo.grouping_key,
    eo.org_count,
    eo.partner_count,
    eo.location_count,
    en.location_owner,
    eo.org_number,

    -- Already created?
    (pam.target_partner_admin_id IS NOT NULL) AS already_created,
    pam.target_partner_admin_id,

    -- Quality / status message
    CASE
        WHEN eo.org_count > 1
            THEN 'Email is tied to multiple organisations (' || eo.org_count || '). Cannot auto-assign to a single partner. Manual resolution required.'
        WHEN upper(eo.email) LIKE '%NOTINUSE%'
            THEN 'Email contains "notinuse" — likely a placeholder, not a real address.'
        WHEN eo.email NOT LIKE '%@%'
            THEN 'Email has no @-sign — invalid syntax.'
        WHEN pam.target_partner_admin_id IS NOT NULL
            THEN NULL
        WHEN eo.target_partner_id IS NOT NULL
            THEN 'Not created — Probably tied to sub-operator or other partner.'
        ELSE NULL
    END AS quality_issue

FROM email_orgs eo
LEFT JOIN email_names en ON en.email = eo.email
LEFT JOIN "Mapping"."partner_admin_mapping" pam
    ON pam.mapping_key = 'Sleet|PartnerAdmin|' || eo.email;
