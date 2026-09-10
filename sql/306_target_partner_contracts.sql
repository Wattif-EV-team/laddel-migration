-- ============================================================================
-- View: target.partner_contracts
-- Depends on: target.partner_contract_mapping (006), target.partner_mapping
-- (001), target.facility_migration_eligibility (201), read-only `laddel` source.
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
--
-- Grain: one partner contract per `laddel.facility`. Maps `laddel` onto the
-- Ampeco "create partner contract" payload
-- (POST /public-api/resources/partner-contracts/v1.0, schema
-- PartnerContract_write). See docs/fieldmapping/partner_contract.md.
--
-- Batch gate: facilities whose organization is migration_status = 'READY', AND
-- the facility is migration-eligible per target.facility_migration_eligibility
-- (201) — the same gate as target.location (304).
--
-- Partner dependency is deliberately NOT a filter here. `partnerId` is
-- API-required, but gating it out in SQL would make the view unpreviewable
-- before the partners step has run. The joins to facility_contact / customer /
-- partner_mapping are therefore all LEFT JOINs: every in-scope facility gets a
-- row, with a NULL `partnerId` when its partner does not exist yet. The
-- partner_contracts STEP holds those rows back (see its `skip_reason`).
--
-- The `title` prefix is the shared project code from
-- target.facility_migration_eligibility (201) — the W047L + zero-padded
-- facility_id scheme, also used as the Location externalId and the SiteTracker
-- Site_ID__c. It is never re-derived here.
--
-- Contract model is driven by price_information.priceModel:
--   SUBSCRIPTION -> paymentFacilitation, partnerShare 100, handlingFee 0
--   MARKUP       -> paymentFacilitation, partnerShare 100, handlingFee formula
--   COMMISSION   -> revenueSharing,      partnerShare surChargeKeepModifier*100,
--                                        handlingFee NULL (dropped by the step)
--
-- Layout: SOURCE -> TARGET ID -> PAYLOAD (Ampeco field names, underscores for
-- nesting; the step folds them back into nested objects).
--
-- Robust trim: source free-text carries stray Unicode separators/control chars
-- (e.g. U+2028 LINE SEPARATOR) that plain TRIM() does not remove. We strip any
-- leading/trailing run of separator (\p{Z}) or control/format (\p{C}) chars with
-- REGEXP_REPLACE while preserving internal spaces.
--
-- Number formatting in `title`: the source columns are DECIMAL, so a naive CAST
-- to CHAR yields '89.00' / '10.000'. TRIM(TRAILING '0' ...) alone is WRONG —
-- on '10.000' it strips every trailing zero and produces '1'. We therefore test
-- for a whole number first and only trim the fractional case.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`partner_contracts`;

