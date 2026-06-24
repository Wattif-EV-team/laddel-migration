-- ============================================================================
-- View: target.sitetracker_accounts
-- Depends on: target.sitetracker_account_mapping (002), read-only `laddel` source.
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
--
-- Grain: one Account per `laddel.customer`. A customer is in scope if it is
-- linked — via facility_contact -> facility -> organization — to ANY
-- organization flagged migration_status = 'READY'. The EXISTS gate yields
-- exactly one row per customer (no fan-out), so no DISTINCT/GROUP BY is needed.
--
-- Maps `laddel` onto the SiteTracker (Salesforce) "create Account" payload
-- (POST /services/data/vXX.0/sobjects/Account/). See
-- docs/fieldmapping/sitetracker_account.md.
--
-- Layout: SOURCE -> TARGET ID -> PAYLOAD (Salesforce field names, 1:1, flat).
-- Account fields are flat — the `__c` / `Business_Registration_Number__c`
-- underscores are part of the API name and are NOT nesting; the step builds a
-- flat payload (no underscore re-nesting).
--
-- Robust trim: source free-text carries stray Unicode separators/control chars
-- (e.g. U+2028 LINE SEPARATOR) that plain TRIM() does not remove. We strip any
-- leading/trailing run of separator (\p{Z}) or control/format (\p{C}) chars with
-- REGEXP_REPLACE while preserving internal spaces.
--
-- ⚠️ Verify against a live `describe` of the laddel SiteTracker org before a
-- full run: that `Email__c` exists, and the expected `BillingCountry` format
-- (`Norway` vs `Norway(NOR)`). The field list below comes from the Wattif
-- sandbox and may differ.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`sitetracker_accounts`;

CREATE OR REPLACE VIEW `target`.`sitetracker_accounts` AS
SELECT
    -- -- SOURCE ----------------------------------------------------------------
    CONCAT('Laddel|SiteTrackerAccount|', c.customer_id)             AS mapping_key,
    CONCAT(
        REGEXP_REPLACE(c.name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''),
        ' (cust=', c.customer_id, ')'
    )                                                               AS source_label,

    -- -- TARGET ID(S) -----------------------------------------------------------
    sam.target_sf_account_id                                        AS target_sf_account_id,

    -- -- PAYLOAD (Salesforce Account field names, 1:1) ------------------------
    -- Identity
    REGEXP_REPLACE(c.name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '') AS `Name`,
    REGEXP_REPLACE(TRIM(c.organization_number), '[^0-9]', '')       AS `Business_Registration_Number__c`,
    'Customer'                                                      AS `Type`,

    -- Billing address
    REGEXP_REPLACE(c.address, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')     AS `BillingStreet`,
    REGEXP_REPLACE(c.city, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')        AS `BillingCity`,
    REGEXP_REPLACE(c.postal_code, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '') AS `BillingPostalCode`,
    'Norway'                                                       AS `BillingCountry`,

    -- Contact
    LOWER(NULLIF(REGEXP_REPLACE(c.email, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), '')) AS `Email__c`,
    NULLIF(REGEXP_REPLACE(c.phone, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), '')        AS `Phone`

FROM `laddel`.`customer` c
LEFT JOIN `target`.`sitetracker_account_mapping` sam
    ON sam.mapping_key = CONCAT('Laddel|SiteTrackerAccount|', c.customer_id)
WHERE EXISTS (
    SELECT 1
    FROM `laddel`.`facility_contact` fc
    JOIN `laddel`.`facility`     f ON f.facility_id     = fc.facility_id
    JOIN `laddel`.`organization` o ON o.organization_id = f.organization_id
    WHERE fc.customer_id      = c.customer_id
      AND o.migration_status  = 'READY'
);
