SET ROLE db_sleetmigration_owner;

-- Phase 0: Empty view structure for Target.SubscriptionPlan
DROP VIEW IF EXISTS "Target"."SubscriptionPlan";

CREATE OR REPLACE VIEW "Target"."SubscriptionPlan" AS
SELECT 
    NULL::TEXT AS mapping_table,
    NULL::TEXT AS mapping_key,
    NULL::TEXT AS merge_with_mapping_key,
    NULL::INTEGER AS "TargetSubscriptionPlanID",
    NULL::TEXT AS "ProjectCode",
    NULL::TEXT AS "renewalCycle",
    NULL::TEXT AS "type",
    NULL::TEXT AS "status",
    NULL::TEXT AS "postPaidChargingSessionsAccumulation",
    NULL::TEXT AS "visibilityRestrictions_includedPartnerUsers",
    NULL::INTEGER AS "billingUsageThreshold",
    NULL::NUMERIC AS "baseFee",
    NULL::BOOLEAN AS "baseFeeAppliesPerEachHomeCharger",
    NULL::INTEGER AS "freeRenewalPeriods",
    NULL::TEXT AS "name_en",
    NULL::INTEGER AS "TargetPartnerID",
    NULL::INTEGER AS "SourceAccountID"
WHERE 1=0;
