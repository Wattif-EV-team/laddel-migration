"""
CreateOrUpdateSiteTrackerSites.py
=================================
Phase 2: Create or update Sites in SiteTracker (Salesforce).

Reads from Target.SiteTrackerSites view (which resolves mapping via JOIN) and:
  - If TargetSfSiteId is not NULL → update existing Site
  - If TargetSfSiteId is NULL → SOQL lookup by Site_ID__c, then name-collision check, then create
  - On create: writes mapping via INSERT + rowcount check (SystemExit on failure)

Usage:
    .venv\\Scripts\\python.exe CreateOrUpdateSiteTrackerSites.py
"""

import sys
import json
import logging

from dotenv import load_dotenv

from utils.log_utils import setup_logging
from utils.config_utils import get_db_connection_string
from utils.dbutils import get_db_connection
from utils.sitetracker_utils import (
    get_sitetracker_token,
    find_site_by_project_code,
    find_site_by_name,
    sitetracker_create,
    sitetracker_update,
    snapshot_record,
    log_field_diffs,
)

load_dotenv()

# Constants
SOBJECT = "sitetracker__Site__c"

# Counters
total_rows = 0
created_count = 0
updated_count = 0
error_count = 0
errors = []


def fetch_target_sites(conn_str):
    """Fetch all rows from Target.SiteTrackerSites."""
    with get_db_connection(conn_str) as conn:
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM "Target"."SiteTrackerSites"')
            rows = cursor.fetchall()
            if not rows:
                raise SystemExit("Target.SiteTrackerSites returned 0 rows — aborting to prevent issues.")
            return rows


def update_site_mapping(conn_str, row, sf_site_id, existed_before, snapshot):
    """Write the SF Site ID into the mapping table. Hard abort if write fails."""
    try:
        with get_db_connection(conn_str) as conn:
            with conn.cursor() as cursor:
                query = '''
                    INSERT INTO "Mapping"."sitetracker_site_mapping"
                        (mapping_key, project_code, target_sf_site_id,
                         site_existed_before_migration, previous_record_snapshot)
                    VALUES (?, ?, ?, ?, ?)
                '''
                params = (
                    row.mapping_key,
                    row.project_code,
                    sf_site_id,
                    existed_before,
                    json.dumps(snapshot) if snapshot else None,
                )
                cursor.execute(query, params)
                if cursor.rowcount == 0:
                    raise SystemExit(
                        f"Mapping INSERT affected 0 rows for {row.project_code}; "
                        f"halting to prevent orphaned Site in SiteTracker."
                    )
                conn.commit()
                logging.info(
                    f"    Mapped {row.project_code} → {sf_site_id} "
                    f"(existed_before={existed_before})"
                )
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(
            f"Mapping write failed for {row.project_code} (sf_id={sf_site_id}): {e} — "
            f"halting to prevent orphaned resources."
        )


def build_site_payload(row):
    """Build the Salesforce payload from a target view row."""
    payload = {
        "Site_ID__c": row.Site_ID__c,
        "Name": row.Name,
        "sitetracker__Site_Status__c": row.sitetracker__Site_Status__c,
        "sitetracker__Site_Type__c": row.sitetracker__Site_Type__c,
        "sitetracker__Street_Address__c": row.sitetracker__Street_Address__c,
        "sitetracker__City__c": row.sitetracker__City__c,
        "sitetracker__Zip_Code__c": row.sitetracker__Zip_Code__c,
        "Country__c": row.Country__c,
        "Owner_Type__c": row.Owner_Type__c,
        "Load_Management__c": row.Load_Management__c,
    }

    # Coordinates
    if row.sitetracker__Location__Latitude__s is not None:
        payload["sitetracker__Location__Latitude__s"] = float(row.sitetracker__Location__Latitude__s)
    if row.sitetracker__Location__Longitude__s is not None:
        payload["sitetracker__Location__Longitude__s"] = float(row.sitetracker__Location__Longitude__s)

    # Multi-picklist fields
    if row.EV_Connector_Type__c:
        payload["EV_Connector_Type__c"] = row.EV_Connector_Type__c
    if row.EV_Charging_Level__c:
        payload["EV_Charging_Level__c"] = row.EV_Charging_Level__c

    # Date fields
    if row.Open_Date__c:
        payload["Open_Date__c"] = str(row.Open_Date__c)
    if row.Installed_Date__c:
        payload["Installed_Date__c"] = str(row.Installed_Date__c)

    return {k: v for k, v in payload.items() if v is not None}


