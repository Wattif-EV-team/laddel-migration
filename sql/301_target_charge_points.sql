-- ============================================================================
-- View: target.charge_points
-- Depends on: target.charge_point_mapping (007), target.location_mapping (003),
--             target.partner_mapping (001), target.partner_contract_mapping
--             (006), target.facility_migration_eligibility (201), read-only
--             `laddel` source.
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
--
-- Grain: one Ampeco charge point per `laddel.charger` row. Maps `laddel` onto
-- the Ampeco "create charge point" payload
-- (POST /public-api/resources/charge-points/v2.0, schema chargePointV2Write).
-- See docs/fieldmapping/charge_point.md.
--
-- Batch gate: chargers whose organization is migration_status = 'READY', AND
-- whose facility is migration-eligible per target.facility_migration_eligibility
-- (201) — the same gate as target.location (304) and target.partner_contracts
-- (306). Inactive chargers (active = 0) are deliberately NOT excluded: the
-- charge point is always created `enabled` and real availability is expressed on
-- the EVSE.
--
-- MASTER / SATELLITE
-- ------------------
-- A `laddel.charger` is conceptually an EVSE, and `UNIQUE (ocpp_id, socket_id)`
-- means several chargers can share one physical box / OCPP connection. Only one
-- of them may own that connection, so within each `ocpp_id`:
--   • the charger with the LOWEST socket_id is the MASTER — it carries
--     communicationMode = 'direct_ocpp' plus the network/security blocks;
--   • every other charger is a SATELLITE — communicationMode =
--     'via_ocpp_connected_charge_point' plus ocppConnectedChargePointId pointing
--     at the master's Ampeco id.
-- ⚠️ socket_id is NOT a small ordinal: it is 1 for most chargers but a large
-- connector serial for EVBox boxes (e.g. 2305204). "Lowest socket_id" is
-- deterministic and stable, but it is never "socket_id = 1".
--
-- All master-only payload columns are NULL on satellites so the step's
-- prune_none drops them: the `via_ocpp_connected_charge_point` contract forbids
-- network, security, capabilities, integratedAt and the behaviour flags.
--
-- The step runs TWO PASSES over this view, filtered on `communicationMode`:
-- masters first, then satellites — by which time the masters' ids have been
-- written to target.charge_point_mapping and the `mst` self-join below resolves
-- `ocppConnectedChargePointId`. Row order in this view is therefore irrelevant.
--
-- Dependencies are deliberately NOT filtered here. locationId, partner.id and
-- partner.contractId are all resolved through LEFT JOINs so the whole in-scope
-- batch stays previewable before the locations / partners / partner_contracts
-- steps have run; the charge_points STEP holds those rows back (skip_reason).
--
-- Layout: SOURCE -> TARGET ID(S) -> PAYLOAD (Ampeco field names, underscores for
-- nesting; the step folds them back into nested objects and json.loads the JSON
-- string columns).
--
-- Deliberately omitted from the payload (see the field mapping doc): externalId,
-- chargingZoneId, pin, modelId, networkType, usesRenewableEnergy,
-- enabledRandomisedDelay, sharingCode, calibrationLawDataAvailability,
-- powerSharing, countryStationId, manufacturedAt, the personal-only user /
-- subscription blocks, and the deprecated managedByOperator /
-- enableAutoFaultRecovery / partner.notice.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`charge_points`;

