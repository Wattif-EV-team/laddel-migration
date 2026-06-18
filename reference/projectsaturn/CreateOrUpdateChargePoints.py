import pyodbc
import requests
import json
import logging
from typing import cast
from utils.config_utils import get_db_connection_string, get_api_base_url, get_api_token
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import get_db_connection, get_sql_dialect, quote_identifier, get_value

# Initialize counters and error list
total_rows = 0
created_count = 0
updated_count = 0
error_count = 0
errors = []

# Using shared get_value from utils.dbutils

def fetch_charge_points(conn_str):
    """Fetch all charge points from the database."""
    with get_db_connection(conn_str) as conn:
        cursor = conn.cursor()
        dialect = get_sql_dialect(conn)
        target_schema = quote_identifier("Target", dialect)
        table = quote_identifier("ChargePoints", dialect)
        target_partner_contract_id = quote_identifier("TargetPartnerContractID", dialect)
        target_location_id = quote_identifier("TargetLocationID", dialect)
        query = (
            f"SELECT * FROM {target_schema}.{table} "
            f"WHERE {target_partner_contract_id} IS NOT NULL AND {target_location_id} IS NOT NULL"
        )
        cursor.execute(query)
        return cursor.fetchall()

def create_charge_point(charge_point_data):
    """Create a charge point in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/charge-points/v2.0"
        return requests.post(url, headers=headers, json=charge_point_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_charge_point')

def update_charge_point(charge_point_id, charge_point_data):
    """Update a charge point in Ampeco API."""
    # Output charge_point_data as pretty JSON
    # print("Updating charge point with data:")
    # print(json.dumps(charge_point_data, indent=2))
    def api_call(base_url, headers):
        url = f"{base_url}/resources/charge-points/v2.0/{charge_point_id}"
        return requests.patch(url, headers=headers, json=charge_point_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_charge_point')

def fetch_charge_point_by_network_id(network_id):
    """Fetch a charge point by networkId from Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/charge-points/v2.0?filter[networkId]={network_id}"
        return requests.get(url, headers=headers)

    response = execute_ampeco_api_call(api_call)
    if response.status_code == 200:
        data = response.json().get("data", [])
        if data:
            return data[0]  # Return the first matching charge point
    return None


def enable_smart_charging(charge_point_id, smart_charging_data):
    """Enable Smart Charging (DLM) for a ChargePoint.
    
    Follows the quirk pattern: if first POST returns 201 but mode is not 'dynamic',
    retry once. This handles cases where the API returns 'disabled' mode on first call.
    
    Args:
        charge_point_id: The ChargePoint ID to configure
        smart_charging_data: Dict with smart charging settings (mode, electricalConfiguration, etc.)
    
    Returns:
        Response data from API on success
        
    Raises:
        Exception if smart charging cannot be enabled after retry
    """
    def api_call(base_url, headers):
        url = f"{base_url}/resources/charge-points/v2.0/{charge_point_id}/smart-charging"
        return requests.post(url, headers=headers, json=smart_charging_data)

    response = execute_ampeco_api_call(api_call)
    
    if response.status_code == 201:
        response_data = response.json().get('data', {})
        actual_mode = response_data.get('mode')
        
        # Quirk: If mode is not 'dynamic', retry once
        if actual_mode != 'dynamic':
            logging.warning(
                f"Smart charging for CP {charge_point_id} returned mode='{actual_mode}', "
                f"expected 'dynamic'. Retrying..."
            )
            response = execute_ampeco_api_call(api_call)
            
            if response.status_code != 201:
                raise Exception(
                    f"Failed to enable Smart Charging (retry). "
                    f"Response Code: {response.status_code}, Response: {response.text}"
                )
            
            response_data = response.json().get('data', {})
            actual_mode = response_data.get('mode')
            if actual_mode != 'dynamic':
                raise Exception(
                    f"Failed to enable Smart Charging (retry): mode is still '{actual_mode}', "
                    f"expected 'dynamic'"
                )
        
        return response_data
    else:
        raise Exception(
            f"Failed to enable Smart Charging. "
            f"Response Code: {response.status_code}, Response: {response.text}"
        )


