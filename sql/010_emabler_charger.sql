-- ============================================================================
-- API EXTRACT TABLE: target.emabler_charger
-- ============================================================================
-- ⚠️  NEVER DROP THIS TABLE from a `ladmig build` — it is created once and then
--     owned by the extract command, exactly like the 0xx mapping tables:
--       • Create with CREATE TABLE IF NOT EXISTS (never DROP + recreate).
--       • Add columns with a guarded ALTER TABLE — MySQL 8 has NO
--         `ADD COLUMN IF NOT EXISTS`.
--
-- Unlike the mapping tables this holds no migration state: it is a *snapshot*
-- of an external system, refreshed wholesale by
--
--     uv run ladmig emabler extract chargers
--
-- which replaces every row inside a single transaction (DELETE + INSERT, not
-- TRUNCATE — TRUNCATE is DDL in MySQL and would implicitly commit, so a failed
-- reload could not roll back to the previous snapshot).
--
-- SOURCE
--   eMabler Entity Management API, GET {EMABLER_V2_API_URL}/v2/chargers
--   (operationId `getChargers`, schema `chargerDto`). See docs/emabler-entity.json.
--
-- WHY THIS EXISTS
--   `laddel` has no OCPP protocol version anywhere in its 114 tables, so
--   `target.charge_points` currently hardcodes network.protocol = 'ocpp 1.6'.
--   The real per-charger value lives only in eMabler, as `chargerDto.ocppVersion`
--   — mirrored here as `ocpp_version`.
--
--   ⚠️  docs/emabler-entity.json declares ocppVersion as an INT enum (0-3). The
--   live API actually returns the enum *names* as strings ('Unknown',
--   'Version16', ...), verified 2026-09-10. The column is therefore VARCHAR and
--   stores whatever the API sends, verbatim; decoding to Ampeco's protocol
--   string happens downstream, not here.
--
-- JOINING BACK TO laddel
--   `charger_id` (eMabler's OCPP identity string)  ->  `laddel`.`charger`.`ocpp_id`
--   `site_id`    (eMabler's numeric site id)       ->  `laddel`.`facility`.`emabler_id`
--
-- SHAPE
--   Scalar fields of `chargerDto` are mirrored as typed columns. The nested
--   arrays/objects (`sockets`, `location`, `chargerConfigurations`,
--   `ongoingTransactions`, `customProperties`, `capabilities`, ...) are NOT
--   flattened — the untouched payload is kept in `raw_json`, so anything not
--   yet promoted to a column can still be reached with MySQL's JSON functions
--   (e.g. `raw_json ->> '$.location.city'`) without re-running the extract.
-- ============================================================================

CREATE TABLE IF NOT EXISTS `target`.`emabler_charger` (
    -- chargerDto.id — eMabler's own surrogate key, guaranteed unique.
    -- Used as the PK rather than charger_id so a duplicate OCPP identity in the
    -- source cannot abort the whole reload.
    emabler_id                BIGINT        NOT NULL,

    -- chargerDto.chargerId — the OCPP identity string. Joins to laddel.charger.ocpp_id.
    charger_id                VARCHAR(255)  NULL,

    -- chargerDto.ocppVersion — THE reason this table exists. The API sends the
    -- enum name ('Unknown', 'Version16', ...), NOT the int the spec advertises.
    ocpp_version              VARCHAR(32)   NULL,

    -- Identity / grouping.
    name                      VARCHAR(255)  NULL,
    site_id                   BIGINT        NULL,   -- joins to laddel.facility.emabler_id
    site_name                 VARCHAR(255)  NULL,
    evse_id                   VARCHAR(64)   NULL,

    -- Hardware. No laddel equivalent beyond the coarse `charger`.`brand`.
    manufacturer              VARCHAR(255)  NULL,
    model                     VARCHAR(255)  NULL,
    firmware                  VARCHAR(128)  NULL,
    serial                    VARCHAR(128)  NULL,
    charger_type              VARCHAR(16)   NULL,   -- 'AC' | 'DC' | 'Other'

    -- Integration flags.
    ocpi_integration_enabled  TINYINT(1)    NULL,
    split_evse_by_socket      TINYINT(1)    NULL,

    -- Liveness at extract time (a snapshot value — do not treat as durable).
    -- last_seen is NULL when eMabler reports .NET's DateTime.MinValue sentinel
    -- ('0001-01-01T00:00:00Z'), which MySQL DATETIME cannot represent.
    state                     VARCHAR(32)   NULL,
    last_seen                 DATETIME      NULL,
    active_connection         TINYINT(1)    NULL,

    -- Count of chargerDto.sockets[]; the sockets themselves stay in raw_json.
    socket_count              INT           NOT NULL DEFAULT 0,

    -- Lifecycle timestamps as reported by eMabler.
    installation_date         DATETIME      NULL,
    created_at                DATETIME      NULL,
    updated_at                DATETIME      NULL,

    -- Provenance: the untouched API item, and when this snapshot was taken.
    raw_json                  JSON          NOT NULL,
    extracted_at              DATETIME      NOT NULL,

    PRIMARY KEY (`emabler_id`),
    KEY `idx_emabler_charger_charger_id` (`charger_id`),
    KEY `idx_emabler_charger_site_id` (`site_id`),
    KEY `idx_emabler_charger_ocpp_version` (`ocpp_version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
