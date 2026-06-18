-- ============================================================================
-- SHARED VIEW: MasterPartnerResolution
-- ============================================================================
-- One row per SOURCE LOCATION (keyed by lm.mapping_key).
--
-- Resolves which target_partner_id is the "master" for organisations that span
-- multiple project codes (and thus multiple partners). Also carries the
-- per-project-code target_partner_id so consumers can JOIN this single view
-- instead of both project_code_mapping and a grouping-key lookup.
--
-- Grouping key logic (cascading fallback):
--   1. Normalised org number (digits only) from ehf_org_number or org_number
--   2. 'ACCT:' || account_number  (Location Owner AccountNumber)
--   3. 'LOC:' || mapping_key      (last resort for locations missing both)
--
-- Lives in Mapping schema (not Source) because it depends on mapping tables.
-- No filters applied — consumers (303, 307, 310, etc.) apply their own
-- migrate/merge predicates.
--
-- master_target_partner_id is NULL on first run (no partners created yet).
-- Second run picks up the correct value. MIN() ignores NULLs.
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Mapping"."MasterPartnerResolution" CASCADE;

CREATE OR REPLACE VIEW "Mapping"."MasterPartnerResolution" AS
WITH grouped AS (
    SELECT
        lm.mapping_key,
        lm.project_code,

        -- Grouping key: normalised org number → ACCT:{account_number} → LOC:{mapping_key}
        COALESCE(
            NULLIF(
                REGEXP_REPLACE(
                    btrim(COALESCE(NULLIF(btrim(loc.ehf_org_number), ''), loc.org_number)),
                    '[^0-9]', '', 'g'
                ), ''
            ),
            'ACCT:' || loc.account_number::TEXT,
            'LOC:' || lm.mapping_key
        ) AS grouping_key,

        -- Per-project-code partner (from project_code_mapping)
        pcm.target_partner_id,

        -- Location name (prefer mapping override, fall back to source)
        COALESCE(NULLIF(lm.location_name, ''), loc.location_name) AS location_name,

        -- Partner name (same derivation as Target.Partners)
        COALESCE(loc.location_owner, loc.location_name) AS partner_name

    FROM "Source"."Locations" loc
    JOIN "Mapping"."location_mapping" lm
        ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    LEFT JOIN "Mapping"."project_code_mapping" pcm
        ON pcm.project_code = lm.project_code
)
SELECT
    mapping_key,
    grouping_key,
    project_code,

    -- Location name (preferred: mapping override, fallback: source)
    location_name,

    -- Per-project-code partner ID (same as project_code_mapping.target_partner_id)
    target_partner_id,

    -- Master partner: lowest target_partner_id across all project codes in the org group
    MIN(target_partner_id) OVER (PARTITION BY grouping_key) AS master_target_partner_id,

    -- Master partner name: deterministic org-level name (MIN for consistency)
    -- Mirrors the master_target_partner_id MIN() pattern
    MIN(partner_name) OVER (PARTITION BY grouping_key) AS master_partner_name,

    -- How many distinct project codes (partners) share this grouping key
    -- (window COUNT(DISTINCT) not supported in PG — use a subquery instead)
    (SELECT COUNT(DISTINCT g2.project_code)
     FROM grouped g2
     WHERE g2.grouping_key = grouped.grouping_key
    ) AS partner_count

FROM grouped;
