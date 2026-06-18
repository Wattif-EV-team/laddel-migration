"""
CreateOrUpdateSiteTrackerSiteRelations.py
==========================================
Phase 4: Create or update Site Relations in SiteTracker (Salesforce).

Reads from Target.SiteTrackerSiteRelations view (which resolves mapping via JOIN
and filters out rows with unresolved dependencies) and:
  - If TargetSfSiteRelationId is not NULL → update existing relation
  - If TargetSfSiteRelationId is NULL → SOQL lookup by site + role (conflict detection for OWNER)
  - On create: writes mapping via INSERT + rowcount check (SystemExit on failure)

Prerequisites:
    - Phase 2 (Sites) must have run: sitetracker_site_mapping populated
    - Phase 3 (Accounts) must have run: sitetracker_account_mapping populated
    - The view filters out rows where either site_sf_id or company_sf_id is NULL

Usage:
    .venv\\Scripts\\python.exe CreateOrUpdateSiteTrackerSiteRelations.py
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
    find_site_relations_by_role,
    sitetracker_create,
    sitetracker_update,
    snapshot_record,
)

load_dotenv()

# Constants
SOBJECT = "Site_Relation__c"

# Counters
total_rows = 0
created_count = 0
updated_count = 0
conflict_count = 0
error_count = 0
errors = []


def fetch_target_relations(conn_str):
    """Fetch all rows from Target.SiteTrackerSiteRelations."""
    with get_db_connection(conn_str) as conn:
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM "Target"."SiteTrackerSiteRelations"')
            rows = cursor.fetchall()
            if not rows:
                raise SystemExit("Target.SiteTrackerSiteRelations returned 0 rows — aborting to prevent issues.")
            return rows


def update_relation_mapping(conn_str, row, sf_relation_id, existed_before, snapshot):
    """Write the SF Site Relation ID into the mapping table. Hard abort if write fails."""
    try:
        with get_db_connection(conn_str) as conn:
            with conn.cursor() as cursor:
                query = '''
                    INSERT INTO "Mapping"."sitetracker_site_relation_mapping"
                        (mapping_key, target_sf_site_relation_id,
                         relation_existed_before_migration, previous_record_snapshot)
                    VALUES (?, ?, ?, ?)
                '''
                params = (
                    row.mapping_key,
                    sf_relation_id,
                    existed_before,
                    json.dumps(snapshot) if snapshot else None,
                )
                cursor.execute(query, params)
                if cursor.rowcount == 0:
                    raise SystemExit(
                        f"Mapping INSERT affected 0 rows for {row.project_code}|{row.role}; "
                        f"halting to prevent orphaned Site Relation in SiteTracker."
                    )
                conn.commit()
                logging.info(
                    f"    Mapped {row.project_code}|{row.role} → {sf_relation_id} "
                    f"(existed_before={existed_before})"
                )
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(
            f"Mapping write failed for {row.project_code}|{row.role} "
            f"(sf_id={sf_relation_id}): {e} — halting to prevent orphaned resources."
        )


def build_relation_payload(row):
    """Build the Salesforce Site_Relation__c payload from a target view row."""
    payload = {
        "Site__c": row.site_sf_id,
        "Company__c": row.company_sf_id,
        "Site_Relation_Role__c": row.role,
        "previous_CPO__c": row.previous_cpo,
    }
    if row.relation_start_date:
        payload["Site_Relation_Start_Date__c"] = str(row.relation_start_date)

    return {k: v for k, v in payload.items() if v is not None}


def process_relation(conn_str, row):
    """Process a single site relation row: create or update in SiteTracker."""
    global created_count, updated_count, conflict_count, error_count

    project_code = row.project_code
    role = row.role
    sf_id = row.TargetSfSiteRelationId

    try:
        if sf_id is not None:
            # Already mapped → update
            payload = build_relation_payload(row)
            sitetracker_update(SOBJECT, sf_id, payload)
            updated_count += 1
            logging.info(f"  UPDATED {project_code}|{role} → {sf_id}")
        else:
            # Not mapped — SOQL lookup for existing relation with same role on this site
            existing_relations = find_site_relations_by_role(row.site_sf_id, role)

            if existing_relations:
                # Check if any existing relation points to the SAME company
                for existing in existing_relations:
                    if existing.get("Company__c") == row.company_sf_id:
                        # Same company → update it
                        sf_id = existing["Id"]
                        before = snapshot_record(SOBJECT, sf_id)
                        payload = build_relation_payload(row)
                        sitetracker_update(SOBJECT, sf_id, payload)
                        update_relation_mapping(conn_str, row, sf_id, True, before)
                        updated_count += 1
                        logging.info(f"  UPDATED (pre-existing) {project_code}|{role} → {sf_id}")
                        return

                # Different company — conflict!
                if role == "OWNER of SITE":
                    # Do NOT overwrite existing OWNER — log warning
                    existing_company = existing_relations[0].get("Company__r", {}).get("Name", "?")
                    conflict_count += 1
                    logging.warning(
                        f"  CONFLICT {project_code}|{role}: existing OWNER is '{existing_company}' "
                        f"(SF ID: {existing_relations[0]['Company__c']}), NOT overwriting"
                    )
                    # Still record existing relation in mapping for tracking
                    sf_id = existing_relations[0]["Id"]
                    update_relation_mapping(conn_str, row, sf_id, True, None)
                    return
                # For INSTALLER: multiple installers may be valid → fall through to create

            # Create new relation
            payload = build_relation_payload(row)
            result = sitetracker_create(SOBJECT, payload)
            sf_id = result["id"]

            # Write mapping (hard abort if this fails)
            update_relation_mapping(conn_str, row, sf_id, False, None)
            created_count += 1
            logging.info(f"  CREATED {project_code}|{role} → {sf_id}")

    except SystemExit:
        raise  # Let mapping failures propagate as hard aborts
    except Exception as e:
        error_count += 1
        errors.append(f"{project_code}|{role}: {e}")
        logging.error(f"  ERROR processing {project_code}|{role}: {e}")


def main():
    """Main entry point."""
    global total_rows

    conn_str = get_db_connection_string()

    logging.info("=" * 70)
    logging.info("SiteTracker Site Relations — Create or Update")
    logging.info("=" * 70)

    # Authenticate
    get_sitetracker_token()

    # Fetch target data (aborts if view is empty or missing)
    rows = fetch_target_relations(conn_str)
    total_rows = len(rows)
    logging.info(f"Found {total_rows} site relations to process")

    # Process OWNER relations first, then INSTALLER (order matters for conflict detection)
    owner_rows = [r for r in rows if r.role == "OWNER of SITE"]
    installer_rows = [r for r in rows if r.role == "INSTALLER"]

    logging.info(f"  OWNER relations: {len(owner_rows)}")
    logging.info(f"  INSTALLER relations: {len(installer_rows)}")

    logging.info("")
    logging.info("── Processing OWNER of SITE relations ──")
    for i, row in enumerate(owner_rows, 1):
        if i % 50 == 0:
            logging.info(f"  Progress: {i}/{len(owner_rows)}")
        process_relation(conn_str, row)

    logging.info("")
    logging.info("── Processing INSTALLER relations ──")
    for i, row in enumerate(installer_rows, 1):
        if i % 50 == 0:
            logging.info(f"  Progress: {i}/{len(installer_rows)}")
        process_relation(conn_str, row)

    # Summary
    logging.info("")
    logging.info("=" * 70)
    logging.info(f"COMPLETE: {total_rows} relations processed")
    logging.info(f"  Created:   {created_count}")
    logging.info(f"  Updated:   {updated_count}")
    logging.info(f"  Conflicts: {conflict_count}")
    logging.info(f"  Errors:    {error_count}")
    if errors:
        logging.info("  Error details:")
        for err in errors:
            logging.error(f"    {err}")
    logging.info("=" * 70)

    if error_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    setup_logging("CreateOrUpdateSiteTrackerSiteRelations")
    try:
        main()
    except SystemExit as e:
        logging.critical(f"ABORT: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        sys.exit(1)
