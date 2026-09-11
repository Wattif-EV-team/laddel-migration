-- ============================================================================
-- MAPPING TABLE: target.charge_point_mapping
-- ============================================================================
-- ⚠️  NEVER DROP THIS TABLE — it stores migration state and target-system IDs.
--
-- Persistent mapping table (data preserved across migrations):
--   • Create with CREATE TABLE IF NOT EXISTS (never DROP + recreate).
--   • Add columns with a guarded ALTER TABLE — MySQL 8 has NO
--     `ADD COLUMN IF NOT EXISTS`, so check information_schema first rather than
--     dropping the table.
--
-- Key format: Laddel|Charger|{charger_id}  (one Ampeco charge point per
-- `laddel.charger` row). The key uses the SOURCE table name, not the
-- target-system entity name — see MigrationPatternGuide.md §5.1.
--
-- Written by the create-or-update charge point step after each create or
-- adoption: one INSERT per charge point. The id is joined back into
-- `target`.`charge_points` twice — once as the row's own
-- `target_charge_point_id`, and once through `master_mapping_key` to resolve a
-- satellite's `ocppConnectedChargePointId`. That self-join is why the step runs
-- two passes: masters first, then satellites, which re-read the view and pick up
-- the master ids written moments earlier.
--
-- Unlike partners (001), locations (003) and partner contracts (006), charge
-- points DO have a reliable natural key in Ampeco: `network.id` (the OCPP id).
-- The step therefore looks up `filter[networkId]` before creating and adopts any
-- pre-existing record, so the adoption columns below are populated:
--   • charge_point_existed_before_migration — 0 = we created it, 1 = adopted.
--   • matched_by                            — 'created' | 'network_id'.
--   • previous_record_snapshot              — the adopted record as returned by
--     Ampeco BEFORE our first PATCH, kept as an audit trail / rollback aid.
-- Satellites have no `network.id` and can never be adopted; they are always
-- 'created'.
-- ============================================================================

CREATE TABLE IF NOT EXISTS `target`.`charge_point_mapping` (
    -- Composite key emitted by `target`.`charge_points`
    -- (Laddel|Charger|{charger_id}).
    mapping_key                            VARCHAR(255) NOT NULL,

    -- Ampeco charge point id returned on create / found on adoption.
    target_charge_point_id                 BIGINT       NULL,

    -- Adoption audit trail (see the header).
    charge_point_existed_before_migration  TINYINT(1)   NULL,
    matched_by                             VARCHAR(64)  NULL,
    previous_record_snapshot               JSON         NULL,

    PRIMARY KEY (mapping_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
