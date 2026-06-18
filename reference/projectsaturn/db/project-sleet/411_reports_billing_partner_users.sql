-- ============================================================================
-- REPORT VIEW: Reports.BillingPartnerUsers
-- ============================================================================
-- Lists all users linked to billing partners from the billing_partner_mapping
-- table, enriched with names and org numbers from Source.BillingAccounts.
--
-- One row per billing partner (keyed by account_owner_guid).
--
-- Columns:
--   target_user_id    — Ampeco user ID (from user_mapping)
--   target_partner_id — Ampeco billing partner ID (from billing_partner_mapping)
--   account_owner_name, org_number — informational
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP VIEW IF EXISTS "Reports"."BillingPartnerUsers";

CREATE OR REPLACE VIEW "Reports"."BillingPartnerUsers" AS
SELECT
    REPLACE(bpm.mapping_key, 'Sleet|BillingPartner|', '') AS account_owner_guid,
    ba.account_owner_name,
    ba.org_number,
    um.target_user_id,
    bpm.target_partner_id
FROM "Mapping"."billing_partner_mapping" bpm
LEFT JOIN "Source"."BillingAccounts" ba
    ON ba.account_owner_guid = REPLACE(bpm.mapping_key, 'Sleet|BillingPartner|', '')
LEFT JOIN "Mapping"."user_mapping" um
    ON um.mapping_key = 'Sleet|Account|' || REPLACE(bpm.mapping_key, 'Sleet|BillingPartner|', '')
ORDER BY ba.account_owner_name;
