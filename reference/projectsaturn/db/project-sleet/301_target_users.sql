-- ============================================================================
-- View: Target.Users
-- Description: API payload for creating/updating Ampeco users from Sleet accounts.
--              Two account sources combined via UNION ALL + DISTINCT ON:
--              1. ChargerUserAccounts (original path via RawChargerUsers)
--              2. CorporateRFIDTags accounts (corporate billing RFID accounts
--                 not present in RawChargerUsers, location-checked via
--                 hard-coded location_guid)
--              One user per account_owner_guid. Excludes accounts without email.
--              Includes accounts linked to at least one location with
--              migrate = TRUE, OR accounts that already have a target_user_id
--              (so updates can be applied to previously created users).
-- Grain: One row per account_owner_guid with valid email and (migrating location OR existing target)
-- Source: Source.ChargerUserAccounts + Source.CorporateRFIDTags
--         + Mapping.user_mapping + Mapping.location_mapping
-- ============================================================================
SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Target"."Users";

CREATE OR REPLACE VIEW "Target"."Users" AS

-- Leg 1: Accounts from ChargerUserAccounts (original path)
WITH charger_base AS (
    SELECT 
        a.account_owner_guid::TEXT AS account_owner_guid,
        a.account_owner_name,
        a.account_classification,
        a.email,
        a.main_phone,
        a.address_1_street_1,
        a.address_1_city,
        a.address_1_postal_code::TEXT AS address_1_postal_code,
        m.target_user_id,
        regexp_replace(COALESCE(a.main_phone, ''), '[^0-9]', '', 'g') AS phone_digits,
        0 AS source_priority  -- wins on duplicate account_owner_guid
    FROM "Source"."ChargerUserAccounts" a
    LEFT JOIN "Mapping"."user_mapping" m 
        ON m.mapping_key = 'Sleet|Account|' || a.account_owner_guid
    WHERE a.email IS NOT NULL AND TRIM(a.email) <> ''
      AND (
          m.target_user_id IS NOT NULL
          OR
          EXISTS (
              SELECT 1
              FROM "Source"."AllRawChargerUsers" r
              JOIN "Mapping"."location_mapping" lm
                  ON lm.mapping_key = 'Sleet|Location|' || r.location_guid
              WHERE r.account_owner_guid = a.account_owner_guid
                AND lm.migrate = TRUE
          )
      )
),

-- Leg 2: Corporate RFID accounts (not in RawChargerUsers)
corporate_base AS (
    SELECT DISTINCT ON (crt.account_owner_guid)
        crt.account_owner_guid,
        crt.account_owner_name,
        crt.account_classification,
        crt.email,
        crt.main_phone,
        crt.address_1_street_1,
        crt.address_1_city                       AS address_1_city,
        crt.address_1_postal_code,
        m.target_user_id,
        regexp_replace(COALESCE(crt.main_phone, ''), '[^0-9]', '', 'g') AS phone_digits,
        1 AS source_priority  -- yields to ChargerUserAccounts on overlap
    FROM "Source"."CorporateRFIDTags" crt
    LEFT JOIN "Mapping"."user_mapping" m
        ON m.mapping_key = 'Sleet|Account|' || crt.account_owner_guid
    WHERE crt.email IS NOT NULL AND TRIM(crt.email) <> ''
      AND (
          m.target_user_id IS NOT NULL
          OR
          EXISTS (
              SELECT 1
              FROM "Mapping"."location_mapping" lm
              WHERE lm.mapping_key = 'Sleet|Location|' || crt.location_guid
                AND lm.migrate = TRUE
          )
      )
    ORDER BY crt.account_owner_guid
),