def configure_smart_charging_if_available(charge_point_id, row):
    """Configure smart charging for a ChargePoint if smartcharging_ columns are present.
    
    This function is called after both create and update operations. It checks if the
    row has smartcharging_ columns (indicating the view supports smart charging config)
    and if so, builds the payload and calls enable_smart_charging().
    
    Smart charging must be configured before EVSE creation to allow electricalConfiguration
    to be inherited by EVSEs. This is required for IT networks where maxVoltage='230' is
    only valid when electricalConfiguration='delta'.
    
    Args:
        charge_point_id: The ChargePoint ID to configure
        row: Database row with optional smartcharging_* columns
    """
    # Check if smartcharging columns are available in the view
    if not hasattr(row, 'smartcharging_mode') or row.smartcharging_mode is None:
        logging.debug(f"Skipping smart charging for CP {charge_point_id}: smartcharging_ columns not present")
        return
    
    # Build smart charging payload from row columns
    # Note: maxVoltage must be a string ('230', '400'), NOT an integer
    smart_charging_data = {
        "mode": get_value(row.smartcharging_mode, str),
        "electricalConfiguration": get_value(row.smartcharging_electricalConfiguration, str),
        "maxVoltage": get_value(row.smartcharging_maxVoltage, str),
        "phases": get_value(row.smartcharging_phases, str),
        "phaseRotation": get_value(row.smartcharging_phaseRotation, str, "RST"),
        "defaultChargePointMaxCurrent": get_value(row.smartcharging_defaultChargePointMaxCurrent, int),
        "minCurrent": get_value(row.smartcharging_minCurrent, int, 6)
    }
    
    # Only include connectedPhase if not NULL (required for single_phase with delta/IT)
    connected_phase = get_value(row.smartcharging_connectedPhase, str)
    if connected_phase:
        smart_charging_data["connectedPhase"] = connected_phase
    
    try:
        enable_smart_charging(charge_point_id, smart_charging_data)
        logging.info(f"Enabled smart charging for ChargePoint {charge_point_id}: {smart_charging_data.get('electricalConfiguration')}/{smart_charging_data.get('maxVoltage')}V")
    except Exception as e:
        logging.error(
            f"Failed to enable smart charging for ChargePoint {charge_point_id}. "
            f"Payload: {smart_charging_data}. Error: {e}"
        )
        raise

def update_shared_partners(charge_point_id, partner_ids):
    """Synchronize shared partners for a charge point.
    
    Replaces all existing shared partners with the given list.
    PUT /resources/charge-points/v2.0/{id}/shared-partners
    
    Args:
        charge_point_id: The ChargePoint ID to configure
        partner_ids: List of partner IDs to set as shared partners
    """
    def api_call(base_url, headers):
        url = f"{base_url}/resources/charge-points/v2.0/{charge_point_id}/shared-partners"
        payload = {"partnerIds": partner_ids}
        return requests.put(url, headers=headers, json=payload)

    response = execute_ampeco_api_call(api_call)
    check_ampeco_response(response, 204, 'update_shared_partners', expect_body=False)


def configure_shared_partners_if_available(charge_point_id, row):
    """Configure shared partners for a ChargePoint if shared_partner_ids column is present.
    
    Called after both create and update operations. Checks if the row has a
    shared_partner_ids column with a non-null JSON array, and if so, calls the
    shared partners PUT endpoint to replace the current shared partners.
    
    The view produces a JSON array containing the master partner ID when the
    organisation spans multiple partners (i.e. the master differs from the
    charge point's own partner). NULL means no shared partners are needed.
    
    Args:
        charge_point_id: The ChargePoint ID to configure
        row: Database row with optional shared_partner_ids column
    """
    # Shared partners are only valid for private charge points (Ampeco returns 422 otherwise)
    cp_type = get_value(row.type, str, "private")
    if cp_type != 'private':
        if hasattr(row, 'shared_partner_ids') and row.shared_partner_ids is not None:
            logging.debug(
                f"Skipping shared partners for ChargePoint {charge_point_id}: "
                f"type is '{cp_type}' (must be 'private')"
            )
        return

    if not hasattr(row, 'shared_partner_ids') or row.shared_partner_ids is None:
        return
    
    partner_ids = json.loads(row.shared_partner_ids)
    if not partner_ids:
        return
    
    try:
        update_shared_partners(charge_point_id, partner_ids)
        logging.info(f"Updated shared partners for ChargePoint {charge_point_id}: {partner_ids}")
    except Exception as e:
        logging.error(
            f"Failed to update shared partners for ChargePoint {charge_point_id}. "
            f"Partner IDs: {partner_ids}. Error: {e}"
        )
        raise


