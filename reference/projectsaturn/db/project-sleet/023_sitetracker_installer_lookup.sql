-- ============================================================================
-- LOOKUP TABLE: SiteTracker Installer Name → SF Account
-- ============================================================================
-- Managed lookup table — safe to DROP and recreate on each DDL run.
-- Maps wattif_installer values (from location_mapping) to SiteTracker Account IDs.
--
-- Rules:
--   - installer_name_lower = LOWER(BTRIM(wattif_installer))
--   - sf_account_id = NULL for all real companies (resolved at runtime via org_number
--     → sitetracker_account_mapping). Hardcoded SF IDs are NOT used because sandbox
--     IDs won't match production.
--   - skip_reason = 'internal_staff' means skip creating INSTALLER Site Relation
--   - If a value from location_mapping is NOT in this table, it is an error
--     (script should warn/halt on unmapped installers)
--   - sf_account_name includes legal status suffix for non-active companies
--     (e.g. "slettet YYYY-MM-DD", "under avvikling", "konkurs")
-- ============================================================================

SET ROLE db_sleetmigration_owner;

DROP TABLE IF EXISTS "Mapping"."SiteTrackerInstallerLookup" CASCADE;
CREATE TABLE "Mapping"."SiteTrackerInstallerLookup" (
    installer_name_lower TEXT PRIMARY KEY,    -- LOWER(BTRIM(wattif_installer)) from location_mapping
    installer_name TEXT NOT NULL,             -- Original name from planning Excel
    sf_account_id TEXT,                       -- Salesforce Account ID (NULL = skip/no INSTALLER relation)
    sf_account_name TEXT,                     -- SF Account name for reference
    org_number TEXT,                          -- Business Registration Number for reference
    skip_reason TEXT                          -- If sf_account_id IS NULL, why (e.g. 'internal_staff')
);

-- ── Auto-resolved entries (exact/contains match from research) ───────────────
INSERT INTO "Mapping"."SiteTrackerInstallerLookup"
    (installer_name_lower, installer_name, sf_account_id, sf_account_name, org_number)
VALUES
    ('tls elektro as',   'TLS Elektro AS',   NULL, 'TLS ELEKTRO AS',        '920416721'),
    ('tls elektro',      'TLS elektro',      NULL, 'TLS ELEKTRO AS',        '920416721'),
    ('argon',            'Argon',            NULL, 'ARGON ELEKTRO AS',      '932607131');

-- ── Manually resolved entries (from user-provided Brreg research) ────────────
INSERT INTO "Mapping"."SiteTrackerInstallerLookup"
    (installer_name_lower, installer_name, sf_account_id, sf_account_name, org_number)
VALUES
    -- Bravida branches → single main org
    ('bravida kristiansand',            'Bravida Kristiansand',            NULL, 'BRAVIDA NORGE AS',                     '987582561'),
    ('bravida bergen',                  'Bravida Bergen',                  NULL, 'BRAVIDA NORGE AS',                     '987582561'),
    -- Other companies resolved via Brreg
    ('bratseth as',                     'Bratseth AS',                     NULL, 'BRATSETH AS',                          '998489520'),
    ('stian@holen-installasjon.no',     'Stian@holen-installasjon.no',     NULL, 'HOLEN INSTALLASJON AS',                '952054511'),
    ('vidar@narvikel.no',               'vidar@narvikel.no',               NULL, 'NARVIK EL AS',                         '920408133'),
    ('drammen@pettersen.no',            'drammen@pettersen.no',            NULL, 'INGENIØR IVAR PETTERSEN AS',           '961773490'),
    ('los elektro - stavanger',         'LOS Elektro - Stavanger',         NULL, 'LOS ELEKTRO AS',                       '978632378'),
    ('elektro sør, tratec as',          'Elektro Sør, Tratec AS',          NULL, 'ELEKTRO SØR AS',                       '980684989'),
    ('are@ryfylke-elektro.no',          'are@ryfylke-elektro.no',          NULL, 'RYFYLKE ELEKTRO AS',                   '987814136'),
    ('ed@ea-teknikk.no',                'ed@ea-teknikk.no',                NULL, 'ELEKTRO OG AUTOMASJONSTEKNIKK AS',     '912177718'),
    ('kjell.sigve.oye@helgevold.com',   'kjell.sigve.oye@helgevold.com',   NULL, 'HELGEVOLD AS',                         '986389180'),
    ('lyngdal el-installasjon as',      'Lyngdal El-Installasjon AS',      NULL, 'LYNGDAL EL-INSTALASJON AS',            '958553625'),
    ('rauland elektric',                'Rauland Elektric',                NULL, 'RAULAND ELEKTRISKE AS',                '997985451');

-- ── Skipped entries: Statkraft internal staff (no INSTALLER relation) ────────
INSERT INTO "Mapping"."SiteTrackerInstallerLookup"
    (installer_name_lower, installer_name, sf_account_id, sf_account_name, skip_reason)