-- Merge both legs, prefer ChargerUserAccounts row on overlap
base AS (
    SELECT DISTINCT ON (account_owner_guid)
        account_owner_guid,
        account_owner_name,
        account_classification,
        email,
        main_phone,
        address_1_street_1,
        address_1_city,
        address_1_postal_code,
        target_user_id,
        phone_digits
    FROM (
        SELECT * FROM charger_base
        UNION ALL
        SELECT * FROM corporate_base
    ) combined
    ORDER BY account_owner_guid, source_priority
),
phone_normalized AS (
    SELECT 
        b.*,
        -- Normalize Norwegian phone: strip leading 47 if 10 digits, keep 8-digit local
        CASE 
            WHEN LENGTH(b.phone_digits) = 8 THEN b.phone_digits
            WHEN LENGTH(b.phone_digits) = 10 AND LEFT(b.phone_digits, 2) = '47' THEN RIGHT(b.phone_digits, 8)
            WHEN LENGTH(b.phone_digits) = 11 AND LEFT(b.phone_digits, 3) = '047' THEN RIGHT(b.phone_digits, 8)
            WHEN LENGTH(b.phone_digits) = 12 AND LEFT(b.phone_digits, 4) = '0047' THEN RIGHT(b.phone_digits, 8)
            ELSE b.phone_digits
        END AS local_number
    FROM base b
)
SELECT 
    -- Mapping columns (used by CreateUsers.py)
    'user_mapping'::TEXT AS mapping_table,
    'Sleet|Account|' || p.account_owner_guid AS mapping_key,
    p.target_user_id AS "TargetUserID",
    
    -- Email (required, already validated as non-empty by WHERE clause)
    p.email AS "email",
    
    -- Fixed verification timestamp (users need to verify on first login)
    '2000-01-01 12:00:00+00'::TEXT AS "emailVerified",
    
    -- Require password reset on first login
    TRUE AS "requirePasswordReset",
    
    -- Name handling based on account_classification:
    -- Private Person: split name into first/last
    -- Company/Public: use full name as first_name, '-' as last_name
    CASE 
        WHEN p.account_classification = 'Private Person' THEN
            COALESCE(NULLIF(TRIM(SPLIT_PART(p.account_owner_name, ' ', 1)), ''), p.email)
        ELSE
            COALESCE(NULLIF(TRIM(p.account_owner_name), ''), p.email)
    END AS "first_name",
    
    -- Middle name: not available in source
    NULL::TEXT AS "middle_name",
    
    -- Last name: for Private Person split after first space, for others use '-'
    CASE 
        WHEN p.account_classification = 'Private Person' THEN
            COALESCE(
                NULLIF(TRIM(
                    CASE 
                        WHEN POSITION(' ' IN COALESCE(p.account_owner_name, '')) > 0 
                        THEN SUBSTRING(p.account_owner_name FROM POSITION(' ' IN p.account_owner_name) + 1)
                        ELSE ''
                    END
                ), ''),
                '-'
            )
        ELSE '-'
    END AS "last_name",
    
    -- Phone: normalized Norwegian format +47XXXXXXXX
    CASE 
        WHEN p.local_number IS NOT NULL AND LENGTH(p.local_number) = 8 
        THEN '+47' || p.local_number
        ELSE NULL
    END AS "phone",
    
    -- Country: default to Norway (Sleet operates in NO)
    'NO'::TEXT AS "country",
    
    -- City: default to '-' if empty
    COALESCE(NULLIF(TRIM(p.address_1_city), ''), '-') AS "city",
    
    -- Post code: default to '-' if empty
    COALESCE(NULLIF(TRIM(p.address_1_postal_code), ''), '-') AS "post_code",
    
    -- Address
    p.address_1_street_1 AS "address",
    
    -- External ID for linking back to source system
    'Sleet:' || p.account_owner_guid AS "externalId",
    
    -- Session options: allow multiple simultaneous sessions both remotely and via ID tags
    -- (37.6% of Private Persons and 62.8% of Companies have multiple RFID tags)
    'multiple_simultaneous_sessions_remotely_and_idtags'::TEXT AS "options_sessionsAllowed",
    
    -- Marketing opt-in: default to false
    FALSE AS "receiveNewsAndPromotions",
    
    -- Source identifier for CreateUsers.py mapping detection (Sleet uses account_owner_guid)
    -- UPPER() to match ODBC driver behavior (returns UUID columns as uppercase)
    UPPER(p.account_owner_guid) AS "account_owner_guid"

FROM phone_normalized p;
