-- ============================================================================
-- LOOKUP TABLE: Historical Installer (from Mer) → SF Account
-- ============================================================================
-- Maps installer_company values from Source.Locations (Mer export) to
-- SiteTracker Account IDs. These are the companies that originally installed
-- the chargers before the Wattif migration.
--
-- Similar to SiteTrackerInstallerLookup but keyed on Mer's installer_company
-- field rather than wattif_installer from planning.
--
-- sf_account_id is always NULL — accounts are resolved at runtime via org_number
-- → sitetracker_account_mapping. This ensures environment-agnostic behavior
-- (sandbox IDs won't match production).
--
-- sf_account_name includes legal status suffix for non-active companies
-- (e.g. "slettet YYYY-MM-DD", "under avvikling", "konkurs").
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP TABLE IF EXISTS "Mapping"."SiteTrackerHistoricalInstallerLookup" CASCADE;
CREATE TABLE "Mapping"."SiteTrackerHistoricalInstallerLookup" (
    mer_installer_name_lower TEXT PRIMARY KEY,   -- LOWER(BTRIM(installer_company)) from Source.Locations
    mer_installer_name TEXT NOT NULL,            -- Original name from Mer export
    sf_account_id TEXT,                          -- Salesforce Account ID (NULL = resolve by org_number at runtime)
    sf_account_name TEXT,                        -- SF Account name for reference
    org_number TEXT,                             -- Business Registration Number
    skip_reason TEXT                             -- If set, skip creating relation for this installer
);

-- ── Entries that match existing SiteTrackerInstallerLookup companies ─────────
INSERT INTO "Mapping"."SiteTrackerHistoricalInstallerLookup"
    (mer_installer_name_lower, mer_installer_name, sf_account_id, sf_account_name, org_number)
VALUES
    ('tls elektro as',                              'TLS Elektro AS',                              NULL, 'TLS ELEKTRO AS',                       '920416721'),
    ('bravida kristiansand',                        'Bravida Kristiansand',                        NULL, 'BRAVIDA NORGE AS',                     '987582561'),
    ('bratseth avd. sunndalsøra',                   'Bratseth avd. Sunndalsøra',                   NULL, 'BRATSETH AS',                          '998489520'),
    ('bratseth',                                    'Bratseth',                                    NULL, 'BRATSETH AS',                          '998489520'),
    ('rauland elektriske',                          'Rauland Elektriske',                          NULL, 'RAULAND ELEKTRISKE AS',                '997985451');

-- ── Auto-resolved via SiteTracker + Brreg search ────────────────────────────
INSERT INTO "Mapping"."SiteTrackerHistoricalInstallerLookup"
    (mer_installer_name_lower, mer_installer_name, sf_account_id, sf_account_name, org_number)
VALUES
    ('otera infra as',                              'Otera Infra AS',                              NULL, 'OTERA INFRA AS',                       '995845067'),
    ('otera infra as avd. kristiansand',            'Otera Infra AS avd. Kristiansand',            NULL, 'OTERA INFRA AS',                       '995845067'),
    ('caverion norge as',                           'Caverion Norge AS',                           NULL, 'CAVERION NORGE AS',                    '959069743'),
    ('caverion mandal og kristiansand',             'Caverion Mandal og Kristiansand',             NULL, 'CAVERION NORGE AS',                    '959069743'),
    ('elektriker bekkevold as',                     'Elektriker Bekkevold AS',                     NULL, 'ELEKTRIKER BEKKEVOLD AS',              '986113339'),
    ('el partner',                                  'EL Partner',                                  NULL, 'EL PARTNER AS (slettet 2026-03-26)',   '991255338'),
    ('oneco stavanger',                             'Oneco Stavanger',                             NULL, 'ONECO ELEKTRO AS',                     '996669173'),
    ('sterk elektro as',                            'Sterk Elektro AS',                            NULL, 'STERK-ELEKTRO AS (slettet 2024-02-05)', '952743635'),
    ('straume elektriske',                          'Straume Elektriske',                          NULL, 'STRAUME ELEKTRISKE AS',                '996619583');

-- ── Resolved via Brreg (not yet in SiteTracker — will be created as Accounts) ─
INSERT INTO "Mapping"."SiteTrackerHistoricalInstallerLookup"
    (mer_installer_name_lower, mer_installer_name, sf_account_id, sf_account_name, org_number)
VALUES
    ('øra elektro',                                 'Øra Elektro',                                 NULL, 'ØRA ELEKTRO AS',                       '827026042'),
    ('strømsborg elektro as',                       'Strømsborg Elektro AS',                       NULL, 'STRØMSBORG ELEKTRO AS',                '948685957'),
    ('hemnes el-installasjon as',                   'Hemnes El-Installasjon AS',                   NULL, 'HEMNES EL-INSTALLASJON AS',            '930824208'),
    ('krøderen elektro as avd. hallingdal',         'Krøderen Elektro AS Avd. Hallingdal',         NULL, 'KRØDEREN ELEKTRO AS',                  '941163343');

-- ── Manually resolved entries ────────────────────────────────────────────────
-- Resolved via manual Brreg/business-registry research (2026-05-02).
INSERT INTO "Mapping"."SiteTrackerHistoricalInstallerLookup"
    (mer_installer_name_lower, mer_installer_name, sf_account_id, sf_account_name, org_number)
VALUES
    ('elektro 4',                                   'Elektro 4',                                   NULL, 'ELEKTRO 4 AS',                                    '881489392'),
    ('nordlien elektro',                            'Nordlien Elektro',                            NULL, 'NORDLIEN ELEKTRO AS (slettet 2025-11-20)',         '915922376'),
    ('elektro-entreprenøren arendal as',            'Elektro-Entreprenøren Arendal AS',            NULL, 'ELEKTRO-ENTREPRENØREN ARENDAL AS (slettet 2022-11-05)', '950257849'),
    ('sønnico oslo',                                'Sønnico Oslo',                                NULL, 'ONECO ELEKTRO AS',                                '996669173'),
    ('sønnico trondheim',                           'Sønnico Trondheim',                           NULL, 'ONECO ELEKTRO AS',                                '996669173'),
    ('agder el installasjon',                       'Agder El Installasjon',                       NULL, 'AGDER EL INSTALLASJON AS',                        '985170266'),
    ('sørlandets elektro',                          'Sørlandets elektro',                          NULL, 'SØRLANDETS ELEKTRO AS',                           '896630342'),
    ('rune  konnestad',                             'Rune  Konnestad',                             NULL, 'ARENDAL EL TEAM AS',                              '916544480'),
    ('birger gundersen',                            'Birger Gundersen',                            NULL, 'ELEKTRO SØR AS',                                  '980684989');

-- ── Skipped: Statkraft internal installers ───────────────────────────────────
INSERT INTO "Mapping"."SiteTrackerHistoricalInstallerLookup"
    (mer_installer_name_lower, mer_installer_name, sf_account_id, sf_account_name, skip_reason)
VALUES
    ('statkraft egne installatører',                'Statkraft Egne installatører',                NULL, NULL, 'internal_staff'),
    ('statkraft energi as – kg hardanger, bjølvo',  'Statkraft Energi AS – KG Hardanger, Bjølvo', NULL, NULL, 'internal_staff');

-- NOTE: All entries have sf_account_id = NULL. The Account creation script resolves
-- accounts by org_number at runtime (via sitetracker_account_mapping). This makes
-- the pipeline environment-agnostic — sandbox and production will each get their own
-- correct SF IDs through the mapping table.
