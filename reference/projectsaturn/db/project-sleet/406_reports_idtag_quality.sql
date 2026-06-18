-- ============================================================================
-- QUALITY CHECK VIEW: IdTagQualityIssues
-- ============================================================================
-- Reports data quality issues that may affect IdTag/RFID migration.
-- Scope: All RFIDs from Source.RFIDs (excludes rfid_tag_type = 'Virtual')
--
-- Classification:
--   INFO    - Data observation, no action needed
--   WARNING - Data may need verification or uses fallback
--   ERROR   - Major problems that require resolution before migration
--
-- Issue Types:
--   1. multi_account_rfid       [ERROR]   - RFID linked to multiple different accounts
--   2. invalid_hex_pattern      [WARNING] - Hex value contains non-hex characters
--   3. uid_hex_mismatch         [WARNING] - UID decimal doesn't match hex (endianness issue)
--   4. uid_is_hex_fallback      [WARNING] - Hex invalid but UID contains valid hex (fallback available)
--   5. zero_only_hex            [INFO]    - Hex value is all zeros (excluded from migration)
--   6. invalid_byte_length      [WARNING] - Hex length not valid RFID byte count (4/7/8/10 bytes)
--   7. account_no_email         [WARNING] - Account has no email, user not created, RFID cannot be linked
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Reports"."IdTagQualityIssues";

CREATE OR REPLACE VIEW "Reports"."IdTagQualityIssues" AS

-- Issue 1: RFIDs linked to multiple different accounts (ERROR)
-- These cannot be migrated as we don't know which user should own the tag
SELECT 
    'ERROR'::TEXT AS classification,
    'RFID'::TEXT AS entity_type,
    r.rfid_guid::TEXT AS entity_id,
    COALESCE(r.rfid_description, r.hex, '[no description]') AS entity_name,
    'multi_account_rfid'::TEXT AS issue_type,
    'RFID is linked to ' || ma.account_count || ' different accounts - cannot determine owner'::TEXT AS issue_reason,
    r.hex AS hex_value,
    r.uid AS uid_value,
    r.rfid_tag_type,
    ma.account_count::TEXT AS detail_value
FROM "Source"."RFIDs" r
JOIN (
    SELECT cu.rfid_guid, COUNT(DISTINCT cu.account_owner_guid) AS account_count
    FROM "Source"."ChargerUsers" cu
    WHERE cu.rfid_guid IS NOT NULL
    GROUP BY cu.rfid_guid
    HAVING COUNT(DISTINCT cu.account_owner_guid) > 1
) ma ON ma.rfid_guid = r.rfid_guid
WHERE COALESCE(r.rfid_tag_type, '') != 'Virtual'

UNION ALL

-- Issue 2: Invalid hex pattern (WARNING)
-- Hex value contains non-hex characters
SELECT 
    'WARNING'::TEXT AS classification,
    'RFID'::TEXT AS entity_type,
    r.rfid_guid::TEXT AS entity_id,
    COALESCE(r.rfid_description, r.hex, '[no description]') AS entity_name,
    'invalid_hex_pattern'::TEXT AS issue_type,
    'Hex value contains non-hexadecimal characters'::TEXT AS issue_reason,
    r.hex AS hex_value,
    r.uid AS uid_value,
    r.rfid_tag_type,
    r.hex AS detail_value
FROM "Source"."RFIDs" r
WHERE COALESCE(r.rfid_tag_type, '') != 'Virtual'
  AND r.hex IS NOT NULL 
  AND BTRIM(r.hex) != ''
  AND NOT (BTRIM(r.hex) ~* '^[0-9a-f]+$')

UNION ALL

