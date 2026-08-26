-- ============================================================================
-- View: target.sitetracker_site_relations
-- Depends on: target.sitetracker_site_mapping (004),
--             target.sitetracker_site_relation_mapping (005),
--             target.sitetracker_account_mapping (002),
--             read-only `laddel` source.
-- Drop-and-recreate. Reads from the read-only `laddel` source database.
--
-- Grain: one Site Relation per `laddel.facility` (facility_contact is verified
-- 1:1 per facility — see docs/fieldmapping/sitetracker_site_relation.md). Batch
-- gate: `organization.migration_status IN ('READY', 'MIGRATE')` — same gate as
-- `target`.`sitetracker_accounts` (314) and `target`.`sitetracker_sites` (315)
-- — AND the facility must be migration-eligible per
-- `target`.`facility_migration_eligibility` (201), same rule as 315, so a
-- relation is never proposed for a facility whose Site itself is excluded.
--
-- Maps `laddel` onto the SiteTracker (Salesforce) "create Site_Relation__c"
-- payload (POST /services/data/vXX.0/sobjects/Site_Relation__c/). See
-- docs/fieldmapping/sitetracker_site_relation.md.
--
-- Hard dependency gate (MigrationPatternGuide §5.2): a relation can only be
-- created once BOTH its Site and its Account already exist in SiteTracker, and
-- only when the facility actually has a linked customer. All three conditions
-- are enforced in the WHERE clause below (not just left as NULL-able payload
-- fields) — a facility failing any of them emits no row at all.
--
-- Idempotency is by `mapping_key` only — same simple pattern as
-- `sitetracker_accounts`/`sitetracker_sites`: no SOQL lookup/adopt. See
-- docs/fieldmapping/sitetracker_site_relation.md for the accepted risk (a lost
-- mapping write could create a duplicate on re-run).
-- ============================================================================
DROP VIEW IF EXISTS `target`.`sitetracker_site_relations`;

CREATE OR REPLACE VIEW `target`.`sitetracker_site_relations` AS
SELECT
    -- -- SOURCE ----------------------------------------------------------------
    CONCAT('Laddel|Facility|', f.facility_id)                           AS mapping_key,
    CONCAT(
        REGEXP_REPLACE(f.facility_name, '^[\\p{Z}\\p{C}]+|[\\p{Z}\\p{C}]+$', ''),
        ' (fac=', f.facility_id, ')'
    )                                                                   AS source_label,

    -- -- TARGET ID(S) -----------------------------------------------------------
    ssrm.target_sf_site_relation_id                                     AS target_sf_site_relation_id,

    -- -- PAYLOAD (Salesforce Site_Relation__c field names, 1:1) ---------------
    ssm.target_sf_site_id                                               AS `Site__c`,
    sam.target_sf_account_id                                            AS `Company__c`,
    'OWNER of SITE'                                                     AS `Site_Relation_Role__c`,
    DATE(f.creation_date)                                               AS `Site_Relation_Start_Date__c`,
    'Laddel (eMabler)'                                                  AS `previous_CPO__c`

FROM `laddel`.`facility` f
JOIN `laddel`.`organization` o          ON o.organization_id = f.organization_id
JOIN `target`.`facility_migration_eligibility` fme ON fme.facility_id = f.facility_id
LEFT JOIN `laddel`.`facility_contact` fc ON fc.facility_id   = f.facility_id
LEFT JOIN `laddel`.`customer` c          ON c.customer_id    = fc.customer_id
LEFT JOIN `target`.`sitetracker_site_mapping` ssm
    ON ssm.mapping_key = CONCAT('Laddel|Facility|', f.facility_id)
LEFT JOIN `target`.`sitetracker_account_mapping` sam
    ON sam.mapping_key = CONCAT('Laddel|Customer|', fc.customer_id)
LEFT JOIN `target`.`sitetracker_site_relation_mapping` ssrm
    ON ssrm.mapping_key = CONCAT('Laddel|Facility|', f.facility_id)
WHERE o.migration_status IN ('READY', 'MIGRATE')
  AND fme.should_not_migrate = 0
  AND fc.customer_id IS NOT NULL
  AND ssm.target_sf_site_id IS NOT NULL
  AND sam.target_sf_account_id IS NOT NULL;