def process_site(conn_str, row):
    """Process a single site row: create or update in SiteTracker."""
    global created_count, updated_count, error_count

    project_code = row.project_code
    sf_id = row.TargetSfSiteId

    try:
        if sf_id is not None:
            # Already mapped → snapshot + diff + update
            before = snapshot_record(SOBJECT, sf_id)
            payload = build_site_payload(row)
            payload.pop("Site_ID__c", None)  # Don't update the lookup key
            log_field_diffs(project_code, before, payload)
            sitetracker_update(SOBJECT, sf_id, payload)
            updated_count += 1
            logging.info(f"  UPDATED {project_code} → {sf_id}")
        else:
            # Not mapped — SOQL lookup by project_code (Site_ID__c)
            existing_record = find_site_by_project_code(project_code)

            if existing_record:
                # Found in SiteTracker but not in our mapping
                sf_id = existing_record["Id"]
                logging.warning(f"  Site already exists in SiteTracker: {project_code} → {sf_id}")

                # Guard: skip update if names don't match (likely project code reuse)
                existing_name = (existing_record.get("Name") or "").strip().lower()
                our_name = (row.Name or "").strip().lower()
                if existing_name and our_name and existing_name != our_name:
                    error_count += 1
                    errors.append(
                        f"{project_code}: Name mismatch — SiteTracker has '{existing_record.get('Name')}' "
                        f"but we want '{row.Name}'. Likely project code reuse — requires manual intervention."
                    )
                    logging.error(
                        f"  ERROR {project_code}: Name mismatch — SiteTracker='{existing_record.get('Name')}', "
                        f"Ours='{row.Name}'. Skipping update to avoid overwriting unrelated site."
                    )
                    return

                before = snapshot_record(SOBJECT, sf_id)
                payload = build_site_payload(row)
                payload.pop("Site_ID__c", None)
                log_field_diffs(project_code, before, payload)
                sitetracker_update(SOBJECT, sf_id, payload)

                # Write mapping (hard abort if fails)
                update_site_mapping(conn_str, row, sf_id, True, before)
                updated_count += 1
                logging.info(f"  UPDATED (pre-existing) {project_code} → {sf_id}")
                return

            # Name-collision check: another site with same Name but different Site_ID__c
            name_match = find_site_by_name(row.Name)
            if name_match:
                existing_site_id_c = name_match.get("Site_ID__c", "")
                error_count += 1
                errors.append(
                    f"{project_code}: Name '{row.Name}' already exists as {name_match['Id']} "
                    f"with Site_ID__c='{existing_site_id_c}' — manual resolution required"
                )
                logging.error(
                    f"  ERROR {project_code}: Name '{row.Name}' already in SiteTracker "
                    f"(Id={name_match['Id']}, Site_ID__c='{existing_site_id_c}'). "
                    f"Likely from earlier incomplete migration — requires manual correction."
                )
                return

            # Create new site
            payload = build_site_payload(row)
            result = sitetracker_create(SOBJECT, payload)
            sf_id = result["id"]

            # Write mapping (hard abort if this fails)
            update_site_mapping(conn_str, row, sf_id, False, None)
            created_count += 1
            logging.info(f"  CREATED {project_code} → {sf_id}")

    except SystemExit:
        raise  # Let mapping failures propagate as hard aborts
    except Exception as e:
        error_count += 1
        errors.append(f"{project_code}: {e}")
        logging.error(f"  ERROR processing {project_code}: {e}")


def main():
    """Main entry point."""
    global total_rows

    conn_str = get_db_connection_string()

    logging.info("=" * 70)
    logging.info("SiteTracker Sites — Create or Update")
    logging.info("=" * 70)

    # Authenticate
    get_sitetracker_token()

    # Fetch target data (aborts if view is empty or missing)
    rows = fetch_target_sites(conn_str)
    total_rows = len(rows)
    logging.info(f"Found {total_rows} sites to process")

    # Process each site
    for i, row in enumerate(rows, 1):
        if i % 50 == 0:
            logging.info(f"Progress: {i}/{total_rows}")
        process_site(conn_str, row)

    # Summary
    logging.info("")
    logging.info("=" * 70)
    logging.info(f"COMPLETE: {total_rows} sites processed")
    logging.info(f"  Created: {created_count}")
    logging.info(f"  Updated: {updated_count}")
    logging.info(f"  Errors:  {error_count}")
    if errors:
        logging.info("  Error details:")
        for err in errors:
            logging.error(f"    {err}")
    logging.info("=" * 70)

    if error_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    setup_logging("CreateOrUpdateSiteTrackerSites")
    try:
        main()
    except SystemExit as e:
        logging.critical(f"ABORT: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        sys.exit(1)
