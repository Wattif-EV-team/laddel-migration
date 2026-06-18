import pyodbc
import requests
import json
import logging
from datetime import datetime
from utils.config_utils import get_db_connection_string, get_api_base_url, get_api_token
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import get_value, get_db_connection, get_sql_dialect, quote_identifier

# Initialize counters and error list
total_rows = 0
created_count = 0
updated_count = 0
error_count = 0
errors = []

def fetch_partner_contracts(conn_str):
    """Fetch all partner contracts from the database."""
    with get_db_connection(conn_str) as conn:
        with conn.cursor() as cursor:
            dialect = get_sql_dialect(conn)
            target_schema = quote_identifier("Target", dialect)
            table = quote_identifier("PartnerContracts", dialect)
            target_partner_id = quote_identifier("TargetPartnerID", dialect)
            query = f"SELECT * FROM {target_schema}.{table} WHERE {target_partner_id} IS NOT NULL"
            cursor.execute(query)
            return cursor.fetchall()

def create_partner_contract(contract_data):
    """Create a partner contract in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/partner-contracts/v1.0"
        return requests.post(url, headers=headers, json=contract_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_partner_contract')

def update_partner_contract(contract_id, contract_data):
    """Update a partner contract in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/partner-contracts/v1.0/{contract_id}"
        return requests.put(url, headers=headers, json=contract_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_partner_contract')

def update_partner_contract_mapping(conn_str, row, contract_id):
    """
    Update the appropriate mapping table with the new PartnerContractID.
    Supports multiple source systems based on available source identifiers.
    """
    try:
        with get_db_connection(conn_str) as conn:
            with conn.cursor() as cursor:
                query = None
                params = None
                success_msg = None

                # Generic mapping_table/mapping_key pattern (key-based migrations)
                if hasattr(row, "mapping_table") and row.mapping_table:
                    table_name = row.mapping_table  # e.g., "location_mapping"
                    query = f'''
                        UPDATE "Mapping"."{table_name}"
                        SET target_partner_contract_id = ?
                        WHERE mapping_key = ?
                    '''
                    params = (contract_id, row.mapping_key)
                    success_msg = f"Updated {table_name} for mapping_key: {row.mapping_key} with target_partner_contract_id {contract_id}"
                # Choose exactly one update based on available attributes
                elif hasattr(row, "SourceStationID"):
                    query = (
                        """
                        UPDATE [Mapping].[StationMapping]
                        SET TargetPartnerContractID = ?
                        WHERE SourceStationID = ?
                        """
                    )
                    params = (contract_id, row.SourceStationID)
                    success_msg = (
                        f"Updated StationMapping with SourceStationID: {row.SourceStationID} for TargetPartnerContractID {contract_id}"
                    )
                elif hasattr(row, "SourceLocationID") and hasattr(row, "SourceAccountID"):
                    # Charge365 - PostgreSQL
                    query = (
                        """
                        UPDATE "Mapping"."LocationMapping"
                        SET "TargetPartnerContractID" = ?
                        WHERE "SourceLocationID" = ?
                        """
                    )
                    params = (contract_id, row.SourceLocationID)
                    success_msg = (
                        f"Updated LocationMapping (PG) with SourceLocationID: {row.SourceLocationID} for TargetPartnerContractID {contract_id}"
                    )
                elif hasattr(row, "SourceLocationID"):
                    # MSSQL path
                    query = (
                        """
                        UPDATE [Mapping].[LocationMapping]
                        SET TargetPartnerContractID = ?
                        WHERE SourceLocationID = ?
                        """
                    )
                    params = (contract_id, row.SourceLocationID)
                    success_msg = (
                        f"Updated LocationMapping with SourceLocationID: {row.SourceLocationID} for TargetPartnerContractID {contract_id}"
                    )

                if query is None or params is None:
                    raise SystemExit("No valid mapping condition found; query not built. Halting execution.")

                cursor.execute(query, params)
                if cursor.rowcount == 0:
                    raise SystemExit("Update affected 0 rows; halting execution to prevent missing mapping.")
                logging.info(success_msg)
                conn.commit()
    except Exception as e:
        logging.error(
            f"Failed to update mapping tables for row ({row}) with PartnerContractID: {contract_id}. Error: {e}"
        )
        raise SystemExit(f"Halting execution due to error: {e}")

def process_contract_row(conn_str, row):
    """Processes each partner contract row."""
    global created_count, updated_count, error_count
    contract_id = row.TargetPartnerContractID
    formatted_start_date = f"{row.startDate}T00:00:00Z"
    formatted_end_date = f"{row.endDate}T00:00:00Z" if row.endDate else None
    contract_data = {
        "title": get_value(row.title, str),
        "contractType": get_value(row.contractType, str),
        "partnerId": get_value(row.partnerId, int),
        "startDate": formatted_start_date,
        "endDate": formatted_end_date,
        "autoRenewal": get_value(row.autoRenewal, bool),
        "accessAndPermissions": {
            "sessionsRemoteControl": get_value(row.accessAndPermissions_sessionsRemoteControl, bool),
            "startReservation": get_value(row.accessAndPermissions_startReservation, bool),
            "stopReservation": get_value(row.accessAndPermissions_stopReservation, bool),
            "resetChargePoint": get_value(row.accessAndPermissions_resetChargePoint, bool),
            "firmwareUpdate": get_value(row.accessAndPermissions_firmwareUpdate, bool)
        },
        "revenueSharing": {
            "partnerSharePercentageAcEvse": get_value(row.revenueSharing_partnerSharePercentageAcEvse, float),
            "partnerSharePercentageDcEvse": get_value(row.revenueSharing_partnerSharePercentageDcEvse, float),
            "excludeConnectionFee": get_value(row.revenueSharing_excludeConnectionFee, bool),
            "deductElectricityCost": get_value(row.revenueSharing_deductElectricityCost, bool),
            "reimburseForElectricityCost": get_value(row.revenueSharing_reimburseForElectricityCost, bool),
            "fixedFeePerSessionAc": get_value(row.revenueSharing_fixedFeePerSessionAc, float),
            "fixedFeePerSessionDc": get_value(row.revenueSharing_fixedFeePerSessionDc, float),
            "feePerKwhAc": get_value(row.revenueSharing_feePerKwhAc, float),
            "feePerKwhDc": get_value(row.revenueSharing_feePerKwhDc, float),
            "handlingFee": get_value(row.revenueSharing_handlingFee, float)
        },
        "monthlyPlatformFees": {
            "perChargePoint": get_value(row.monthlyPlatformFees_perChargePoint, float),
            "perAcEvse": get_value(row.monthlyPlatformFees_perAcEvse, float),
            "perDcEvse": get_value(row.monthlyPlatformFees_perDcEvse, float)
        }
    }

    try:
        if contract_id is None:
            # Create a new Partner Contract
            response_data = create_partner_contract(contract_data)
            contract_id = response_data['id']
            created_count += 1
            logging.info(f"Created partner contract '{row.title}' with id={contract_id} (partnerId={contract_data['partnerId']})")
            # Update mapping table with new PartnerContractID
            update_partner_contract_mapping(conn_str, row, contract_id)
        else:
            # Update the existing Partner Contract
            update_partner_contract(contract_id, contract_data)
            updated_count += 1
            logging.info(f"Updated partner contract '{row.title}' (id={contract_id})")
    except Exception as e:
        error_count += 1
        errors.append((row, str(e)))
        logging.error(f"Error processing row {row}: {e}")

def main():
    """Main function to fetch and process partner contracts."""
    global total_rows
    conn_str = get_db_connection_string()
    partner_contracts = fetch_partner_contracts(conn_str)
    total_rows = len(partner_contracts)
    logging.info(f"Total partner contracts fetched: {total_rows}")

    for index, row in enumerate(partner_contracts, start=1):
        logging.info(f"Processing Partner Contract {index} of {total_rows}...")
        process_contract_row(conn_str, row)

    # Summarize results
    logging.info(f"Total rows processed: {total_rows}")
    logging.info(f"Partner contracts created: {created_count}")
    logging.info(f"Partner contracts updated: {updated_count}")
    logging.info(f"Errors encountered: {error_count}")

    if errors:
        logging.info("Rows causing errors and their exceptions:")
        for error_row, error_msg in errors:
            logging.info(f"Row: {error_row}, Error: {error_msg}")

if __name__ == "__main__":
    setup_logging("CreateOrUpdatePartnerContract")
    try:
        main()
    except Exception as e:
        logging.error(f"An error occurred: {e}")
