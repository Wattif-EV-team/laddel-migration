-- ============================================================================
-- View: Source.ChargerUserAccounts
-- Description: EV Driver accounts extracted from RawChargerUsers denormalized export.
--              Named with ChargerUser prefix to indicate Account role context
--              (same Account entity appears with different roles elsewhere).
-- Grain: One row per account_owner_guid (1,384 expected)
-- Source: Source.RawChargerUsers
-- ============================================================================
SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Source"."ChargerUserAccounts" CASCADE;
CREATE OR REPLACE VIEW "Source"."ChargerUserAccounts" AS
SELECT
    account_owner_guid,
    -- Use NULLIF to convert empty strings to NULL, then MAX picks non-empty value
    -- This ensures partially masked rows don't overwrite actual PII values
    -- btrim removes leading/trailing whitespace from source data
    btrim(MAX(NULLIF(account_owner_name, ''))) AS account_owner_name,
    MAX(account_number) AS account_number,
    MAX(NULLIF(account_classification, '')) AS account_classification,
    MAX(NULLIF(invoice_distribution, '')) AS invoice_distribution,
    MAX(NULLIF(email, '')) AS email,
    MAX(NULLIF(main_phone, '')) AS main_phone,
    MAX(NULLIF(address_1_street_1, '')) AS address_1_street_1,
    MAX(NULLIF(address_1_city, '')) AS address_1_city,
    MAX(NULLIF(address_1_postal_code, '')) AS address_1_postal_code,
    MAX(NULLIF(msisdn, '')) AS msisdn
FROM "Source"."AllRawChargerUsers"
WHERE account_owner_guid IS NOT NULL
GROUP BY account_owner_guid;