-- Issue 3: UID decimal doesn't match hex - endianness issue (WARNING)
-- The UID (as decimal) converted to hex doesn't match the hex column even after zero-padding
-- This happens with Elbilforeningen tags where bytes are swapped (little-endian vs big-endian)
-- Only flags TRUE mismatches where the values differ even when padded to same length
SELECT 
    'WARNING'::TEXT AS classification,
    'RFID'::TEXT AS entity_type,
    r.rfid_guid::TEXT AS entity_id,
    COALESCE(r.rfid_description, r.hex, '[no description]') AS entity_name,
    'uid_hex_mismatch'::TEXT AS issue_type,
    'UID decimal converted to hex does not match hex column (possible endianness issue)'::TEXT AS issue_reason,
    r.hex AS hex_value,
    r.uid AS uid_value,
    r.rfid_tag_type,
    'uid_as_hex=' || UPPER(TO_HEX(r.uid::BIGINT)) AS detail_value
FROM "Source"."RFIDs" r
WHERE COALESCE(r.rfid_tag_type, '') != 'Virtual'
  AND r.hex IS NOT NULL 
  AND BTRIM(r.hex) != ''
  AND BTRIM(r.hex) ~* '^[0-9a-f]+$'
  AND NOT (BTRIM(r.hex) ~* '^0+$')
  AND r.uid IS NOT NULL
  AND r.uid ~ '^[0-9]+$'  -- uid is decimal
  -- Only flag if hex length matches expected (4 bytes = 8 chars for MIFARE Classic)
  -- and the values don't match when zero-padded to same length
  AND LENGTH(BTRIM(r.hex)) = 8  -- Only check 4-byte tags where uid should match hex exactly
  AND UPPER(LPAD(BTRIM(r.hex), 8, '0')) != UPPER(LPAD(TO_HEX(r.uid::BIGINT), 8, '0'))

UNION ALL

-- Issue 4: Hex is invalid but UID contains valid hex - fallback available (WARNING)
-- The hex column is unusable but uid column has valid hex we could use
SELECT 
    'WARNING'::TEXT AS classification,
    'RFID'::TEXT AS entity_type,
    r.rfid_guid::TEXT AS entity_id,
    COALESCE(r.rfid_description, r.hex, '[no description]') AS entity_name,
    'uid_is_hex_fallback'::TEXT AS issue_type,
    'Hex column invalid but UID contains valid ' || LENGTH(BTRIM(r.uid)) || '-char hex - consider using UID as fallback'::TEXT AS issue_reason,
    r.hex AS hex_value,
    r.uid AS uid_value,
    r.rfid_tag_type,
    r.uid AS detail_value
FROM "Source"."RFIDs" r
WHERE COALESCE(r.rfid_tag_type, '') != 'Virtual'
  AND r.uid IS NOT NULL
  AND BTRIM(r.uid) ~* '^[0-9a-f]+$'  -- uid is valid hex
  AND LENGTH(BTRIM(r.uid)) IN (8, 14, 16, 20)  -- valid byte length
  AND NOT (r.uid ~ '^[0-9]+$')  -- uid is NOT purely decimal (so it must be hex)
  -- hex is invalid or wrong length
  AND (
    r.hex IS NULL 
    OR BTRIM(r.hex) = ''
    OR NOT (BTRIM(r.hex) ~* '^[0-9a-f]+$')
    OR LENGTH(BTRIM(r.hex)) NOT IN (8, 14, 16, 20)
  )

UNION ALL

-- Issue 5: Zero-only hex values (INFO)
-- These are excluded from migration but worth noting
SELECT 
    'INFO'::TEXT AS classification,
    'RFID'::TEXT AS entity_type,
    r.rfid_guid::TEXT AS entity_id,
    COALESCE(r.rfid_description, r.hex, '[no description]') AS entity_name,
    'zero_only_hex'::TEXT AS issue_type,
    'Hex value is all zeros - excluded from migration'::TEXT AS issue_reason,
    r.hex AS hex_value,
    r.uid AS uid_value,
    r.rfid_tag_type,
    r.hex AS detail_value
FROM "Source"."RFIDs" r
WHERE COALESCE(r.rfid_tag_type, '') != 'Virtual'
  AND r.hex IS NOT NULL
  AND BTRIM(r.hex) ~* '^0+$'

UNION ALL

