SET ROLE db_sleetmigration_owner;

-- Phase 2: Target.ChargePoints view for Project Sleet
-- Maps Source.Chargers to Ampeco ChargePoint API payload format
-- 
-- Join path:
--   Source.Chargers c -> Source.Locations loc ON c.location_nr = loc.location_nr
--   Source.Locations loc -> Mapping.location_mapping lm ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
--   Source.Chargers c -> Mapping.charger_mapping cm ON 'Sleet|Charger|' || c."Id"::TEXT = cm.mapping_key
--   Source.ElectricalSettingsNormalized esn ON (charger_id, is_first_connector = TRUE) for electrical settings
--
-- All electrical settings are pure passthrough from Source.ElectricalSettingsNormalized (099),
-- which centralizes the business logic for both Target views and Quality Reports.

DROP VIEW IF EXISTS "Target"."ChargePoints";

CREATE OR REPLACE VIEW "Target"."ChargePoints" AS
WITH chargers_in_scope AS (
    -- CTE 1: Get all chargers for locations marked for migration
    -- LEFT JOIN GardermoenZones for per-charger zone assignment and billing partner
    SELECT 
        c.charger_id,
        c."Id" AS charger_guid,
        c.charger_name,
        c.location_nr,
        c.availability,
        lm.mapping_key AS location_mapping_key,
        lm.project_code,
        lm.partner_model,
        lm.target_location_id,
        lm.target_charge_zone_id,
        lm.target_partner_contract_id,
        loc.city,
        loc.address,
        c.status AS charger_status,
        -- Gardermoen multi-zone: per-charger zone name and billing partner
        gz.zone_name AS gz_zone_name,
        gz.billing_partner_id AS gz_billing_partner_id
    FROM "Source"."Chargers" c
    JOIN "Source"."Locations" loc ON loc.location_nr = c.location_nr
    JOIN "Mapping"."location_mapping" lm ON 'Sleet|Location|' || loc."Id"::TEXT = lm.mapping_key
    LEFT JOIN "Source"."GardermoenZones" gz ON gz.ocpp_id = c.charger_name
    WHERE lm.migrate = TRUE
),
with_sequence AS (
    -- CTE 2: Add sequence number per project_code for unique naming
    SELECT 
        cis.*,
        ROW_NUMBER() OVER (
            PARTITION BY cis.project_code 
            ORDER BY cis.location_nr, cis.charger_name, cis.charger_id
        ) AS seq_num,
        -- Tags: Source:MerB2B + Owner based on partner_model (CO* = Customer, WO* = Wattif)
        '["Source:MerB2B","Owner:' || 
            CASE 
                WHEN UPPER(cis.partner_model) LIKE 'WO%' THEN 'Wattif'
                ELSE 'Customer'
            END || '"]' AS tags
    FROM chargers_in_scope cis
),
with_codes AS (
    -- CTE 3: Derive city_code and address_code (uppercase, no diacritics, alpha only)
    SELECT 
        ws.*,
        LEFT(
            regexp_replace(
                translate(UPPER(COALESCE(ws.city, '')), 'ÉÈÍÓÚÛÅÄÆÖØ', 'EEIOUUAAAOO'),
                '[^A-Z]', '', 'g'
            ) || 'XXX', 
            3
        ) AS city_code,
        LEFT(
            regexp_replace(
                translate(UPPER(COALESCE(ws.address, '')), 'ÉÈÍÓÚÛÅÄÆÖØ', 'EEIOUUAAAOO'),
                '[^A-Z]', '', 'g'
            ) || 'XXX', 
            3
        ) AS address_code
    FROM with_sequence ws
),
with_mappings AS (
    -- CTE 4: Join with charger_mapping, project_code_mapping, MasterPartnerResolution,
    -- and zone_mapping (for multi-zone locations like Gardermoen) for target IDs.
    -- Also resolves additional shared billing partners per zone via
    -- GardermoenZoneSharedPartners → billing_partner_mapping.
    SELECT 
        wc.*,
        'Sleet|Charger|' || wc.charger_guid::TEXT AS mapping_key,
        cm.target_charge_point_id,
        pcm.target_partner_id,
        -- Multi-zone override: per-charger zone ID from zone_mapping (NULL for standard locations)
        zm.target_charge_zone_id AS zone_mapping_charge_zone_id,
        -- Shared partners: merge org-level master partner + zone billing partner
        --                   + additional zone-level shared billing partners
        (SELECT '[' || string_agg(pid::TEXT, ',') || ']'
         FROM (
            -- 1. Org-level master partner (when different from charge point's own partner)
            SELECT mpr.master_target_partner_id AS pid
             WHERE mpr.master_target_partner_id IS NOT NULL
               AND pcm.target_partner_id IS NOT NULL
               AND mpr.master_target_partner_id <> pcm.target_partner_id
            UNION
            -- 2. Zone billing partner from GardermoenZones
            SELECT wc.gz_billing_partner_id AS pid
             WHERE wc.gz_billing_partner_id IS NOT NULL
            UNION
            -- 3. Additional zone-level shared billing partners
            SELECT bpm.target_partner_id AS pid
              FROM "Source"."GardermoenZoneSharedPartners" gzsp
              JOIN "Mapping"."billing_partner_mapping" bpm
                ON bpm.mapping_key = gzsp.billing_partner_mapping_key
             WHERE gzsp.zone_name = wc.gz_zone_name
               AND bpm.target_partner_id IS NOT NULL
         ) sub
         WHERE sub.pid IS NOT NULL
        ) AS shared_partner_ids,
        EXISTS (
            SELECT 1 FROM "Source"."Connectors" conn
            WHERE conn.id = wc.charger_id AND conn.status_reason = 'Active'
        ) AS has_active_connectors
    FROM with_codes wc
    LEFT JOIN "Mapping"."charger_mapping" cm ON cm.mapping_key = 'Sleet|Charger|' || wc.charger_guid::TEXT
    LEFT JOIN "Mapping"."project_code_mapping" pcm ON pcm.project_code = wc.project_code
    LEFT JOIN "Mapping"."MasterPartnerResolution" mpr ON mpr.mapping_key = wc.location_mapping_key
    LEFT JOIN "Mapping"."zone_mapping" zm 
        ON wc.gz_zone_name IS NOT NULL
        AND zm.mapping_key = REPLACE(wc.location_mapping_key, 'Sleet|Location|', 'Sleet|Zone|') || '|' || wc.gz_zone_name
),
with_electrical_settings AS (
    -- CTE 5: Electrical settings passthrough from ESN (first connector per charger)
    SELECT 
        wm.*,
        esn.target_chargepoint_electrical_configuration,
        esn.target_chargepoint_max_voltage,
        esn.target_chargepoint_phases,
        esn.target_cp_phase_rotation,
        esn.target_chargepoint_connected_phase,
        esn.target_cp_default_max_current
    FROM with_mappings wm
    LEFT JOIN "Source"."ElectricalSettingsNormalized" esn 
        ON esn.charger_id = wm.charger_id 
        AND esn.is_first_connector = TRUE
)
-- Final SELECT: Map all columns per Ampeco ChargePoint API specification
SELECT 
    -- Mapping columns
    'charger_mapping'::TEXT AS mapping_table,
    wes.mapping_key,
    NULL::TEXT AS merge_with_mapping_key,
    
    -- Target IDs
    wes.target_partner_contract_id AS "TargetPartnerContractID",
    wes.target_location_id AS "TargetLocationID",
    wes.target_charge_point_id AS "TargetChargePointID",
    
    -- ChargePoint type (public/private based on availability)
    CASE 
        WHEN wes.availability = 'Public' THEN 'public'
        ELSE 'private'
    END AS "type",
    
    -- Status
    CASE 
        WHEN wes.charger_status = 'Inactive' THEN 'disabled'
        WHEN NOT wes.has_active_connectors THEN 'disabled'
        ELSE 'enabled'
    END::TEXT AS "status",
    
    -- Network configuration
    wes.charger_name AS "network_id",
    'ocpp 1.6'::TEXT AS "network_protocol",
    2 AS "security_desiredProfile",
    
    -- Derived name: NOR + city_code + address_code + seq_num (e.g., NOROSLFRI001)
    ('NOR' || wes.city_code || wes.address_code || to_char(wes.seq_num, 'FM000'))::TEXT AS "name",
    
    -- PIN derived from charger GUID hash (4-digit, 0001-9999)
    to_char(
        (('x' || substr(md5(wes.charger_guid::text), 1, 8))::bit(32)::bigint % 9999) + 1, 
        'FM0000'
    )::TEXT AS "pin",
    
    -- Location references
    wes.target_location_id AS "locationId",
    -- Multi-zone override: use zone_mapping zone ID if available, else default from location_mapping
    COALESCE(wes.zone_mapping_charge_zone_id, wes.target_charge_zone_id) AS "chargingZoneId",
    NULL::INTEGER AS "electricityRateId",
    
    -- Subscription (not required for Sleet)
    FALSE AS "subscription_required",
    '[]'::TEXT AS "subscription_planIds",
    
    -- External ID (Sleet: + charger GUID for uniqueness)
    ('Sleet:' || wes.charger_guid::TEXT) AS "externalId",
    
    -- Capabilities
    '["remote_start_stop_capable","meter_values","stop_transaction_on_ev_disconnect"]'::TEXT AS "capabilities",
    
    -- Behavior flags
    FALSE AS "autoStartWithoutAuthorization",
    FALSE AS "disableAutoStartEmulation",
    NULL::INTEGER AS "modelId",
    TRUE AS "enableAutoFaultRecovery",
    
    -- User assignment (not used for Sleet)
    NULL::INTEGER AS "user_id",
    
    -- Partner assignment
    wes.target_partner_id AS "partner_id",
    wes.target_partner_contract_id AS "partner_contractId",
    FALSE AS "partner_corporateBillingAsDefault",
    CASE 
        WHEN wes.availability != 'Public' THEN 'private_view_public_use'
        ELSE NULL
    END AS "partner_accessType",
    
    -- Shared partners (master partner ID for multi-partner organisations)
    -- Only applicable for private charge points (Ampeco rejects shared partners on public CPs)
    CASE 
        WHEN wes.availability = 'Public' THEN NULL
        ELSE wes.shared_partner_ids
    END AS "shared_partner_ids",
    
    -- Utility (not used)
    NULL::INTEGER AS "utilityId",
    
    -- Tags for filtering (source:MerB2B + owner based on partner_model)
    wes.tags::TEXT AS "tags",
    
    -- Notice (not used)
    NULL::INTEGER AS "noticeId",
    
    -- Timestamps (not used)
    NULL::TEXT AS "integratedAt",
    NULL::TEXT AS "manufacturedAt",
    
    -- Smart Charging configuration (API: POST /resources/charge-points/v2.0/{chargePoint}/smart-charging)
    -- electricalConfiguration must be set before EVSE creation to allow maxVoltage='230' on IT networks
    -- All electrical settings derived from Source.ElectricalSettingsNormalized (single source of truth)
    'dynamic'::TEXT AS "smartcharging_mode",
    wes.target_chargepoint_electrical_configuration AS "smartcharging_electricalConfiguration",
    wes.target_chargepoint_max_voltage AS "smartcharging_maxVoltage",
    wes.target_chargepoint_phases AS "smartcharging_phases",
    wes.target_cp_phase_rotation AS "smartcharging_phaseRotation",
    wes.target_chargepoint_connected_phase AS "smartcharging_connectedPhase",
    wes.target_cp_default_max_current AS "smartcharging_defaultChargePointMaxCurrent",
    6 AS "smartcharging_minCurrent",
    
    -- Source IDs for Sleet detection and mapping (snake_case triggers Sleet branch in Python)
    wes.charger_id AS source_charger_id,
    
    -- Legacy source IDs (PascalCase for other projects - keep as NULL for Sleet)
    NULL::INTEGER AS "SourceChargerID",
    NULL::INTEGER AS "SourceChargeBoxID",
    NULL::INTEGER AS "SourceStationID",
    wes.project_code AS "ProjectCode"

FROM with_electrical_settings wes;