def insert_charge_point_mapping(conn_str, row, charge_point_id, device_id):
    """Insert the new ChargePointID into the appropriate mapping table using row data."""
    try:
        with get_db_connection(conn_str) as conn:
            cursor = conn.cursor()

            query = None
            params = None
            success_msg = None

            # Generic mapping_table/mapping_key pattern (key-based migrations)
            if hasattr(row, "mapping_table") and row.mapping_table:
                table_name = row.mapping_table  # e.g., "charger_mapping"
                query = f'''
                    INSERT INTO "Mapping"."{table_name}" (mapping_key, target_charge_point_id)
                    VALUES (?, ?)
                    ON CONFLICT (mapping_key) DO UPDATE SET target_charge_point_id = EXCLUDED.target_charge_point_id
                '''
                params = (row.mapping_key, charge_point_id)
                success_msg = f"Inserted/Updated {table_name} for mapping_key: {row.mapping_key} with target_charge_point_id: {charge_point_id}"
            # Choose exactly one insert based on available attributes
            # ChargerMapping (Charge365 - PostgreSQL) when SourceChargerID is present
            elif hasattr(row, "SourceChargerID"):
                query = (
                    """
                    INSERT INTO "Mapping"."ChargerMapping" ("SourceChargerID", "TargetChargePointID", "CreateWithDeviceID")
                    VALUES (?, ?, ?)
                    """
                )
                params = (getattr(row, "SourceChargerID", None), charge_point_id, device_id)
                success_msg = (
                    f"Inserted TargetChargePointID for SourceChargerID: {getattr(row, 'SourceChargerID', None)}, DeviceID: {device_id} with ChargePointID: {charge_point_id}"
                )
            # ChargeBoxMapping (System: Current - MSSQL)
            elif hasattr(row, "SourceChargeBoxID"):
                query = (
                    """
                    INSERT INTO [Mapping].[ChargeBoxMapping] (SourceChargeBoxID, TargetChargePointID, CreateWithDeviceID)
                    VALUES (?, ?, ?)
                    """
                )
                params = (getattr(row, "SourceChargeBoxID", None), charge_point_id, device_id)
                success_msg = (
                    f"Inserted TargetChargePointID for ProjectCode: {row.ProjectCode}, DeviceID: {device_id} with ChargePointID: {charge_point_id}"
                )
            # StationsMapping (System: EV-Advisor - MSSQL)
            elif hasattr(row, "SourceStationID"):
                query = (
                    """
                    INSERT INTO [Mapping].[StationsMapping] (SourceStationID, TargetChargePointID, CreateWithDeviceID)
                    VALUES (?, ?, ?)
                    """
                )
                params = (getattr(row, "SourceStationID", None), charge_point_id, device_id)
                success_msg = (
                    f"Inserted TargetChargePointID for SourceStationID: {row.SourceStationID}, DeviceID: {device_id} with ChargePointID: {charge_point_id}"
                )

            if query is None or params is None:
                raise SystemExit("No valid mapping condition found; query not built. Halting execution.")

            cursor.execute(query, params)
            if cursor.rowcount == 0:
                raise SystemExit("Insert affected 0 rows; halting execution to prevent missing mapping.")
            logging.info(success_msg)
            conn.commit()
    except Exception as e:
        logging.error(
            f"Failed to insert mapping tables for ProjectCode: {getattr(row, 'ProjectCode', None)}, DeviceID: {device_id}. Error: {e}"
        )
        raise SystemExit(f"Halting execution due to error: {e}")