CREATE OR REPLACE VIEW `target`.`charge_points` AS
WITH scoped AS (
    -- The in-scope charger batch, with the facility-level attributes the type
    -- and tag rules need.
    SELECT
        c.charger_id                                                   AS charger_id,
        c.charger_reference                                            AS charger_reference,
        c.ocpp_id                                                      AS ocpp_id,
        c.socket_id                                                    AS socket_id,
        c.creation_date                                                AS creation_date,
        c.is_whitelist_enabled                                         AS is_whitelist_enabled,
        f.facility_id                                                  AS facility_id,
        fc.customer_id                                                 AS customer_id,
        fi.is_hidden                                                   AS is_hidden,
        pi.priceModel                                                  AS price_model,
        cu.vat_registered                                              AS vat_registered
    FROM `laddel`.`charger` c
    JOIN `laddel`.`facility` f
        ON f.facility_id = c.facility_id
    JOIN `laddel`.`organization` o
        ON o.organization_id = f.organization_id
    JOIN `target`.`facility_migration_eligibility` fme
        ON fme.facility_id = f.facility_id
    JOIN `laddel`.`facility_information` fi
        ON fi.facility_id = f.facility_id
    -- LEFT so a facility without a price row still previews; a NULL price_model
    -- makes the `<> 'SUBSCRIPTION'` test NULL and `type` falls through to the
    -- safe default 'private'.
    LEFT JOIN `laddel`.`price_information` pi
        ON pi.price_id = fi.price_id
    -- LEFT by convention (UNIQUE(facility_id), so no fan-out). Every in-scope
    -- charger does have a customer today.
    LEFT JOIN `laddel`.`facility_contact` fc
        ON fc.facility_id = f.facility_id
    LEFT JOIN `laddel`.`customer` cu
        ON cu.customer_id = fc.customer_id
    WHERE o.migration_status = 'READY'
      AND fme.should_not_migrate = 0
),
boxed AS (
    -- Elect the master of each physical box and count the box's EVSEs.
    SELECT
        s.charger_id                                                   AS charger_id,
        s.charger_reference                                            AS charger_reference,
        s.ocpp_id                                                      AS ocpp_id,
        s.creation_date                                                AS creation_date,
        s.is_whitelist_enabled                                         AS is_whitelist_enabled,
        s.facility_id                                                  AS facility_id,
        s.customer_id                                                  AS customer_id,
        s.is_hidden                                                    AS is_hidden,
        s.price_model                                                  AS price_model,
        s.vat_registered                                               AS vat_registered,
        (s.socket_id = MIN(s.socket_id) OVER (PARTITION BY s.ocpp_id)) AS is_master,
        COUNT(*) OVER (PARTITION BY s.ocpp_id)                         AS box_size,
        FIRST_VALUE(s.charger_id) OVER (
            PARTITION BY s.ocpp_id ORDER BY s.socket_id
        )                                                              AS master_charger_id
    FROM scoped s
),
typed AS (
    -- `type` drives `partner.accessType`, so it has to be resolved before the
    -- final SELECT can reference it.
    SELECT
        b.charger_id                                                   AS charger_id,
        b.charger_reference                                            AS charger_reference,
        b.ocpp_id                                                      AS ocpp_id,
        b.creation_date                                                AS creation_date,
        b.is_whitelist_enabled                                         AS is_whitelist_enabled,
        b.facility_id                                                  AS facility_id,
        b.customer_id                                                  AS customer_id,
        b.price_model                                                  AS price_model,
        b.is_master                                                    AS is_master,
        b.box_size                                                     AS box_size,
        b.master_charger_id                                            AS master_charger_id,
        CASE
            -- Personal chargers: 0 in the current batch (all LDB% chargers sit
            -- outside READY), so this branch is specified but dead code.
            WHEN b.charger_reference LIKE 'LDB%'  THEN 'personal'
            WHEN b.is_hidden = 0
             AND b.price_model <> 'SUBSCRIPTION'
             AND b.vat_registered = 1             THEN 'public'
            ELSE                                       'private'
        END                                                            AS cp_type
    FROM boxed b
)
SELECT
    -- -- SOURCE ----------------------------------------------------------------
    CONCAT('Laddel|Charger|', t.charger_id)                            AS mapping_key,
    CONCAT(
        t.charger_reference,
        ' (chg=', t.charger_id, ', fac=', t.facility_id, ')'
    )                                                                  AS source_label,
    t.is_master                                                        AS is_master,
    t.box_size                                                         AS box_size,
    CONCAT('Laddel|Charger|', t.master_charger_id)                     AS master_mapping_key,

    -- -- TARGET ID(S) -----------------------------------------------------------
    cpm.target_charge_point_id                                         AS target_charge_point_id,

    -- -- PAYLOAD (Ampeco field names, 1:1, in API order) ----------------------
    -- Identity. `name` is admin-facing only; the MASTER/SLAVE suffix mirrors the
    -- OCPP relationship expressed by communicationMode and is added only when the
    -- box actually carries more than one EVSE. Unique across the whole batch.
    CONCAT(
        'NOR', t.charger_reference,
        CASE
            WHEN t.box_size > 1 AND t.is_master = 1 THEN ' [MASTER]'
            WHEN t.box_size > 1                     THEN ' [SLAVE]'
            ELSE                                         ''
        END
    )                                                                  AS `name`,
    t.cp_type                                                          AS `type`,
    -- Always enabled: availability is expressed on the EVSE, including for the
    -- few `active = 0` chargers.
    'enabled'                                                          AS `status`,

    -- Placement / relationships
    lm.target_location_id                                              AS `locationId`,
    pm.target_partner_id                                               AS `partner_id`,
    pcm.target_partner_contract_id                                     AS `partner_contractId`,
    0                                                                  AS `partner_corporateBillingAsDefault`,
    CASE
        WHEN t.cp_type <> 'private'         THEN NULL
        WHEN t.is_whitelist_enabled = 1     THEN 'private_view_private_use'
        ELSE                                     'private_view_public_use'
    END                                                                AS `partner_accessType`,

    -- OCPP / network. Everything from here down is master-only, NULL on
    -- satellites, except communicationMode and ocppConnectedChargePointId.
    IF(t.is_master = 1, 'direct_ocpp', 'via_ocpp_connected_charge_point')
                                                                       AS `communicationMode`,
    IF(t.is_master = 1, NULL, mst.target_charge_point_id)              AS `ocppConnectedChargePointId`,
    IF(t.is_master = 1, t.ocpp_id, NULL)                               AS `network_id`,
    -- Flat 'ocpp 1.6': laddel does not store the protocol version. The real
    -- per-charger value lives in eMabler and is a future PATCH-in extract.
    IF(t.is_master = 1, 'ocpp 1.6', NULL)                              AS `network_protocol`,
    -- 0 = No Authentication.
    IF(t.is_master = 1, 0, NULL)                                       AS `security_desiredProfile`,

    -- Behaviour & capabilities (master-only). No per-charger source for any of
    -- these; the capability triple is the operator-wide default.
    IF(
        t.is_master = 1,
        '["remote_start_stop_capable","meter_values","stop_transaction_on_ev_disconnect"]',
        NULL
    )                                                                  AS `capabilities`,
    IF(t.is_master = 1, 0, NULL)                                       AS `autoStartWithoutAuthorization`,
    IF(t.is_master = 1, 0, NULL)                                       AS `disableAutoStartEmulation`,
    IF(t.is_master = 1, 1, NULL)                                       AS `monitoringEnabled`,
    IF(t.is_master = 1, 1, NULL)                                       AS `autoRecoveryEnabled`,
    -- ⚠️ Ampeco returns 422 unless operational availability is enabled on the
    -- operator. Valid here because every in-scope charge point is commercial.
    IF(t.is_master = 1, 1, NULL)                                       AS `uptimeTrackingEnabled`,

    -- Tags. Base pair matches target.location (304); the MDU tag marks the
    -- multi-dwelling-unit sites, which are exactly the SUBSCRIPTION price model.
    CASE
        WHEN t.price_model = 'SUBSCRIPTION'
            THEN '["Owner:Customer","Source:Laddel","LocationType:MDU"]'
        ELSE     '["Owner:Customer","Source:Laddel"]'
    END                                                                AS `tags`,
    IF(t.is_master = 1, DATE_FORMAT(t.creation_date, '%Y-%m-%dT%H:%i:%s'), NULL)
                                                                       AS `integratedAt`

FROM typed t
LEFT JOIN `target`.`charge_point_mapping` cpm
    ON cpm.mapping_key = CONCAT('Laddel|Charger|', t.charger_id)
-- The master's own mapping row, so a satellite can point at its Ampeco id. NULL
-- until the masters pass has run — the step skips satellites while it is.
LEFT JOIN `target`.`charge_point_mapping` mst
    ON mst.mapping_key = CONCAT('Laddel|Charger|', t.master_charger_id)
LEFT JOIN `target`.`location_mapping` lm
    ON lm.mapping_key = CONCAT('Laddel|Facility|', t.facility_id)
LEFT JOIN `target`.`partner_contract_mapping` pcm
    ON pcm.mapping_key = CONCAT('Laddel|Facility|', t.facility_id)
LEFT JOIN `target`.`partner_mapping` pm
    ON pm.mapping_key = CONCAT('Laddel|Customer|', t.customer_id);
