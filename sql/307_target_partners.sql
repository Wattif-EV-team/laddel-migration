-- ============================================================================
-- View: target.partners
-- Depends on: target.partner_mapping (001), read-only `laddel` source.
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
--
-- Grain: one partner per facility (first batch = MDU / SUBSCRIPTION).
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
    CONCAT('Laddel|Facility|', f.facility_id)                       AS mapping_key,
    CONCAT(
        CASE
            WHEN REGEXP_REPLACE(o.organization_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')
               = REGEXP_REPLACE(f.facility_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')
            THEN REGEXP_REPLACE(o.organization_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')
            ELSE CONCAT(
                REGEXP_REPLACE(o.organization_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''),
                '/',
                REGEXP_REPLACE(f.facility_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '')
            )
        END,
        ' (fac=', f.facility_id, ', org=', o.organization_id, ', cust=', c.customer_id, ')'
    )                                                               AS source_label,
    f.migration_project_code                                        AS project_code,

    -- -- TARGET ID(S) -----------------------------------------------------------
    pm.target_partner_id                                            AS target_partner_id,

    -- -- PAYLOAD (Ampeco field names, 1:1, in API order) ----------------------
    -- Top-level / identity
    REGEXP_REPLACE(c.name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', '') AS `name`,
    CONCAT(
        REGEXP_REPLACE(o.organization_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''),
        ' [', f.migration_project_code, ']'
    )                                                               AS `businessName`,
    f.migration_project_code                                        AS `externalId`,
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

    -- Fees & numbering (receiptsStartingNumber deliberately never emitted)
    0                                                               AS `monthlyPlatformFee`,
    f.migration_project_code                                        AS `receiptsPrefix`,

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

FROM `laddel`.`facility` f
JOIN `laddel`.`organization` o
    ON o.organization_id = f.organization_id
JOIN `laddel`.`facility_contact` fc
    ON fc.facility_id = f.facility_id
JOIN `laddel`.`customer` c
    ON c.customer_id = fc.customer_id
LEFT JOIN `laddel`.`facility_information` fi
    ON fi.facility_id = f.facility_id
LEFT JOIN `laddel`.`price_information` pi
    ON pi.price_id = fi.price_id
LEFT JOIN `target`.`partner_mapping` pm
    ON pm.mapping_key = CONCAT('Laddel|Facility|', f.facility_id)
WHERE o.migration_status       = 'READY'
  AND f.migration_project_code IS NOT NULL
  AND pi.priceModel            = 'SUBSCRIPTION';
