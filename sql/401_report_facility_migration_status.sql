-- ============================================================================
-- View: target.report_facility_migration_status
-- Report / quality view (4xx). Drop-and-recreate. Reads from the read-only
-- `laddel` source database plus the shared `target.facility_migration_eligibility`
-- (201) and `target.facility_external_id` (202) views. Not part of the Ampeco
-- payload target views (3xx) — this view has no `mapping_key`/target-id
-- columns, it exists purely for migration reporting.
--
-- Grain: one row per `laddel.facility`.
--
-- project_code reuses `target.facility_external_id` (202) — the same
-- W047L + zero-padded facility_id scheme as the Location view's externalId
-- (304). facility_creation_date/last_session_at are truncated to DATE (no
-- time-of-day).
--
-- Migration status/date are attributes of the facility's ORGANIZATION (not the
-- facility itself) — `laddel.organization.migration_status` /
-- `laddel.organization.migration_date`.
--
-- Parent organization tree: `organization.parent_organization_id` is a
-- self-referencing FK. `org_ancestors` is a recursive CTE walking from each
-- organization up to its root (`depth < 50` is a safety cap against an
-- accidental cycle, not a real business limit — live max depth is 3, i.e. 4
-- levels total including the org itself). `org_levels` pivots that chain into
-- 4 fixed columns, top-level (root) first / leftmost, the facility's own
-- organization last / rightmost — left-padded with NULLs when the real chain
-- is shorter than 4 levels. Chains deeper than 4 levels collapse extra
-- ancestors into level 1 (documented limitation, not hit by current data).
--
-- organization_ev_fleet_information is 1:1 with organization and often absent.
-- `ev_fleet_resolved` walks the SAME org_ancestors chain outward (self first,
-- then nearest parent, ...) and picks the first ancestor (including self) that
-- has a row, so the EV fleet numbers shown are inherited from the nearest
-- ancestor that defines them. `ev_fleet_inherited_from_organization_name` is
-- NULL when the data is the organization's own (not inherited) or when no
-- ancestor in the chain has any EV fleet info at all.
--
-- Charger counts reuse `target.facility_migration_eligibility` (201) for
-- total/active charger counts and last-session-at instead of re-deriving them.
-- `kwh_last_3_months` / `session_count_last_3_months` are a separate, narrower
-- time-windowed aggregate over `archived_session` (last session ever is a
-- different question than sessions in the last 3 months).
--
-- active_subscription_count counts subscriptions that are NOT cancelled and
-- whose expiration_date is still in the future (status != 'CANCELLED' AND
-- expiration_date > CURDATE()) — broader than status = 'ACTIVE' alone (also
-- covers PAUSED/PAYMENT_FAILED/OUTSTANDING_DEBT that haven't expired yet).
--
-- facility_contact is nullable: ~105/5047 facilities currently have no
-- `facility_contact` row (no linked customer) — LEFT JOIN, not JOIN.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`report_facility_migration_status`;

CREATE OR REPLACE VIEW `target`.`report_facility_migration_status` AS
WITH RECURSIVE org_ancestors AS (
    SELECT
        organization_id        AS start_organization_id,
        organization_id        AS organization_id,
        parent_organization_id AS parent_organization_id,
        organization_name      AS organization_name,
        0                      AS depth
    FROM `laddel`.`organization`

    UNION ALL

    SELECT
        a.start_organization_id,
        o.organization_id,
        o.parent_organization_id,
        o.organization_name,
        a.depth + 1
    FROM org_ancestors a
    JOIN `laddel`.`organization` o ON o.organization_id = a.parent_organization_id
    WHERE a.depth < 50
),
org_depth AS (
    -- Total chain length per organization (self + ancestors), capped at 3
    -- (=> 4 levels including self) to bound the pivot below.
    SELECT
        start_organization_id       AS organization_id,
        LEAST(MAX(depth), 3)        AS max_depth
    FROM org_ancestors
    GROUP BY start_organization_id
),
org_levels AS (
    -- Pivot the chain into 4 columns, root (topmost) leftmost, the
    -- organization itself rightmost, left-padded with NULL when the real
    -- chain is shorter than 4 levels.
    SELECT
        a.start_organization_id AS organization_id,
        MAX(CASE WHEN od.max_depth - LEAST(a.depth, 3) + 1 = 1 THEN a.organization_name END) AS organization_level_1,
        MAX(CASE WHEN od.max_depth - LEAST(a.depth, 3) + 1 = 2 THEN a.organization_name END) AS organization_level_2,
        MAX(CASE WHEN od.max_depth - LEAST(a.depth, 3) + 1 = 3 THEN a.organization_name END) AS organization_level_3,
        MAX(CASE WHEN od.max_depth - LEAST(a.depth, 3) + 1 = 4 THEN a.organization_name END) AS organization_level_4
    FROM org_ancestors a
    JOIN org_depth od ON od.organization_id = a.start_organization_id
    GROUP BY a.start_organization_id
),
ev_fleet_resolved AS (
    -- Nearest ancestor (self first, depth 0, then parent, grandparent, ...)
    -- that actually has an organization_ev_fleet_information row.
    SELECT
        a.start_organization_id                                                      AS organization_id,
        a.organization_id                                                            AS source_organization_id,
        a.organization_name                                                          AS source_organization_name,
        a.depth                                                                      AS depth,
        ROW_NUMBER() OVER (PARTITION BY a.start_organization_id ORDER BY a.depth ASC) AS rn
    FROM org_ancestors a
    JOIN `laddel`.`organization_ev_fleet_information` oef
        ON oef.organization_id = a.organization_id
),
sessions_recent AS (
    SELECT
        c.facility_id                    AS facility_id,
        COUNT(*)                         AS session_count_last_3_months,
        SUM(s.energy)                    AS kwh_last_3_months
    FROM `laddel`.`archived_session` s
    JOIN `laddel`.`charger` c ON c.charger_id = s.charger_id
    WHERE s.start_time >= NOW() - INTERVAL 3 MONTH
    GROUP BY c.facility_id
)

SELECT
    -- Facility
    f.facility_id                                       AS facility_id,
    f.facility_name                                      AS facility_name,
    fei.external_id                                      AS project_code,
    DATE(f.creation_date)                                AS facility_creation_date,

    -- Chargers (reusing 201's total/active; inactive derived)
    COALESCE(fme.total_chargers, 0)                      AS charger_count,
    COALESCE(fme.active_chargers, 0)                     AS active_charger_count,
    COALESCE(fme.total_chargers, 0) - COALESCE(fme.active_chargers, 0) AS inactive_charger_count,

    -- Subscriptions
    COALESCE(sub.active_subscription_count, 0)           AS active_subscription_count,

    -- Organization / migration status
    o.organization_id                                    AS organization_id,
    o.organization_name                                  AS organization_name,
    ol.organization_level_1                              AS organization_level_1,
    ol.organization_level_2                              AS organization_level_2,
    ol.organization_level_3                              AS organization_level_3,
    ol.organization_level_4                               AS organization_level_4,
    o.migration_status                                   AS migration_status,
    o.migration_date                                     AS migration_date,
    o.enable_ev_fleet                                    AS enable_ev_fleet,

    -- Sessions
    DATE(fme.last_session_at)                            AS last_session_at,
    COALESCE(sr.session_count_last_3_months, 0)          AS session_count_last_3_months,
    COALESCE(sr.kwh_last_3_months, 0)                    AS kwh_last_3_months,

    -- Facility contact (customer) — nullable, see header note
    cust.name                                            AS facility_contact_name,
    cust.email                                           AS facility_contact_email,
    cust.invoice_email                                   AS facility_contact_billing_email,
    cust.organization_number                             AS facility_contact_organization_number,

    -- Pricing
    pi.priceModel                                        AS price_model,

    -- Organization EV fleet information — inherited from the nearest ancestor
    -- (including self) that has a row; see header note.
    oef.admin_fee_charging_percentage                    AS ev_fleet_admin_fee_charging_percentage,
    oef.admin_fee_homecharging_fixed                     AS ev_fleet_admin_fee_homecharging_fixed,
    oef.use_collective_invoice                           AS ev_fleet_use_collective_invoice,
    oef.default_capacity_level_excl_vat                  AS ev_fleet_default_capacity_level_excl_vat,
    CASE WHEN efr.depth > 0 THEN efr.source_organization_name END AS ev_fleet_inherited_from_organization_name

FROM `laddel`.`facility` f
JOIN `laddel`.`organization` o
    ON o.organization_id = f.organization_id
JOIN `target`.`facility_external_id` fei
    ON fei.facility_id = f.facility_id
LEFT JOIN `target`.`facility_migration_eligibility` fme
    ON fme.facility_id = f.facility_id
LEFT JOIN org_levels ol
    ON ol.organization_id = o.organization_id
LEFT JOIN sessions_recent sr
    ON sr.facility_id = f.facility_id
LEFT JOIN (
    SELECT facility_id, COUNT(*) AS active_subscription_count
    FROM `laddel`.`facility_subscription`
    WHERE status != 'CANCELLED'
      AND expiration_date > CURDATE()
    GROUP BY facility_id
) sub
    ON sub.facility_id = f.facility_id
LEFT JOIN `laddel`.`facility_contact` fc
    ON fc.facility_id = f.facility_id
LEFT JOIN `laddel`.`customer` cust
    ON cust.customer_id = fc.customer_id
LEFT JOIN `laddel`.`facility_information` fi
    ON fi.facility_id = f.facility_id
LEFT JOIN `laddel`.`price_information` pi
    ON pi.price_id = fi.price_id
LEFT JOIN ev_fleet_resolved efr
    ON efr.organization_id = o.organization_id AND efr.rn = 1
LEFT JOIN `laddel`.`organization_ev_fleet_information` oef
    ON oef.organization_id = efr.source_organization_id;
