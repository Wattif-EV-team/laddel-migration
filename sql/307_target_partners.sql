-- ============================================================================
-- View: target.partners
-- Depends on: target.partner_mapping (001), read-only `laddel` source.
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
--
-- Grain: one partner per `laddel.customer`. A customer is in scope if it is
-- linked — via facility_contact -> facility -> organization — to ANY
-- organization flagged migration_status = 'READY'. The organization is joined
-- via a GROUP BY customer_id derived table, so there is exactly one row per
-- customer (customer -> organization is many-to-one in the source; no fan-out).
-- migration_project_code is deliberately NOT used (it is being removed from the
-- source facility table).
-- Maps `laddel` onto the Ampeco "create partner" payload
-- (POST /public-api/resources/partners/v2.0). See docs/fieldmapping/partner.md.
--
-- Layout: SOURCE -> TARGET ID -> PAYLOAD (Ampeco field names, underscores for nesting).
--
-- Robust trim: source free-text carries stray Unicode separators/control chars
-- (e.g. U+2028 LINE SEPARATOR) that plain TRIM() does not remove. We strip any
-- leading/trailing run of separator (\p{Z}) or control/format (\p{C}) chars with
-- REGEXP_REPLACE while preserving internal spaces.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`partners`;

CREATE OR REPLACE VIEW `target`.`partners` AS
SELECT
    -- -- SOURCE ----------------------------------------------------------------
    CONCAT('Laddel|Customer|', c.customer_id)                       AS mapping_key,
    CONCAT(
        REGEXP_REPLACE(c.name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''),
        ' (cust=', c.customer_id, ')'
    )                                                               AS source_label,

    -- -- TARGET ID(S) -----------------------------------------------------------
    pm.target_partner_id                                            AS target_partner_id,

    -- -- PAYLOAD (Ampeco field names, 1:1, in API order) ----------------------
    -- Top-level / identity
    REGEXP_REPLACE(c.name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '') AS `name`,
    -- businessName: "Organization Name [Customer Name]", collapsed to a single
    -- name when the organization and customer names are identical. Both operands
    -- are collated to utf8mb4_0900_ai_ci to avoid an "illegal mix of collations"
    -- (organization and customer tables use different default collations).
    CASE
        WHEN org.organization_name
           = REGEXP_REPLACE(c.name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')
             COLLATE utf8mb4_0900_ai_ci
        THEN org.organization_name
        ELSE CONCAT(
            org.organization_name,
            ' [', REGEXP_REPLACE(c.name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')
                  COLLATE utf8mb4_0900_ai_ci, ']'
        )
    END                                                             AS `businessName`,
    NULL                                                            AS `externalId`,
    COALESCE(REPLACE(TRIM(c.organization_number), ' ', ''), '')     AS `regNo`,
    CASE
        WHEN c.vat_registered = 1
            THEN CONCAT(REPLACE(TRIM(c.organization_number), ' ', ''), 'MVA')
        ELSE ''
    END                                                             AS `vatNo`,

    -- Address
    REGEXP_REPLACE(c.address, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')     AS `address`,
    REGEXP_REPLACE(c.postal_code, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '') AS `postcode`,
    REGEXP_REPLACE(c.city, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')        AS `city`,
    'NO'                                                            AS `country`,

    -- Contact details
    REGEXP_REPLACE(c.name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')  AS `contactDetails_administrative_contactPerson`,
    REGEXP_REPLACE(c.email, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '') AS `contactDetails_administrative_email`,
    REPLACE(REGEXP_REPLACE(c.phone, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), ' ', '') AS `contactDetails_administrative_phone`,
    NULL                                                            AS `contactDetails_technical_contactPerson`,
    NULL                                                            AS `contactDetails_technical_email`,
    NULL                                                            AS `contactDetails_technical_phone`,
    COALESCE(NULLIF(REGEXP_REPLACE(c.invoice_reference, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), ''), 'Elbil-lading') AS `contactDetails_billing_contactPerson`,
    COALESCE(
        NULLIF(REGEXP_REPLACE(c.invoice_email, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''), ''),
        REGEXP_REPLACE(c.email, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')
    )                                                               AS `contactDetails_billing_email`,
    NULL                                                            AS `contactDetails_billing_phone`,

    -- Fees & numbering (receiptsStartingNumber deliberately never emitted).
    -- Prefixes are derived from customer_id (e.g. `L0123-`): non-blank and
    -- globally unique, satisfying invoiceNumberPrefix's minLength/uniqueness.
    -- TODO: replace the placeholder `L####-` scheme with the final prefix.
    0                                                               AS `monthlyPlatformFee`,
    CONCAT('L', LPAD(c.customer_id, 4, '0'), '-')                   AS `receiptsPrefix`,
    CONCAT('L', LPAD(c.customer_id, 4, '0'), '-')                   AS `invoiceNumberPrefix`,

    -- Options
    'all'                                                           AS `options_userVisibility`,
    0                                                               AS `options_allowViewingAllSessionsOfInvitedUsers`,
    0                                                               AS `options_createUsers`,
    0                                                               AS `options_addUserBalance`,
    1                                                               AS `options_supplierOnReceipts`,
    1                                                               AS `options_supplierOnInvoices`,
    1                                                               AS `options_allowToControlTariffs`,
    1                                                               AS `options_allowToControlTariffGroups`,
    0                                                               AS `options_allowToControlCpConfigurations`,
    'by_location_and_partner_contract'                              AS `options_settlementReportBreakdown`,

    -- Corporate billing (disabled in first batch)
    0                                                               AS `corporateBilling_enabled`,

    -- Notifications
    0                                                               AS `notifications_technical_chargePointFaults`,
    0                                                               AS `notifications_billing_settlementReports`,
    'nb-NO'                                                         AS `notifications_billing_settlementReportLanguage`,

    -- Bank details (bankCode repurposed to carry the KID; digits only)
    REGEXP_REPLACE(TRIM(c.KID), '[^0-9]', '')                       AS `bankDetails_bankCode`,
    REGEXP_REPLACE(TRIM(c.account_number), '[^0-9]', '')            AS `bankDetails_bankAccountNumber`

FROM `laddel`.`customer` c
-- One row per customer: the derived table groups by customer_id and gates on a
-- READY organization (customer -> organization is many-to-one in the source).
JOIN (
    SELECT
        fc.customer_id                                                     AS customer_id,
        -- Force the customer-table collation so businessName's `=` / CONCAT
        -- against c.name below does not raise an "illegal mix of collations".
        MIN(REGEXP_REPLACE(o.organization_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')
            COLLATE utf8mb4_0900_ai_ci)                                    AS organization_name
    FROM `laddel`.`facility_contact` fc
    JOIN `laddel`.`facility`     f ON f.facility_id     = fc.facility_id
    JOIN `laddel`.`organization` o ON o.organization_id = f.organization_id
    WHERE o.migration_status = 'READY'
    GROUP BY fc.customer_id
) org
    ON org.customer_id = c.customer_id
LEFT JOIN `target`.`partner_mapping` pm
    ON pm.mapping_key = CONCAT('Laddel|Customer|', c.customer_id);
