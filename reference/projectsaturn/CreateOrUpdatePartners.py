import pyodbc
import requests
import json
import logging
from utils.config_utils import get_db_connection_string
from utils.dbutils import get_value, try_get_value, get_db_connection, get_sql_dialect, quote_identifier
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response

# Initialize counters and error list
total_rows = 0
created_count = 0
updated_count = 0
error_count = 0
errors = []

def create_partner(partner_data):
    """Create a partner in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/partners/v2.0"
        return requests.post(url, headers=headers, json=partner_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_partner')

def update_partner(partner_id, partner_data):
    """Update a partner in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/partners/v2.0/{partner_id}"
        return requests.patch(url, headers=headers, json=partner_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_partner')

def update_partner_mapping(conn_str, row, partner_id, project_code=None):
    """Insert or update the appropriate mapping table with the new partner ID.
    
    Uses ON CONFLICT upsert so reruns are idempotent.
    """
    try:
        with get_db_connection(conn_str) as conn:
            with conn.cursor() as cursor:
                query = None
                params = None
                success_msg = None

                # Generic mapping_table/mapping_key pattern (key-based migrations)
                if hasattr(row, "mapping_table") and row.mapping_table:
                    table_name = row.mapping_table  # e.g., "project_code_mapping" or "billing_partner_mapping"
                    if project_code is not None:
                        # Mapping tables with a project_code column (e.g., project_code_mapping)
                        query = f'''
                            INSERT INTO "Mapping"."{table_name}" (mapping_key, project_code, target_partner_id)
                            VALUES (?, ?, ?)
                            ON CONFLICT (mapping_key) DO UPDATE SET target_partner_id = EXCLUDED.target_partner_id
                        '''
                        params = (row.mapping_key, project_code, partner_id)
                    else:
                        # Mapping tables without project_code (e.g., billing_partner_mapping)
                        query = f'''
                            INSERT INTO "Mapping"."{table_name}" (mapping_key, target_partner_id)
                            VALUES (?, ?)
                            ON CONFLICT (mapping_key) DO UPDATE SET target_partner_id = EXCLUDED.target_partner_id
                        '''
                        params = (row.mapping_key, partner_id)
                    success_msg = f"Upserted {table_name} target_partner_id for mapping_key: {row.mapping_key} with PartnerID: {partner_id}"
                # Insert into ProjectCodeMapping for SourceAccountID (System=Charge365 - PostgreSQL)
                elif project_code is not None and hasattr(row, "SourceAccountID"):
                    query = (
                        """
                        INSERT INTO "Mapping"."ProjectCodeMapping" ("ProjectCode", "SourceAccountID", "TargetPartnerID")
                        VALUES (?, ?, ?)
                        """
                    )
                    params = (project_code, row.SourceAccountID, partner_id)
                    success_msg = (
                        f"Inserted TargetPartnerID for ProjectCode: {project_code} with PartnerID: {partner_id} (SourceAccountID)."
                    )
                # Insert into ProjectCodeMapping for SourceCompanyID (System=Current - MSSQL)
                elif project_code is not None and hasattr(row, "SourceCompanyId"):
                    query = (
                        """
                        INSERT INTO [Mapping].[ProjectCodeMapping] (ProjectCode, SourceCompanyID, TargetPartnerID)
                        VALUES (?, ?, ?)
                        """
                    )
                    params = (project_code, row.SourceCompanyId, partner_id)
                    success_msg = (
                        f"Inserted TargetPartnerID for ProjectCode: {project_code} with PartnerID: {partner_id} (SourceCompanyID)."
                    )
                # Insert into ProjectCodeMapping for SourceOrganizationID (System=EV-Advisor - MSSQL)
                elif project_code is not None and hasattr(row, "SourceOrganizationId"):
                    query = (
                        """
                        INSERT INTO [Mapping].[ProjectCodeMapping] (ProjectCode, SourceOrganizationID, TargetPartnerID)
                        VALUES (?, ?, ?)
                        """
                    )
                    params = (project_code, row.SourceOrganizationId, partner_id)
                    success_msg = (
                        f"Inserted TargetPartnerID for ProjectCode: {project_code} with PartnerID: {partner_id} (SourceOrganizationID)."
                    )
                # Insert into CompanyMapping for SourceCompanyId (System=Current - MSSQL)
                elif project_code is None and hasattr(row, "SourceCompanyId"):
                    query = (
                        """
                        INSERT INTO [Mapping].[CompanyMapping] (SourceCompanyId, TargetPartnerId)
                        VALUES (?, ?)
                        """
                    )
                    params = (row.SourceCompanyId, partner_id)
                    success_msg = (
                        f"Inserted CompanyMapping for SourceCompanyId: {row.SourceCompanyId} with PartnerID: {partner_id}."
                    )

                if query is None or params is None:
                    raise SystemExit("No valid mapping condition found; query not built. Halting execution.")

                cursor.execute(query, params)
                if cursor.rowcount == 0:
                    raise SystemExit("Insert affected 0 rows; halting execution to prevent missing mapping.")
                logging.info(success_msg)
                conn.commit()
    except Exception as e:
        raise SystemExit(f"SQL execution failed: {e}")

def process_partner_row(conn_str, row):
    """Processes each row from the query."""
    global created_count, updated_count, error_count
    partner_id = row.TargetPartnerID
    project_code = getattr(row, "ProjectCode", None)  # Handle rows without ProjectCode
    # TODO: API property 'state' is explicitly excluded — not relevant for our markets (NO/SE/GB).
    #       Do not re-add without confirming country-specific state/province requirements.
    partner_data = {
        "businessName": get_value(row.businessName, str),
        "name": get_value(row.name, str),
        "regNo": get_value(row.regNo, str),
        "vatNo": get_value(row.vatNo, str),
        "address": get_value(row.address, str),
        "postcode": get_value(row.postcode, str),
        "city": get_value(row.city, str),
        "country": get_value(row.country, str),
        "region": get_value(row.region, str),
        "contactDetails": {
            "administrative": {
                "contactPerson": get_value(row.contactDetails_administrative_contactPerson, str),
                "email": get_value(row.contactDetails_administrative_email, str),
                "phone": get_value(row.contactDetails_administrative_phone, str)
            },
            "technical": {
                "contactPerson": get_value(row.contactDetails_technical_contactPerson, str),
                "email": get_value(row.contactDetails_technical_email, str),
                "phone": get_value(row.contactDetails_technical_phone, str)
            },
            "billing": {
                "contactPerson": get_value(row.contactDetails_billing_contactPerson, str),
                "email": get_value(row.contactDetails_billing_email, str),
                "phone": get_value(row.contactDetails_billing_phone, str)
            }
        },
        "notifications": {
            "technical": {
                "chargePointFaults": get_value(row.notifications_technical_chargePointFaults, bool, default=False)
            },
            "billing": {
                "settlementReports": get_value(row.notifications_billing_settlementReports, bool, default=False)
            }
        },
        "monthlyPlatformFee": get_value(row.monthlyPlatformFee, float),
        "options": {
            "allowViewingUsersWhoAcceptedInvite": try_get_value(row, "options_allowViewingUsersWhoAcceptedInvite", bool, default=False),
            "createUsers": get_value(row.options_createUsers, bool, default=False),
            "addUserBalance": get_value(row.options_addUserBalance, bool, default=False),
            "allowViewingAllSessionsOfInvitedUsers": try_get_value(row, "options_allowViewingAllSessionsOfInvitedUsers", bool, default=False),
            "supplierOnReceipts": get_value(row.options_supplierOnReceipts, bool, default=False),
            "allowToControlTariffs": get_value(row.options_allowToControlTariffs, bool, default=False),
            "allowToControlTariffGroups": get_value(row.options_allowToControlTariffGroups, bool, default=False)
        },
        "corporateBilling": {
            "enabled": get_value(row.corporateBilling_enabled, bool, default=False),
            "monthlyLimit": get_value(row.corporateBilling_monthlyLimit, float),
            "discount": get_value(row.corporateBilling_discount, float)
        },
        "externalId": get_value(row.externalId, str),
        "bankDetails": {
            "bankIban": get_value(row.bankDetails_bankIban, str),
            "bankName": get_value(row.bankDetails_bankName, str),
            "bankAddress": get_value(row.bankDetails_bankAddress, str),
            "bankCode": get_value(row.bankDetails_bankCode, str),
            "bankAccountNumber": get_value(row.bankDetails_bankAccountNumber, str),
            "bankAccountType": get_value(row.bankDetails_bankAccountType, str)
        }
    }

    try:
        if partner_id is None:
            logging.info(f"Creating partner: {row.businessName}")
            # Create a new Partner
            response_data = create_partner(partner_data)
            partner_id = response_data['id']
            created_count += 1
            logging.info(f"Created partner '{row.businessName}' with id={partner_id}")
            # Update mapping table with new PartnerID
            update_partner_mapping(conn_str, row, partner_id, project_code)
        else:
            logging.info(f"Updating partner '{row.businessName}' (id={partner_id})")
            # Update the existing Partner
            update_partner(partner_id, partner_data)
            updated_count += 1
    except Exception as e:
        error_count += 1
        errors.append((row, str(e)))
        logging.error(f"Error processing row {row}: {e}")

def fetch_partners(conn_str):
    """Fetch all partners from the database."""
    with get_db_connection(conn_str) as conn:
        with conn.cursor() as cursor:
            dialect = get_sql_dialect(conn)
            target_schema = quote_identifier("Target", dialect)
            table = quote_identifier("Partners", dialect)
            query = f"SELECT * FROM {target_schema}.{table}"
            cursor.execute(query)
            return cursor.fetchall()

def main():
    """Main function to query the database and process each partner."""
    global total_rows
    conn_str = get_db_connection_string()

    # Process all partners
    partners = fetch_partners(conn_str)
    total_partners = len(partners)
    for idx, row in enumerate(partners, start=1):
        logging.info(f"Processing Partner {idx} of {total_partners}...")
        total_rows += 1
        process_partner_row(conn_str, row)

    # Summarize results
    logging.info(f"Total rows processed: {total_rows}")
    logging.info(f"Partners created: {created_count}")
    logging.info(f"Partners updated: {updated_count}")
    logging.info(f"Errors encountered: {error_count}")

    if errors:
        logging.info("Rows causing errors and their exceptions:")
        for error_row, error_msg in errors:
            logging.info(f"Row: {error_row}, Error: {error_msg}")

if __name__ == "__main__":
    setup_logging("CreateOrUpdatePartners")
    try:
        main()
    except Exception as e:
        logging.error(f"An error occurred: {e}")
