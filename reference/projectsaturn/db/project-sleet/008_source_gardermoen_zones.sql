-- ============================================================================
-- SOURCE TABLE: GardermoenZones
-- ============================================================================
-- This is a MANAGED source table. It is dropped and recreated on each DDL run.
-- Contains the per-charger zone assignment and billing partner mapping for
-- Gardermoen Leiebilservice (location 3006 / location_guid cbdd1089-...).
--
-- 5 zones: Hertz, Sixt, Avis, Europcar, Enterprise
-- 4 billing partners: Hertz=808, Sixt=809, Avis=810, Enterprise=811
-- Europcar has no billing partner (NULL).
--
-- Data source: 008_gardermoen_zones.txt (186 rows, tab-delimited)
-- ============================================================================

SET ROLE db_sleetmigration_owner;

-- Drop and recreate (managed source table)
-- CASCADE required because Target views will depend on this table
DROP TABLE IF EXISTS "Source"."GardermoenZones" CASCADE;

CREATE TABLE "Source"."GardermoenZones" (
    ocpp_id TEXT PRIMARY KEY,
    zone_name TEXT NOT NULL,
    billing_partner_id INTEGER
);

-- Insert all 186 rows with title-cased zone names and billing partner mapping
INSERT INTO "Source"."GardermoenZones" (ocpp_id, zone_name, billing_partner_id)
VALUES
    ('25321-31', 'Hertz', 808),
    ('25322-30', 'Hertz', 808),
    ('25323-29', 'Hertz', 808),
    ('25324-28', 'Hertz', 808),
    ('25325-27', 'Hertz', 808),
    ('25326-26', 'Hertz', 808),
    ('25327-25', 'Hertz', 808),
    ('25328-24', 'Hertz', 808),
    ('25329-23', 'Hertz', 808),
    ('25331-21', 'Hertz', 808),
    ('25332-20', 'Hertz', 808),
    ('25333-19', 'Hertz', 808),
    ('25334-18', 'Hertz', 808),
    ('25335-17', 'Hertz', 808),
    ('25336-16', 'Hertz', 808),
    ('25337-15', 'Hertz', 808),
    ('25338-14', 'Hertz', 808),
    ('25339-13', 'Hertz', 808),
    ('25340-12', 'Hertz', 808),
    ('25341-11', 'Hertz', 808),
    ('25342-10', 'Hertz', 808),
    ('25343-9', 'Hertz', 808),
    ('25345-7', 'Hertz', 808),
    ('25346-6', 'Hertz', 808),
    ('25347-5', 'Hertz', 808),
    ('25348-4', 'Hertz', 808),
    ('25349-3', 'Hertz', 808),
    ('25350-2', 'Hertz', 808),
    ('25597-1', 'Hertz', 808),
    ('25674-32', 'Hertz', 808),
    ('26188-22', 'Hertz', 808),
    ('26980-8', 'Hertz', 808),
    ('41528-61-1', 'Sixt', 809),
    ('41529-61-2', 'Sixt', 809),
    ('41530-62-1', 'Sixt', 809),
    ('41531-62-2', 'Sixt', 809),
    ('41532-63-1', 'Sixt', 809),
    ('41533-63-2', 'Sixt', 809),
    ('41534-64-1', 'Sixt', 809),
    ('41540-79', 'Sixt', 809),
    ('41541-80', 'Sixt', 809),
    ('41542-76', 'Sixt', 809),
    ('41543-77', 'Sixt', 809),
    ('41547-78', 'Sixt', 809),
    ('41548-75', 'Sixt', 809),
    ('41549-73', 'Sixt', 809),
    ('41550-74', 'Sixt', 809),
    ('42358-55-1', 'Sixt', 809),
    ('42421-68-2', 'Sixt', 809),
    ('42422-65-2', 'Sixt', 809),
    ('42423-66-1', 'Sixt', 809),
    ('42424-66-2', 'Sixt', 809),
    ('42451-3-2', 'Hertz', 808),
    ('42452-2-2', 'Hertz', 808),
    ('42453-2-1', 'Hertz', 808),
    ('42454-1-2', 'Hertz', 808),
    ('42455-1-1', 'Hertz', 808),
    ('42456-4-1', 'Hertz', 808),
    ('27479-4-2', 'Hertz', 808),  -- was 42457-4-2 in source file; charger renamed/replaced
    ('42458-5-1', 'Hertz', 808),
    ('42459-5-2', 'Hertz', 808),
    ('42460-6-1', 'Hertz', 808),
    ('42461-6-2', 'Hertz', 808),
    ('42462-7-1', 'Hertz', 808),
    ('42463-7-2', 'Hertz', 808),
    ('42464-8-1', 'Hertz', 808),
    ('42465-8-2', 'Hertz', 808),
    ('42466-9-1', 'Hertz', 808),
    ('42467-9-2', 'Hertz', 808),
    ('42468-10-2', 'Avis', 810),
    ('42469-10-1', 'Avis', 810),
    ('42470-11-1', 'Avis', 810),
    ('42471-11-2', 'Avis', 810),
    ('42472-12-1', 'Avis', 810),
    ('42473-12-2', 'Avis', 810),
    ('42474-23-2', 'Avis', 810),
    ('42475-24-2', 'Avis', 810),
    ('42476-24-1', 'Avis', 810),
    ('42477-25-2', 'Avis', 810),
    ('42478-23-1', 'Avis', 810),
    ('42479-22-1', 'Avis', 810),
    ('42480-22-2xx', 'Avis', 810),
    ('42481-21-1', 'Avis', 810),
    ('42482-21-2', 'Avis', 810),
    ('42483-35-2', 'Europcar', NULL),
    ('42484-20-1', 'Avis', 810),
    ('42485-20-2', 'Avis', 810),
    ('42486-19-2', 'Avis', 810),
    ('42487-19-1', 'Avis', 810),
    ('42488-18-1', 'Avis', 810),
    ('42489-18-2', 'Avis', 810),
    ('42490-17-2', 'Avis', 810),
    ('42491-3-1', 'Hertz', 808),
    ('42492-17-1', 'Avis', 810),
    ('42493-16-1', 'Avis', 810),
    ('42494-16-2', 'Avis', 810),
    ('42495-15-1', 'Avis', 810),
    ('42496-15-2', 'Avis', 810),
    ('42497-14-1', 'Avis', 810),
    ('42498-14-2', 'Avis', 810),
    ('42499-13-1', 'Avis', 810),
    ('42500-13-2', 'Avis', 810),
    ('42551-54-2', 'Sixt', 809),
    ('42552-54-1', 'Sixt', 809),
    ('42553-55-2', 'Sixt', 809),
    ('42554-55-1', 'Sixt', 809),
    ('42554-58-1', 'Sixt', 809),
    ('42555-60-1', 'Sixt', 809),
    ('42556-60-2', 'Sixt', 809),
    ('42557-59-1', 'Sixt', 809),
    ('42558-59-2', 'Sixt', 809),
    ('42560-58-2', 'Sixt', 809),
    ('42561-57-1', 'Sixt', 809),
    ('42562-57-2', 'Sixt', 809),
    ('42563-56-1', 'Sixt', 809),
    ('42564-56-2', 'Sixt', 809),
    ('42601-25-1', 'Avis', 810),
    ('42602-26-2', 'Avis', 810),
    ('42603-26-1', 'Avis', 810),
    ('42604-27-2', 'Avis', 810),
    ('42605-27-1', 'Avis', 810),
    ('42606-28-1', 'Avis', 810),
    ('42607-28-2', 'Avis', 810),
    ('42608-29-2', 'Avis', 810),
    ('42609-29-1', 'Avis', 810),
    ('42610-30-2', 'Avis', 810),
    ('42611-30-1', 'Avis', 810),
    ('42612-31-2', 'Avis', 810),
    ('42613-31-1', 'Avis', 810),
    ('42614-32-2', 'Avis', 810),
    ('42615-32-1', 'Avis', 810),
    ('42616-33-2', 'Avis', 810),
    ('42617-33-1', 'Avis', 810),
    ('42618-34-1', 'Avis', 810),
    ('42619-34-2', 'Avis', 810),
    ('42620-36-2', 'Europcar', NULL),
    ('42621-35-1', 'Europcar', NULL),
    ('42622-36-1', 'Europcar', NULL),
    ('42623-37-1', 'Europcar', NULL),
    ('42624-38-1', 'Europcar', NULL),
    ('42625-37-2', 'Europcar', NULL),
    ('42626-38-2', 'Europcar', NULL),
    ('42627-39-2', 'Europcar', NULL),
    ('42628-40-2', 'Europcar', NULL),
    ('42629-40-1', 'Europcar', NULL),
    ('42630-39-1', 'Europcar', NULL),
    ('42631-42-1', 'Europcar', NULL),
    ('42632-41-1', 'Europcar', NULL),
    ('42633-41-2', 'Europcar', NULL),
    ('42634-43-2', 'Enterprise', 811),
    ('42635-43-1', 'Enterprise', 811),
    ('42636-44-2', 'Enterprise', 811),
    ('42637-44-1', 'Enterprise', 811),
    ('42638-45-1', 'Enterprise', 811),
    ('42639-46-2', 'Enterprise', 811),
    ('42640-46-1', 'Enterprise', 811),
    ('42641-47-1', 'Enterprise', 811),
    ('42642-47-2', 'Enterprise', 811),
    ('42643-50-1', 'Enterprise', 811),
    ('42644-49-1', 'Enterprise', 811),
    ('42645-49-2', 'Enterprise', 811),
    ('42646-48-1', 'Enterprise', 811),
    ('42647-48-2', 'Enterprise', 811),
    ('42648-45-2', 'Enterprise', 811),
    ('42649-51-2', 'Sixt', 809),
    ('42650-51-1', 'Sixt', 809),
    ('42651-52-2', 'Sixt', 809),
    ('42652-53-2', 'Sixt', 809),
    ('42653-53-1', 'Sixt', 809),
    ('42654-52-1', 'Sixt', 809),
    ('43024-68-1', 'Sixt', 809),
    ('43025-71-1', 'Sixt', 809),
    ('43026-70-2', 'Sixt', 809),
    ('43027-71-2', 'Sixt', 809),
    ('43044-72-2', 'Sixt', 809),
    ('43046-70-1', 'Sixt', 809),
    ('43047-69-2', 'Sixt', 809),
    ('43048-69-1', 'Sixt', 809),
    ('43050-67-2', 'Sixt', 809),
    ('25330-22', 'Hertz', 808),
    ('25344-8', 'Hertz', 808),
    ('25351-1', 'Hertz', 808),
    ('27963-22-2', 'Avis', 810),
    ('42369-43-1', 'Enterprise', 811),
    ('42425-67-1', 'Sixt', 809),
    ('43045-72-1', 'Sixt', 809);

-- ============================================================================
-- SOURCE TABLE: GardermoenZoneSharedPartners
-- ============================================================================
-- Additional billing partners that should appear as shared partners on
-- charge points in a given Gardermoen zone.
--
-- The billing_partner_mapping_key references Mapping.billing_partner_mapping
-- so the target_partner_id is resolved dynamically after partner creation.
--
-- Example: Autoleie Oslo AS has RFID tags used on Sixt chargers and their
-- billing partner should be a shared partner on all Sixt charge points.
-- ============================================================================

DROP TABLE IF EXISTS "Source"."GardermoenZoneSharedPartners" CASCADE;

CREATE TABLE "Source"."GardermoenZoneSharedPartners" (
    zone_name TEXT NOT NULL,
    billing_partner_mapping_key TEXT NOT NULL,
    PRIMARY KEY (zone_name, billing_partner_mapping_key)
);

-- Autoleie Oslo AS (org 992432489) — shared partner on Sixt zone
INSERT INTO "Source"."GardermoenZoneSharedPartners" (zone_name, billing_partner_mapping_key)
VALUES
    ('Sixt', 'Sleet|BillingPartner|CorporateRFID|1326880');