VALUES
    ('statkraft egne installatører',                    'Statkraft Egne installatører',                    NULL, NULL, 'internal_staff'),
    ('aleksander.gilje@statkraft.com',                  'Aleksander.Gilje@statkraft.com',                  NULL, NULL, 'internal_staff'),
    ('anders.dahle@statkraft.com',                      'Anders.Dahle@statkraft.com',                      NULL, NULL, 'internal_staff'),
    ('andras.komaromi@statkraft.com',                   'Andras.Komaromi@statkraft.com',                   NULL, NULL, 'internal_staff'),
    ('andreas.bakkelund@statkraft.com',                 'Andreas.Bakkelund@statkraft.com',                 NULL, NULL, 'internal_staff'),
    ('bjarne.rikstad@statkraft.com',                    'Bjarne.Rikstad@statkraft.com',                    NULL, NULL, 'internal_staff'),
    ('espen.sletten@statkraft.com',                     'Espen.Sletten@statkraft.com',                     NULL, NULL, 'internal_staff'),
    ('geir.raaum@statkraft.com',                        'Geir.Raaum@statkraft.com',                        NULL, NULL, 'internal_staff'),
    ('hans-tore.bjerkas@statkraft.com',                 'Hans-Tore.Bjerkas@statkraft.com',                 NULL, NULL, 'internal_staff'),
    ('jan.erling.grini@statkraft.com',                  'Jan.Erling.Grini@statkraft.com',                  NULL, NULL, 'internal_staff'),
    ('kristofer.lindstrom@statkraft.com',               'Kristofer.Lindstrom@statkraft.com',               NULL, NULL, 'internal_staff'),
    ('oystein.holstad@statkraft.com',                   'Oystein.Holstad@statkraft.com',                   NULL, NULL, 'internal_staff'),
    ('per-ove.farstad@statkraft.com',                   'Per-Ove.Farstad@statkraft.com',                   NULL, NULL, 'internal_staff'),
    ('svein.kenneth.kittelsen@statkraft.com',           'Svein.Kenneth.Kittelsen@statkraft.com',           NULL, NULL, 'internal_staff'),
    ('terje.dahl@statkraft.com',                        'Terje.Dahl@statkraft.com',                        NULL, NULL, 'internal_staff'),
    ('thomas.r.haugen@statkraft.com',                   'Thomas.R.Haugen@statkraft.com',                   NULL, NULL, 'internal_staff'),
    ('svenerik.riber@statkraft.com',                    'svenerik.riber@statkraft.com',                    NULL, NULL, 'internal_staff'),
    ('sveinerik.hagen@statkraft.com',                   'sveinerik.hagen@statkraft.com',                   NULL, NULL, 'internal_staff'),
    ('anders.herje@statkraft.com',                      'anders.herje@statkraft.com',                      NULL, NULL, 'internal_staff'),
    ('roy.aune@statkraft.com',                          'Roy.Aune@statkraft.com',                          NULL, NULL, 'internal_staff'),
    ('are.saether@statkraft.com',                       'are.saether@statkraft.com',                       NULL, NULL, 'internal_staff'),
    ('knutoverland.steinsheim@statkraft.com',           'knutoverland.steinsheim@statkraft.com',           NULL, NULL, 'internal_staff'),
    ('olekristian.bronstad@statkraft.com',              'olekristian.bronstad@statkraft.com',              NULL, NULL, 'internal_staff'),
    ('joakim.velleaurdal@statkraft.com',                'joakim.velleaurdal@statkraft.com',                NULL, NULL, 'internal_staff'),
    ('bjornandre.stendalen@statkraft.com',              'bjornandre.stendalen@statkraft.com',              NULL, NULL, 'internal_staff'),
    ('rolf.vikan@statkraft.com',                        'rolf.vikan@statkraft.com',                        NULL, NULL, 'internal_staff'),
    ('erlend.rosseland@statkraft.com',                  'erlend.rosseland@statkraft.com',                  NULL, NULL, 'internal_staff'),
    ('jorgen.nordsether@statkraft.com',                 'jorgen.nordsether@statkraft.com',                 NULL, NULL, 'internal_staff'),
    ('christoffer-andre.eidem@statkraft.com',           'Christoffer-andre.eidem@statkraft.com',           NULL, NULL, 'internal_staff'),
    ('stianandre.hagen@statkraft.com',                  'stianandre.hagen@statkraft.com',                  NULL, NULL, 'internal_staff'),
    ('frode.vitso@statkraft.com',                       'frode.vitso@statkraft.com',                       NULL, NULL, 'internal_staff');

-- NOTE: All entries have sf_account_id = NULL. The Account creation script resolves
-- accounts by org_number at runtime (via sitetracker_account_mapping). This makes
-- the pipeline environment-agnostic — sandbox and production will each get their own
-- correct SF IDs through the mapping table.
--
-- NOTE: Any @statkraft.com email not listed here should also be treated as
-- 'internal_staff' (skip). The script should match by LIKE '%@statkraft.com'
-- as a fallback for new emails that appear in future data imports.
