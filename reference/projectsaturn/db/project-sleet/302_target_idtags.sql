-- ============================================================================
-- View: Target.IdTags
-- Description: API payload for creating Ampeco IdTags from Sleet RFIDs.
--              Two join paths to resolve account_owner_guid:
--              1. RFIDs → ChargerUsers (original RawChargerUsers-based tags)
--              2. RFIDs → CorporateRFIDTags (corporate billing RFID tags that
--                 have no ChargerUser entry)
--              Then looks up target_user_id from user_mapping.
--
--              Status: original tags → always 'enabled'.
--              Corporate tags → 'enabled' if source status is 'Active',
--              'disabled' otherwise.
-- Grain: One row per rfid_guid with valid hex
-- Source: Source.RFIDs + Source.ChargerUsers + Source.CorporateRFIDTags
--         + Mapping.user_mapping + Mapping.rfid_mapping
-- ============================================================================
SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Target"."IdTags";

CREATE OR REPLACE VIEW "Target"."IdTags" AS
WITH 
-- Normalize hex values: trim whitespace, convert to uppercase, filter valid hex
rfid_normalized AS (
    SELECT 
        r.rfid_guid,
        UPPER(BTRIM(r.hex)) AS hex_normalized,
        r.uid,
        r.rfid_description,
        r.rfid_tag_type,
        r.rfid_tag_number
    FROM "Source"."RFIDs" r
    WHERE r.hex IS NOT NULL 
      AND BTRIM(r.hex) != ''
      -- Must be valid hexadecimal pattern
      AND BTRIM(r.hex) ~* '^[0-9a-f]+$'
      -- Exclude zero-only values (e.g., '0', '00', '0000')
      AND BTRIM(r.hex) !~ '^0+$'
),
-- Filter to valid byte lengths: 4, 7, 8, or 10 bytes (8, 14, 16, or 20 hex chars)
rfid_valid_length AS (
    SELECT *
    FROM rfid_normalized
    WHERE LENGTH(hex_normalized) IN (8, 14, 16, 20)
),
-- Deduplicate rfid_tag_number per rfid_guid (prefer non-empty, non-null)
rfid_tag_number_lookup AS (
    SELECT DISTINCT ON (rfid_guid)
        rfid_guid,
        rfid_tag_number
    FROM rfid_valid_length
    WHERE rfid_tag_number IS NOT NULL
      AND BTRIM(rfid_tag_number) != ''
    ORDER BY rfid_guid
),
-- Identify RFIDs that map to multiple DIFFERENT accounts (must be excluded)
rfid_multi_account AS (
    SELECT cu.rfid_guid
    FROM "Source"."ChargerUsers" cu
    WHERE cu.rfid_guid IS NOT NULL
    GROUP BY cu.rfid_guid
    HAVING COUNT(DISTINCT cu.account_owner_guid) > 1
),
-- Get the account_owner_guid and charger_user_name for each RFID (via ChargerUsers)
-- For corporate RFID tags: ChargerUsers yields NULL, fall back to CorporateRFIDTags
-- Use DISTINCT ON to pick one ChargerUser per RFID, preferring non-empty name, then earliest created
rfid_with_account AS (
    SELECT DISTINCT ON (r.rfid_guid)
        r.rfid_guid,
        r.hex_normalized,
        r.uid,
        r.rfid_description,
        r.rfid_tag_type,
        -- account_owner_guid: TEXT-cast for join compatibility with user_mapping.
        -- The final SELECT applies UPPER() on the debug output column to match
        -- the old UUID display format (ODBC returns UUID as UPPERCASE).
        COALESCE(cu.account_owner_guid::TEXT, crt.account_owner_guid) AS account_owner_guid,
        -- charger_user_name: only use CRT org name for corporate-only tags.
        -- COALESCE would leak the org name into existing tags whose
        -- charger_user_name happens to be NULL, changing the idLabel.
        CASE
            WHEN cu.account_owner_guid IS NOT NULL THEN cu.charger_user_name
            ELSE crt.account_owner_name
        END AS charger_user_name,
        -- Status: original (ChargerUsers) tags → always 'enabled'.
        -- Corporate-only tags → respect source status.
        CASE
            WHEN cu.account_owner_guid IS NULL
                 AND crt.rfid_tag_guid IS NOT NULL
                 AND UPPER(BTRIM(COALESCE(crt.status, ''))) != 'ACTIVE'
                 AND BTRIM(COALESCE(crt.status, '')) != ''
            THEN 'disabled'
            ELSE 'enabled'
        END AS resolved_status,
        rtn.rfid_tag_number
    FROM rfid_valid_length r
    LEFT JOIN "Source"."ChargerUsers" cu ON cu.rfid_guid = r.rfid_guid
    LEFT JOIN "Source"."CorporateRFIDTags" crt ON crt.rfid_tag_guid = r.rfid_guid
    LEFT JOIN rfid_tag_number_lookup rtn ON rtn.rfid_guid = r.rfid_guid
    -- Exclude RFIDs linked to multiple different accounts (only applies to ChargerUsers-based tags)
    WHERE r.rfid_guid NOT IN (SELECT rfid_guid FROM rfid_multi_account)
    ORDER BY r.rfid_guid,
        -- Prefer ChargerUsers match over CorporateRFIDTags
        CASE WHEN cu.account_owner_guid IS NOT NULL THEN 0 ELSE 1 END,
        CASE WHEN cu.charger_user_name IS NOT NULL AND BTRIM(cu.charger_user_name) != '' THEN 0 ELSE 1 END,
        cu.created_on NULLS LAST
),
-- Prepare cleaned label components
-- Cleaning rules:
--   - Description: strip "Tag Order -" prefix; treat NULL, '', and literal 'NULL' as blank
--   - User name: treat NULL, '', and literal 'NULL' as blank
--   - Tag number: treat NULL, '', and literal 'NULL' as blank
--   - Usable type: exclude Roaming, Other, AutoCharge, DCS, Virtual, NULL, blank
--   - "Real" description: desc that is NOT pure hex/digits (those are just re-encoded identifiers)
--   - Number redundancy: if UPPER(number) == hex_normalized, number is already visible in app
--   - Deduplication: don't repeat type or number in suffix if already present in base text
label_prep AS (
    SELECT
        rwa.*,
        -- Clean description: strip "Tag Order -" prefix, handle NULL/empty/literal 'NULL'
        NULLIF(BTRIM(
            CASE
                WHEN UPPER(BTRIM(COALESCE(rwa.rfid_description, ''))) IN ('', 'NULL') THEN ''
                WHEN BTRIM(COALESCE(rwa.rfid_description, '')) ILIKE 'Tag Order -%'
                THEN SUBSTRING(BTRIM(rwa.rfid_description) FROM LENGTH('Tag Order -') + 1)
                ELSE BTRIM(rwa.rfid_description)
            END
        ), '') AS clean_desc,
        -- Clean charger_user_name: trim, nullify empty/NULL-text
        CASE WHEN UPPER(BTRIM(COALESCE(rwa.charger_user_name, ''))) IN ('', 'NULL')
             THEN NULL ELSE BTRIM(rwa.charger_user_name) END AS clean_user_name,
        -- Clean rfid_tag_number: trim, nullify empty/NULL-text
        CASE WHEN UPPER(BTRIM(COALESCE(rwa.rfid_tag_number, ''))) IN ('', 'NULL')
             THEN NULL ELSE BTRIM(rwa.rfid_tag_number) END AS clean_tag_number,
        -- Tag type usable for display (exclude non-RFID and generic types)
        CASE
            WHEN UPPER(BTRIM(COALESCE(rwa.rfid_tag_type, '')))
                 NOT IN ('', 'NULL', 'ROAMING', 'OTHER', 'AUTOCHARGE', 'DCS', 'VIRTUAL')
            THEN BTRIM(rwa.rfid_tag_type)
            ELSE NULL
        END AS usable_tag_type,
        -- Does tag number differ from hex? (if same, it's redundant — already shown as UID in app)
        CASE WHEN UPPER(BTRIM(COALESCE(rwa.rfid_tag_number, ''))) != rwa.hex_normalized
                  AND UPPER(BTRIM(COALESCE(rwa.rfid_tag_number, ''))) NOT IN ('', 'NULL')
             THEN TRUE ELSE FALSE
        END AS num_differs_from_hex
    FROM rfid_with_account rwa
),
-- Determine base text and suffix components
-- Priority: real description > charger_user_name > (no base)
-- "Real" = not pure hex/digits pattern (e.g. "Hanne" is real, "804EB21D" is not)
label_base AS (
    SELECT
        lp.*,
        -- Is the cleaned description a "real" human-readable name?
        (lp.clean_desc IS NOT NULL AND lp.clean_desc !~* '^[0-9a-f]+$') AS desc_is_real,
        -- Base text: prefer real description, then user name, else NULL
        CASE
            WHEN lp.clean_desc IS NOT NULL AND lp.clean_desc !~* '^[0-9a-f]+$'
            THEN lp.clean_desc
            WHEN lp.clean_user_name IS NOT NULL
            THEN lp.clean_user_name
            ELSE NULL
        END AS base_text
    FROM label_prep lp
),
-- Compute suffix parts: type and number, suppressed if already in base_text
label_suffix AS (
    SELECT
        lb.*,
        -- Type for suffix: only if not already contained in base_text (case-insensitive)
        CASE WHEN lb.usable_tag_type IS NOT NULL
                  AND (lb.base_text IS NULL
                       OR lb.base_text NOT ILIKE '%' || lb.usable_tag_type || '%')
             THEN lb.usable_tag_type
             ELSE NULL
        END AS suffix_type,
        -- Number for suffix: only if differs from hex AND not already in base_text
        CASE WHEN lb.num_differs_from_hex
                  AND lb.clean_tag_number IS NOT NULL
                  AND (lb.base_text IS NULL
                       OR lb.base_text NOT ILIKE '%' || lb.clean_tag_number || '%')
             THEN lb.clean_tag_number
             ELSE NULL
        END AS suffix_num
    FROM label_base lb
)
SELECT 
    -- Mapping columns (used by CreateIdTags.py)
    'rfid_mapping'::TEXT AS mapping_table,
    'Sleet|RFID|' || ls.rfid_guid AS mapping_key,
    rm.target_idtag_id AS "TargetIdTagsID",
    
    -- User ID from user_mapping (may be NULL if account has no email)
    um.target_user_id AS "userId",
    
    -- IdTag status: original tags always 'enabled', corporate tags respect source status
    ls.resolved_status AS "status",
    
    -- IdTag type: RFID
    'rfid'::TEXT AS "type",
    
    -- The hex value (normalized to uppercase) used as the UID
    ls.hex_normalized AS "idTagUid",
    
    -- Label algorithm (v2):
    --   Hex is NOT included (already shown as idTagUid in the app).
    --   Base text: real description (not pure hex) > charger_user_name > none.
    --   Suffix in parens: (type number) — parts omitted if already in base or redundant with hex.
    --   No base: "type number" without parens, or just number, or empty.
    CASE
        -- Has base text: append suffix in parentheses if any parts present
        WHEN ls.base_text IS NOT NULL THEN
            ls.base_text
            || CASE
                WHEN COALESCE(ls.suffix_type, '') != '' OR COALESCE(ls.suffix_num, '') != ''
                THEN ' (' || BTRIM(CONCAT_WS(' ', ls.suffix_type, ls.suffix_num)) || ')'
                ELSE ''
            END
        -- No base but has usable type: "type number" or just "type"
        WHEN ls.usable_tag_type IS NOT NULL THEN
            CASE
                WHEN ls.num_differs_from_hex AND ls.clean_tag_number IS NOT NULL
                THEN ls.usable_tag_type || ' ' || ls.clean_tag_number
                ELSE ls.usable_tag_type
            END
        -- No base, no type, but has a distinct number: show number only
        WHEN ls.num_differs_from_hex AND ls.clean_tag_number IS NOT NULL
        THEN ls.clean_tag_number
        -- Nothing useful to show
        ELSE ''
    END AS "idLabel",
    
    -- External ID for linking back to source system
    'Sleet:' || ls.rfid_guid AS "externalId",
    
    -- Payment method: not applicable for RFID tags
    NULL::TEXT AS "paymentMethodId",
    
    -- Partner ID: all billing partners own their RFID tags
    bpm.target_partner_id AS "partnerId",
    
    -- Debug columns
    ls.rfid_guid,
    UPPER(ls.account_owner_guid) AS account_owner_guid,
    ls.rfid_tag_type

FROM label_suffix ls
LEFT JOIN "Mapping"."rfid_mapping" rm 
    ON rm.mapping_key = 'Sleet|RFID|' || ls.rfid_guid
LEFT JOIN "Mapping"."user_mapping" um 
    ON um.mapping_key = 'Sleet|Account|' || ls.account_owner_guid
LEFT JOIN "Mapping"."billing_partner_mapping" bpm
    ON bpm.mapping_key = 'Sleet|BillingPartner|' || ls.account_owner_guid;
