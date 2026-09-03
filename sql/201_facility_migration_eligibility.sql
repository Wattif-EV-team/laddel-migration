-- ============================================================================
-- Table: target.facility_migration_eligibility
-- Shared business-logic table (2xx). Materialized (a real table, not a
-- view): dropped and fully rebuilt by every `ladmig build`. Reads only from
-- the read-only `laddel` source database.
--
-- Grain: one row per `laddel.facility`. Centralises ALL of the per-facility
-- business logic shared by target/report views in a single place, so nothing
-- downstream re-derives it independently:
--   - the `project_code` scheme (`W047L` + zero-padded facility_id) — merged
--     in from the former `202_facility_external_id.sql`, which this file
--     replaces. This is the canonical per-facility project code, reused as
--     Ampeco Location `externalId`, SiteTracker `Site_ID__c`, the Partner
--     Contract `title` prefix, and the migration-status report's
--     `project_code`. The source `facility.migration_project_code` column is
--     deliberately NOT used.
--   - migration eligibility (`should_not_migrate` and its component flags).
--
-- Currently consumed by:
--   - 304_target_location.sql              (project_code + eligibility gate)
--   - 314_target_sitetracker_accounts.sql   (via EXISTS: account must have at
--                                             least one eligible facility)
--   - 315_target_sitetracker_sites.sql      (eligibility gate)
--   - 316_target_sitetracker_site_relations.sql (eligibility gate)
--   - 401_report_facility_migration_status.sql  (project_code + charger/session
--                                                 stats)
--   - 402_report_data_quality_issues.sql        (eligibility scope)
--
-- Why a table instead of a view: the `should_not_migrate` aggregation joins
-- `laddel.charger` to `laddel.archived_session` (~1.8M rows) and was measured
-- (EXPLAIN ANALYZE) at ~4.7s per evaluation, because `archived_session`'s
-- `charger_id` index doesn't cover `start_time` — every match needs a
-- random-I/O row read. As a plain view this cost was paid independently by
-- all 6 downstream consumers above (~28s of redundant work per
-- `ladmig build`/`verify`). Materializing it here means the aggregation runs
-- once per build and every consumer instead does a cheap indexed lookup
-- (`facility_id` PRIMARY KEY) against a ~5,000-row table.
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
-- This object used to be a VIEW. `DROP TABLE IF EXISTS` does NOT drop a view,
-- so the DROP VIEW below is required for the table-ification to apply on any
-- database that still has the old view (otherwise CREATE TABLE fails with
-- "Table 'facility_migration_eligibility' already exists"). Harmless once the
-- table exists.
DROP VIEW IF EXISTS `target`.`facility_migration_eligibility`;
DROP TABLE IF EXISTS `target`.`facility_migration_eligibility`;

-- Explicit column list + PRIMARY KEY (rather than `CREATE TABLE ... AS
-- SELECT`) for two reasons: (1) this server has
-- `sql_generate_invisible_primary_key = ON` and the migration user lacks the
-- SESSION_VARIABLES_ADMIN/SUPER privilege needed to turn it off per-session
-- (1227), and GIPK only kicks in when a table has no explicit primary key —
-- defining one here avoids it outright; (2) `CREATE TABLE ... AS SELECT`
-- mixes DDL and DML in one statement, which some managed-MySQL replication
-- setups (GTID + binlog_format=STATEMENT) reject for the same privilege
-- reason. A plain `CREATE TABLE` followed by `INSERT ... SELECT` avoids both.
CREATE TABLE `target`.`facility_migration_eligibility` (
    `facility_id`           INT          NOT NULL,
    `organization_id`       INT          NOT NULL,
    `project_code`          VARCHAR(20)  NOT NULL,
    `total_chargers`        BIGINT       NOT NULL,
    `active_chargers`       BIGINT       NOT NULL,
    `total_sessions`        BIGINT       NOT NULL,
    `last_session_at`       DATETIME(3)  NULL,
    `no_chargers`           TINYINT(1)   NOT NULL,
    `all_chargers_inactive` TINYINT(1)   NOT NULL,
    `no_sessions_ever`      TINYINT(1)   NOT NULL,
    `no_recent_sessions`    TINYINT(1)   NOT NULL,
    `should_not_migrate`    TINYINT(1)   NOT NULL,
    PRIMARY KEY (`facility_id`),
    KEY `idx_organization_id` (`organization_id`),
    KEY `idx_should_not_migrate` (`should_not_migrate`)
);

INSERT INTO `target`.`facility_migration_eligibility`
    (facility_id, organization_id, project_code, total_chargers, active_chargers,
     total_sessions, last_session_at, no_chargers, all_chargers_inactive,
     no_sessions_ever, no_recent_sessions, should_not_migrate)
SELECT
    f.facility_id,
    f.organization_id,
    CONCAT('W047L', LPAD(f.facility_id, 4, '0'))            AS project_code,
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