def process_charge_point(conn_str, row):
    """Processes each charge point row."""
    global created_count, updated_count, error_count
    charge_point_id = row.TargetChargePointID
    charge_point_data = {
        "type": get_value(row.type, str, "private"),
        "status": get_value(row.status, str, "enabled"),
        "network": {
            "id": get_value(row.network_id, str),
            "protocol": get_value(row.network_protocol, str)
        },
        "security": {
            "desiredProfile": get_value(row.security_desiredProfile, int, 0)
        },
        "name": get_value(row.name, str),
        "pin": get_value(row.pin, str),
        "locationId": get_value(row.locationId, int),
        "chargingZoneId": get_value(row.chargingZoneId, int),
        "electricityRateId": get_value(row.electricityRateId, int),
        "subscription": {
            "required": get_value(row.subscription_required, bool, False),
            "planIds": json.loads(cast(str, get_value(row.subscription_planIds, str, "[]")))
        },
        "externalId": get_value(row.externalId, str),
        "capabilities": json.loads(cast(str, get_value(row.capabilities, str, "[]"))),
        "autoStartWithoutAuthorization": get_value(row.autoStartWithoutAuthorization, bool, False),
        "disableAutoStartEmulation": get_value(row.disableAutoStartEmulation, bool, False),
        "modelId": get_value(row.modelId, int),
        "enableAutoFaultRecovery": get_value(row.enableAutoFaultRecovery, bool, True),
        "user": {
            "id": get_value(row.user_id, int)
        },
        "partner": {
            "id": get_value(row.partner_id, int),
            "contractId": get_value(row.partner_contractId, int),
            "corporateBillingAsDefault": get_value(row.partner_corporateBillingAsDefault, bool, False),
            "accessType": get_value(row.partner_accessType, str)
        },
        "utilityId": get_value(row.utilityId, int),
    "tags": json.loads(cast(str, get_value(row.tags, str, "[]"))),
        # Error: Randomised delay setting is not enabled for the system
        # "enabledRandomisedDelay": get_value(row.enabledRandomisedDelay, bool, False), # Applicable only for personal charge points. For public and private charge points will be omitted
        "noticeId": get_value(row.noticeId, int),
        "integratedAt": get_value(row.integratedAt, str),
        "manufacturedAt": get_value(row.manufacturedAt, str)
    }

    # Remove chargingZoneId if null
    if charge_point_data["chargingZoneId"] is None:
        del charge_point_data["chargingZoneId"]

    # Remove user and subscription if type is not 'personal'
    if charge_point_data["type"] != "personal":
        del charge_point_data["user"]
        del charge_point_data["subscription"]

    try:
        if charge_point_id is None:
            # Try to fetch the charge point by networkId
            existing_charge_point = fetch_charge_point_by_network_id(row.network_id)
            if existing_charge_point:
                charge_point_id = existing_charge_point["id"]
                logging.info(f"Found existing ChargePointID: {charge_point_id} for ProjectCode: {row.ProjectCode}, DeviceID: {row.network_id}")
                update_charge_point(charge_point_id, charge_point_data)
                updated_count += 1
                # Configure smart charging after update
                configure_smart_charging_if_available(charge_point_id, row)
                # Configure shared partners after update
                configure_shared_partners_if_available(charge_point_id, row)
            else:
                # Create a new ChargePoint if not found
                response_data = create_charge_point(charge_point_data)
                charge_point_id = response_data['id']
                created_count += 1
                logging.info(f"Created new ChargePointID: {charge_point_id} for ProjectCode: {row.ProjectCode}, DeviceID: {row.network_id}")
                # Configure smart charging after create
                configure_smart_charging_if_available(charge_point_id, row)
                # Configure shared partners after create
                configure_shared_partners_if_available(charge_point_id, row)

            # Insert mapping table with ChargePointID
            insert_charge_point_mapping(conn_str, row, charge_point_id, row.network_id)
        else:
            # Update the existing ChargePoint
            update_charge_point(charge_point_id, charge_point_data)
            updated_count += 1
            # Configure smart charging after update
            configure_smart_charging_if_available(charge_point_id, row)
            # Configure shared partners after update
            configure_shared_partners_if_available(charge_point_id, row)
    except Exception as e:
        error_count += 1
        errors.append((row, str(e)))
        logging.error(f"Error processing row {row}: {e}")

def main():
    """Main function to fetch and process charge points."""
    global total_rows
    conn_str = get_db_connection_string()  # Fetch connection string
    charge_points = fetch_charge_points(conn_str)
    total_rows = len(charge_points)
    logging.info(f"Total charge points fetched: {total_rows}")

    for index, row in enumerate(charge_points, start=1):
        logging.info(f"Processing Charge Point for ProjectCode: {row.ProjectCode}, DeviceID: {row.network_id} ({index} of {total_rows})...")
        process_charge_point(conn_str, row)

    # Summarize results
    logging.info(f"Total rows processed: {total_rows}")
    logging.info(f"ChargePoints created: {created_count}")
    logging.info(f"ChargePoints updated: {updated_count}")
    logging.info(f"Errors encountered: {error_count}")

    if errors:
        logging.info("Rows causing errors and their exceptions:")
        for error_row, error_msg in errors:
            logging.info(f"Row: {error_row}, Error: {error_msg}")

if __name__ == "__main__":
    setup_logging("CreateOrUpdateChargePoints")
    try:
        main()
    except Exception as e:
        logging.error(f"An error occurred: {e}")
