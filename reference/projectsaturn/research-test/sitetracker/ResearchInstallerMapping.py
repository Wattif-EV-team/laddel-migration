"""
SiteTracker Integration — Phase 0: Installer Mapping Research
=============================================================
Queries:
  1. Distinct wattif_installer values from location_mapping (load_to_sitetracker=TRUE)
  2. Distinct installer_company values from Source.Locations (for cross-reference)
  3. SiteTracker sandbox: all Accounts linked via INSTALLER role in Site Relations
  4. SiteTracker sandbox: all Accounts with Type = 'Sub-contractor'

Outputs a comparison table showing source installers vs best SiteTracker matches.

Usage:
    .venv\\Scripts\\python.exe research-test/sitetracker/ResearchInstallerMapping.py
"""

import os
import sys
import re
import logging
from collections import defaultdict

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv()

from utils.config_utils import get_db_connection_string
from utils.dbutils import get_db_connection
from utils.sitetracker_utils import (
    get_sitetracker_token,
    sitetracker_soql_query_all,
    sitetracker_soql_query,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def normalize_name(name):
    """Normalize a company name for fuzzy matching."""
    if not name:
        return ""
    # Lowercase, strip common suffixes, normalize whitespace
    n = name.lower().strip()
    # Remove common Norwegian/English company suffixes
    n = re.sub(r'\b(as|a/s|ans|sa|asa|da|ba|ltd|inc|gmbh|ab)\b', '', n)
    # Remove punctuation
    n = re.sub(r'[^\w\s]', '', n)
    # Collapse whitespace
    n = re.sub(r'\s+', ' ', n).strip()
    return n


def query_wattif_installers(conn):
    """Get distinct wattif_installer values from location_mapping where load_to_sitetracker=TRUE."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            wattif_installer,
            COUNT(*) AS location_count,
            STRING_AGG(DISTINCT project_code, ', ' ORDER BY project_code) AS sample_project_codes
        FROM "Mapping"."location_mapping"
        WHERE load_to_sitetracker = TRUE
          AND wattif_installer IS NOT NULL
          AND btrim(wattif_installer) <> ''
        GROUP BY wattif_installer
        ORDER BY COUNT(*) DESC
    ''')
    rows = cursor.fetchall()
    cursor.close()
    return [(row[0], row[1], row[2]) for row in rows]


def query_source_installers(conn):
    """Get distinct installer_company from Source.Locations (for cross-reference)."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            installer_company,
            installer_company_guid,
            COUNT(*) AS location_count
        FROM "Source"."Locations"
        WHERE installer_company IS NOT NULL
          AND btrim(installer_company) <> ''
        GROUP BY installer_company, installer_company_guid
        ORDER BY COUNT(*) DESC
    ''')
    rows = cursor.fetchall()
    cursor.close()
    return [(row[0], row[1], row[2]) for row in rows]


def query_sitetracker_installer_accounts():
    """Query SiteTracker for all Accounts that have an INSTALLER Site Relation."""
    records = sitetracker_soql_query_all(
        "SELECT Company__c, Company__r.Name, Company__r.Business_Registration_Number__c "
        "FROM Site_Relation__c "
        "WHERE Site_Relation_Role__c = 'INSTALLER'"
    )
    # Deduplicate by Account ID
    accounts = {}
    for rec in records:
        company_id = rec.get("Company__c")
        company_ref = rec.get("Company__r") or {}
        if company_id and company_id not in accounts:
            accounts[company_id] = {
                "Id": company_id,
                "Name": company_ref.get("Name"),
                "Business_Registration_Number__c": company_ref.get("Business_Registration_Number__c"),
            }
    return list(accounts.values())


def query_sitetracker_subcontractor_accounts():
    """Query SiteTracker for Accounts with Type = 'Sub-contractor'."""
    records = sitetracker_soql_query_all(
        "SELECT Id, Name, Business_Registration_Number__c, Type "
        "FROM Account "
        "WHERE Type = 'Sub-contractor'"
    )
    return [{"Id": r["Id"], "Name": r.get("Name"), "Business_Registration_Number__c": r.get("Business_Registration_Number__c")} for r in records]


def find_best_match(installer_name, sf_accounts):
    """Find best matching SiteTracker Account for a given installer name."""
    normalized_source = normalize_name(installer_name)
    if not normalized_source:
        return None, None, "no_name"

    best_match = None
    best_confidence = "none"

    for acct in sf_accounts:
        sf_name = acct.get("Name") or ""
        normalized_sf = normalize_name(sf_name)

        # Exact normalized match
        if normalized_source == normalized_sf:
            return acct, "exact", acct["Id"]

        # One contains the other
        if normalized_source in normalized_sf or normalized_sf in normalized_source:
            if best_confidence != "exact":
                best_match = acct
                best_confidence = "contains"

    if best_match:
        return best_match, best_confidence, best_match["Id"]
    return None, "none", None


def main():
    print("=" * 90)
    print("  SiteTracker Installer Mapping Research")
    print("=" * 90)

    # ── Step 1: Query local database ─────────────────────────────────────────
    conn_str = get_db_connection_string()
    conn = get_db_connection(conn_str)

    print("\n── Step 1: Wattif Installer values (from location_mapping, load_to_sitetracker=TRUE) ──")
    wattif_installers = query_wattif_installers(conn)
    if not wattif_installers:
        print("  WARNING: No wattif_installer values found! Column may not be populated yet.")
        print("  Falling back to Source.Locations.installer_company...")
    else:
        print(f"  Found {len(wattif_installers)} distinct installer(s):\n")
        print(f"  {'Installer Name':<45} {'Locations':<10} {'Sample Project Codes'}")
        print(f"  {'─'*45} {'─'*10} {'─'*40}")
        for name, count, samples in wattif_installers:
            print(f"  {name:<45} {count:<10} {samples[:40]}")

    print("\n── Step 2: Source.Locations installer_company (cross-reference) ──")
    source_installers = query_source_installers(conn)
    print(f"  Found {len(source_installers)} distinct installer(s) in Mer source data:\n")
    print(f"  {'Installer Company':<45} {'GUID':<40} {'Locations'}")
    print(f"  {'─'*45} {'─'*40} {'─'*10}")
    for name, guid, count in source_installers:
        print(f"  {name:<45} {(guid or '-'):<40} {count}")

    conn.close()

    # ── Step 3: Query SiteTracker ────────────────────────────────────────────
    print("\n── Step 3: Authenticating to SiteTracker sandbox... ──")
    try:
        get_sitetracker_token()
    except Exception as e:
        print(f"  ERROR: Could not authenticate to SiteTracker: {e}")
        print("  Skipping SiteTracker lookup. Fill mapping manually.")
        return

    print("\n── Step 4: SiteTracker Accounts with INSTALLER role ──")
    installer_accounts = query_sitetracker_installer_accounts()
    print(f"  Found {len(installer_accounts)} Account(s) with INSTALLER Site Relations:\n")
    print(f"  {'Account Name':<45} {'Reg No':<20} {'SF Account ID'}")
    print(f"  {'─'*45} {'─'*20} {'─'*20}")
    for acct in installer_accounts:
        print(f"  {(acct['Name'] or '-'):<45} {(acct['Business_Registration_Number__c'] or '-'):<20} {acct['Id']}")

    print("\n── Step 5: SiteTracker Sub-contractor Accounts ──")
    subcontractor_accounts = query_sitetracker_subcontractor_accounts()
    print(f"  Found {len(subcontractor_accounts)} Sub-contractor Account(s):\n")
    print(f"  {'Account Name':<45} {'Reg No':<20} {'SF Account ID'}")
    print(f"  {'─'*45} {'─'*20} {'─'*20}")
    for acct in subcontractor_accounts:
        print(f"  {(acct['Name'] or '-'):<45} {(acct['Business_Registration_Number__c'] or '-'):<20} {acct['Id']}")

    # ── Step 6: Match ────────────────────────────────────────────────────────
    # Combine all SF accounts for matching
    all_sf_accounts = {a["Id"]: a for a in installer_accounts + subcontractor_accounts}
    sf_account_list = list(all_sf_accounts.values())

    # Use wattif_installer if available, fallback to source_installers
    source_names = [name for name, _, _ in wattif_installers] if wattif_installers else [name for name, _, _ in source_installers]

    print("\n── Step 6: Matching Results ──")
    print(f"\n  {'Source Installer':<45} {'Best SF Match':<40} {'Confidence':<12} {'SF Account ID'}")
    print(f"  {'─'*45} {'─'*40} {'─'*12} {'─'*20}")

    unresolved = []
    resolved = []
    for name in source_names:
        match, confidence, sf_id = find_best_match(name, sf_account_list)
        match_name = match["Name"] if match else "-"
        sf_id_display = sf_id or "NEEDS MANUAL MAPPING"
        print(f"  {name:<45} {match_name:<40} {confidence:<12} {sf_id_display}")
        if confidence in ("none", "no_name"):
            unresolved.append(name)
        else:
            resolved.append((name, match_name, sf_id))

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*90}")
    print(f"  SUMMARY: {len(resolved)} resolved, {len(unresolved)} need manual mapping")
    print(f"{'='*90}")
    if unresolved:
        print("\n  Unresolved installers (need manual SF Account ID):")
        for name in unresolved:
            print(f"    - {name}")

    print("\n  Next steps:")
    print("    1. For unresolved entries, manually look up or create Accounts in SiteTracker")
    print("    2. Create 023_sitetracker_installer_lookup.sql with the complete mapping")
    print("    3. Re-run this script to verify all entries are resolved")


if __name__ == "__main__":
    main()
