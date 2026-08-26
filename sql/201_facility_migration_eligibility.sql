-- ============================================================================
-- View: target.facility_migration_eligibility
-- Shared business-logic view (2xx). Drop-and-recreate. Reads only from the
-- read-only `laddel` source database.
--
-- Grain: one row per `laddel.facility`. Centralises the "should this facility
-- be migrated?" rule so every downstream target view applies the identical
-- exclusion logic instead of re-deriving it. Currently consumed by:
--   - 304_target_location.sql              (facility grain, direct filter)
--   - 314_target_sitetracker_accounts.sql   (via EXISTS: account must have at
--                                             least one eligible facility)
--   - 315_target_sitetracker_sites.sql      (facility grain, direct filter)
--   - 316_target_sitetracker_site_relations.sql (facility grain, direct filter)
--
-- A facility is flagged `should_not_migrate = 1` when ANY of these hold:
--   1. no_chargers            -- zero `charger` rows for the facility
--   2. all_chargers_inactive  -- >=1 charger but none with `active = 1`
--                                (vacuously true when no_chargers is true too)
--   3. no_sessions_ever       -- zero `archived_session` rows across all of
--                                the facility's chargers
--   4. no_recent_sessions     -- most recent session is more than 6 months
--                                old, or there is none at all
--
-- `start_time` (NOT NULL) is used for recency instead of `end_time` /
-- `finished_time` (both nullable, e.g. for sessions still open) — see
-- docs/repo notes on `archived_session`.
-- ============================================================================
DROP VIEW IF EXISTS `target`.`facility_migration_eligibility`;

CREATE OR REPLACE VIEW `target`.`facility_migration_eligibility` AS
SELECT
    f.facility_id,
    f.organization_id,
    COALESCE(chg.total_chargers, 0)                        AS total_chargers,
    COALESCE(chg.active_chargers, 0)                        AS active_chargers,
    COALESCE(ses.total_sessions, 0)                         AS total_sessions,
    ses.last_session_at                                     AS last_session_at,
    (COALESCE(chg.total_chargers, 0) = 0)                   AS no_chargers,
    (COALESCE(chg.active_chargers, 0) = 0)                  AS all_chargers_inactive,
    (COALESCE(ses.total_sessions, 0) = 0)                   AS no_sessions_ever,
    (
        ses.last_session_at IS NULL
        OR ses.last_session_at < NOW() - INTERVAL 6 MONTH
    )                                                        AS no_recent_sessions,
    (
        COALESCE(chg.total_chargers, 0) = 0
        OR COALESCE(chg.active_chargers, 0) = 0
        OR COALESCE(ses.total_sessions, 0) = 0
        OR ses.last_session_at IS NULL
        OR ses.last_session_at < NOW() - INTERVAL 6 MONTH
    )                                                        AS should_not_migrate

FROM `laddel`.`facility` f
LEFT JOIN (
    SELECT facility_id, COUNT(*) AS total_chargers, SUM(active) AS active_chargers
    FROM `laddel`.`charger`
    GROUP BY facility_id
) chg ON chg.facility_id = f.facility_id
LEFT JOIN (
    SELECT c.facility_id, COUNT(*) AS total_sessions, MAX(s.start_time) AS last_session_at
    FROM `laddel`.`archived_session` s
    JOIN `laddel`.`charger` c ON c.charger_id = s.charger_id
    GROUP BY c.facility_id
) ses ON ses.facility_id = f.facility_id;