CREATE OR REPLACE VIEW `target`.`partner_contracts` AS
SELECT
    -- -- SOURCE ----------------------------------------------------------------
    CONCAT('Laddel|Facility|', f.facility_id)                          AS mapping_key,
    CONCAT(
        REGEXP_REPLACE(f.facility_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''),
        ' (fac=', f.facility_id, ')'
    )                                                                  AS source_label,

    -- -- TARGET ID(S) -----------------------------------------------------------
    pcm.target_partner_contract_id                                     AS target_partner_contract_id,

    -- -- PAYLOAD (Ampeco field names, 1:1, in API order) ----------------------
    -- Identity: '{project_code} - {facility_name} ({price model summary})'
    CONCAT(
        fme.project_code, ' - ',
        REGEXP_REPLACE(f.facility_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''),
        ' (',
        CASE pi.priceModel
            WHEN 'SUBSCRIPTION' THEN CONCAT(
                'Subscription ',
                IF(pi.subscription_monthly_fee_incl_vat = FLOOR(pi.subscription_monthly_fee_incl_vat),
                   CAST(CAST(pi.subscription_monthly_fee_incl_vat AS SIGNED) AS CHAR),
                   TRIM(TRAILING '0' FROM CAST(pi.subscription_monthly_fee_incl_vat AS CHAR))),
                ' NOK/user'
            )
            WHEN 'MARKUP' THEN CONCAT(
                'Markup ',
                IF(pi.markup * 100 = FLOOR(pi.markup * 100),
                   CAST(CAST(pi.markup * 100 AS SIGNED) AS CHAR),
                   TRIM(TRAILING '0' FROM CAST(pi.markup * 100 AS CHAR))),
                '%, ',
                IF(COALESCE(f.monthly_fee_per_active_charger_excl_vat, 0)
                     = FLOOR(COALESCE(f.monthly_fee_per_active_charger_excl_vat, 0)),
                   CAST(CAST(COALESCE(f.monthly_fee_per_active_charger_excl_vat, 0) AS SIGNED) AS CHAR),
                   TRIM(TRAILING '0' FROM CAST(COALESCE(f.monthly_fee_per_active_charger_excl_vat, 0) AS CHAR))),
                ' NOK/evse'
            )
            ELSE CONCAT(
                -- COMMISSION: the percentage shown is the OPERATOR's cut, i.e.
                -- the complement of the partner's surChargeKeepModifier.
                'Commission ',
                IF((1 - pi.surChargeKeepModifier) * 100 = FLOOR((1 - pi.surChargeKeepModifier) * 100),
                   CAST(CAST((1 - pi.surChargeKeepModifier) * 100 AS SIGNED) AS CHAR),
                   TRIM(TRAILING '0' FROM CAST((1 - pi.surChargeKeepModifier) * 100 AS CHAR))),
                '%, ',
                IF(COALESCE(f.monthly_fee_per_active_charger_excl_vat, 0)
                     = FLOOR(COALESCE(f.monthly_fee_per_active_charger_excl_vat, 0)),
                   CAST(CAST(COALESCE(f.monthly_fee_per_active_charger_excl_vat, 0) AS SIGNED) AS CHAR),
                   TRIM(TRAILING '0' FROM CAST(COALESCE(f.monthly_fee_per_active_charger_excl_vat, 0) AS CHAR))),
                ' NOK/evse'
            )
        END,
        ')'
    )                                                                  AS `title`,
    pm.target_partner_id                                               AS `partnerId`,
    CASE
        WHEN pi.priceModel = 'COMMISSION' THEN 'revenueSharing'
        ELSE 'paymentFacilitation'
    END                                                                AS `contractType`,
    -- Ampeco applies the contract from the 1st of the selected month, so the
    -- day-of-month is not significant; we simply stamp today's date.
    CONCAT(CURDATE(), 'T00:00:00Z')                                    AS `startDate`,
    NULL                                                               AS `endDate`,
    1                                                                  AS `autoRenewal`,
    -- Deliberately not populated: project_code already appears in `title`, and
    -- Location/Site carry the W047L#### identifier.
    NULL                                                               AS `externalId`,

    -- Access and permissions (full access; no source data)
    1                                                                  AS `accessAndPermissions_sessionsRemoteControl`,
    1                                                                  AS `accessAndPermissions_startReservation`,
    1                                                                  AS `accessAndPermissions_stopReservation`,
    1                                                                  AS `accessAndPermissions_resetChargePoint`,
    1                                                                  AS `accessAndPermissions_firmwareUpdate`,
    1                                                                  AS `accessAndPermissions_createFromTemplate`,
    1                                                                  AS `accessAndPermissions_changeSystemStatus`,

    -- Revenue sharing. The API has no separate payment-facilitation object, so
    -- this one object serves both contract types: under paymentFacilitation the
    -- partner is the supplier (100%) and the operator's cut rides on
    -- handlingFee; under revenueSharing the split itself carries it.
    CASE
        WHEN pi.priceModel = 'COMMISSION' THEN pi.surChargeKeepModifier * 100
        ELSE 100
    END                                                                AS `revenueSharing_partnerSharePercentageAcEvse`,
    CASE
        WHEN pi.priceModel = 'COMMISSION' THEN pi.surChargeKeepModifier * 100
        ELSE 100
    END                                                                AS `revenueSharing_partnerSharePercentageDcEvse`,
    0                                                                  AS `revenueSharing_excludeConnectionFee`,
    0                                                                  AS `revenueSharing_deductElectricityCost`,
    0                                                                  AS `revenueSharing_reimburseForElectricityCost`,
    -- handlingFee is a percentage (0-100) of the total the end user pays for
    -- the session. MARKUP: M / (1 + Vhost + M * (1 + Vfee)) * 100, where Vfee is
    -- always 0.25 and Vhost is 0.25 when the customer is VAT registered, else 0.
    CASE pi.priceModel
        WHEN 'SUBSCRIPTION' THEN 0
        WHEN 'MARKUP' THEN ROUND(
            pi.markup
            / (1
               + IF(COALESCE(c.vat_registered, 0) = 1, 0.25, 0)
               + pi.markup * 1.25)
            * 100
        , 4)
        ELSE NULL
    END                                                                AS `revenueSharing_handlingFee`,

    -- Monthly platform fees. The source fee is per ACTIVE CHARGER, so it is
    -- applied to the EVSE fields; there is no per-charge-point fee and no AC/DC
    -- breakdown in `laddel`.
    0                                                                  AS `monthlyPlatformFees_perChargePoint`,
    COALESCE(f.monthly_fee_per_active_charger_excl_vat, 0)              AS `monthlyPlatformFees_perAcEvse`,
    COALESCE(f.monthly_fee_per_active_charger_excl_vat, 0)              AS `monthlyPlatformFees_perDcEvse`

FROM `laddel`.`facility` f
JOIN `laddel`.`facility_information` fi
    ON fi.facility_id = f.facility_id
JOIN `laddel`.`price_information` pi
    ON pi.price_id = fi.price_id
JOIN `laddel`.`organization` o
    ON o.organization_id = f.organization_id
JOIN `target`.`facility_migration_eligibility` fme
    ON fme.facility_id = f.facility_id
-- LEFT so a facility without a contact still previews; vat_registered is
-- COALESCEd to 0 in the handling-fee formula above. Within the current batch
-- every facility has exactly one facility_contact, so there is no fan-out.
LEFT JOIN `laddel`.`facility_contact` fc
    ON fc.facility_id = f.facility_id
LEFT JOIN `laddel`.`customer` c
    ON c.customer_id = fc.customer_id
-- LEFT so rows survive before the partners step has run; `partnerId` is then
-- NULL and the step skips the row rather than posting an invalid payload.
LEFT JOIN `target`.`partner_mapping` pm
    ON pm.mapping_key = CONCAT('Laddel|Customer|', c.customer_id)
LEFT JOIN `target`.`partner_contract_mapping` pcm
    ON pcm.mapping_key = CONCAT('Laddel|Facility|', f.facility_id)
WHERE o.migration_status = 'READY'
  AND fme.should_not_migrate = 0;