-- Issue 6: Invalid byte length (WARNING)
-- Hex is valid but not a standard RFID byte count (4, 7, 8, or 10 bytes)
SELECT 
    'WARNING'::TEXT AS classification,
    'RFID'::TEXT AS entity_type,
    r.rfid_guid::TEXT AS entity_id,
    COALESCE(r.rfid_description, r.hex, '[no description]') AS entity_name,
    'invalid_byte_length'::TEXT AS issue_type,
    'Hex length ' || LENGTH(BTRIM(r.hex)) || ' chars (' || (LENGTH(BTRIM(r.hex))/2) || ' bytes) is not standard RFID size (4/7/8/10 bytes)'::TEXT AS issue_reason,
    r.hex AS hex_value,
    r.uid AS uid_value,
    r.rfid_tag_type,
    'length=' || LENGTH(BTRIM(r.hex)) AS detail_value
FROM "Source"."RFIDs" r
WHERE COALESCE(r.rfid_tag_type, '') != 'Virtual'
  AND r.hex IS NOT NULL 
  AND BTRIM(r.hex) != ''
  AND BTRIM(r.hex) ~* '^[0-9a-f]+$'
  AND NOT (BTRIM(r.hex) ~* '^0+$')
  AND LENGTH(BTRIM(r.hex)) NOT IN (8, 14, 16, 20)

UNION ALL

-- Issue 7: Account has no email - user not created, RFID cannot be linked (WARNING)
-- These RFIDs have valid hex but the associated account has no email,
-- so no user was created in Ampeco and the RFID cannot be assigned to anyone.
-- Checks both ChargerUsers (original path) and CorporateRFIDTags (corporate path)
-- for account resolution and email.
SELECT 
    'WARNING'::TEXT AS classification,
    'RFID'::TEXT AS entity_type,
    r.rfid_guid::TEXT AS entity_id,
    COALESCE(r.rfid_description, r.hex, '[no description]') AS entity_name,
    'account_no_email'::TEXT AS issue_type,
    'Account has no email address - user not created, RFID cannot be assigned'::TEXT AS issue_reason,
    r.hex AS hex_value,
    r.uid AS uid_value,
    r.rfid_tag_type,
    'account_guid=' || COALESCE(cu.account_owner_guid::TEXT, crt.account_owner_guid, 'NULL') AS detail_value
FROM "Source"."RFIDs" r
-- Join to get the account owner for this RFID (original path via ChargerUsers)
LEFT JOIN (
    SELECT DISTINCT rfid_guid, account_owner_guid
    FROM "Source"."ChargerUsers"
    WHERE rfid_guid IS NOT NULL
) cu ON cu.rfid_guid = r.rfid_guid
-- Join to get account details (original path)
LEFT JOIN "Source"."ChargerUserAccounts" a ON a.account_owner_guid = cu.account_owner_guid
-- Join to get corporate RFID account data (corporate path)
LEFT JOIN "Source"."CorporateRFIDTags" crt ON crt.rfid_tag_guid = r.rfid_guid
WHERE COALESCE(r.rfid_tag_type, '') != 'Virtual'
  -- RFID has valid hex that would otherwise be migrated
  AND r.hex IS NOT NULL 
  AND BTRIM(r.hex) != ''
  AND BTRIM(r.hex) ~* '^[0-9a-f]+$'
  AND NOT (BTRIM(r.hex) ~* '^0+$')
  AND LENGTH(BTRIM(r.hex)) IN (8, 14, 16, 20)
  -- Account has no email from EITHER path (NULL, empty, or just whitespace)
  AND (COALESCE(a.email, crt.email) IS NULL OR BTRIM(COALESCE(a.email, crt.email)) = '')
  -- Not already flagged as multi-account (those are a separate error)
  AND r.rfid_guid NOT IN (
      SELECT cu2.rfid_guid
      FROM "Source"."ChargerUsers" cu2
      WHERE cu2.rfid_guid IS NOT NULL
      GROUP BY cu2.rfid_guid
      HAVING COUNT(DISTINCT cu2.account_owner_guid) > 1
  );