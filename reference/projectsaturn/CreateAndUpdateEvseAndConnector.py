import pyodbc
import requests
import json
import logging
from datetime import datetime
from utils.config_utils import get_db_connection_string, get_api_base_url, get_api_token
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import get_db_connection, get_value, get_sql_dialect, quote_identifier

# Initialize counters and error/warning lists
stats = {
    'processed': 0,
    'created_evse': 0,
    'updated_evse': 0,
    'created_connector': 0,
    'updated_connector': 0,
    'errors': [],
    'warnings': []
}

def fetch_evse_and_connectors(conn_str):
    """Fetch EVSE and Connectors (dialect-aware), only rows with TargetChargePointID."""
    with get_db_connection(conn_str) as conn:
        cursor = conn.cursor()
        dialect = get_sql_dialect(conn)
        target_schema = quote_identifier("Target", dialect)
        table = quote_identifier("EvseAndConnectors", dialect)
        target_cp_id = quote_identifier("TargetChargePointID", dialect)
        query = f"SELECT * FROM {target_schema}.{table} WHERE {target_cp_id} IS NOT NULL"
        cursor.execute(query)
        return cursor.fetchall()

def generate_duplicate_suffix():
    """Generate a unique suffix for duplicate remediation (ddhhmmssff format)."""
    now = datetime.now()
    return now.strftime("%d%H%M%S") + f"{now.microsecond // 10000:02d}"


def apply_evse_remediation(response, evse_data, row, target_charge_point_id, target_evse_id=None):
    """Apply remediation for known 422 errors. Logs warning and adds to stats. Returns modified_data or None."""
    global stats
    body = response.json()
    errors = body.get("errors", {})

    remediations = []

    # Handle "external id already taken" by clearing it
    if "externalId" in errors and any("already been taken" in msg for msg in errors["externalId"]):
        evse_data["externalId"] = None
        remediations.append("cleared externalId (was duplicate)")

    # Handle "physical reference already taken" by appending unique suffix
    if "physicalReference" in errors and any("already been taken" in msg for msg in errors["physicalReference"]):
        suffix = generate_duplicate_suffix()
        original = evse_data["physicalReference"]
        evse_data["physicalReference"] = f"{original}-duplicate-error-{suffix}"
        remediations.append(f"renamed physicalReference from '{original}' to '{evse_data['physicalReference']}'")

    if remediations:
        operation = "update_evse" if target_evse_id else "create_evse"
        evse_id_part = f", EVSE {target_evse_id}" if target_evse_id else ""
        warning_msg = (
            f"{operation} remediated for CP {target_charge_point_id}{evse_id_part}: "
            f"networkId={evse_data.get('networkId')}, externalId={evse_data.get('externalId')}, "
            f"physicalReference={evse_data.get('physicalReference')}. "
            f"Remediations: {'; '.join(remediations)}"
        )
        logging.warning(warning_msg)
        stats['warnings'].append((row, warning_msg))
        return evse_data
    return None


def create_evse(target_charge_point_id, evse_data, row):
    """Create an EVSE in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/charge-points/v2.0/{target_charge_point_id}/evses"
        return requests.post(url, headers=headers, json=evse_data)

    response = execute_ampeco_api_call(api_call)

    # Check for remediable 422 errors
    if response.status_code == 422:
        if apply_evse_remediation(response, evse_data, row, target_charge_point_id) is not None:
            # Retry with modified payload
            response = execute_ampeco_api_call(api_call)

    return check_ampeco_response(response, 201, 'create_evse')

def update_evse(target_charge_point_id, target_evse_id, evse_data, row):
    """Update an EVSE in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/charge-points/v2.0/{target_charge_point_id}/evses/{target_evse_id}"
        return requests.patch(url, headers=headers, json=evse_data)

    response = execute_ampeco_api_call(api_call)

    # Check for remediable 422 errors
    if response.status_code == 422:
        if apply_evse_remediation(response, evse_data, row, target_charge_point_id, target_evse_id) is not None:
            # Retry with modified payload
            response = execute_ampeco_api_call(api_call)

    return check_ampeco_response(response, 200, 'update_evse')

def create_connector(target_charge_point_id, target_evse_id, connector_data):
    """Create a connector in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/charge-points/v2.0/{target_charge_point_id}/evses/{target_evse_id}/connectors"
        return requests.post(url, headers=headers, json=connector_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_connector')

def update_connector(target_charge_point_id, target_evse_id, target_connector_id, connector_data):
    """Update a connector in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/charge-points/v2.0/{target_charge_point_id}/evses/{target_evse_id}/connectors/{target_connector_id}"
        return requests.patch(url, headers=headers, json=connector_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_connector')

def update_mapping(conn_str, row, target_evse_id=None, target_connector_id=None):
    """Update mapping tables with the new EVSE or Connector ID (single-query pattern).

    - If row has mapping_table/mapping_key (generic key-based migrations), upsert "Mapping"."{table_name}" by mapping_key.
    - Else if row has SourceChargerID (Charge365/PostgreSQL), update "Mapping"."ChargerMapping" by SourceChargerID.
    - Else if row has SourceChargePointID (MSSQL), update [Mapping].[ConnectorMapping] by SourceChargePointID.
    - Exactly one of target_evse_id or target_connector_id must be provided.
    """
    if (target_evse_id is None and target_connector_id is None) or (
        target_evse_id is not None and target_connector_id is not None
    ):
        raise SystemExit("Provide exactly one of target_evse_id or target_connector_id to update_mapping().")

    with get_db_connection(conn_str) as conn:
        cursor = conn.cursor()

        query = None
        params = None
        success_msg = None

        # Generic mapping_table/mapping_key pattern (key-based migrations)
        if hasattr(row, "mapping_table") and row.mapping_table:
            table_name = row.mapping_table  # e.g., "connector_mapping"
            physical_ref = getattr(row, "physicalReference", None)
            
            if target_evse_id is not None:
                query = f'''
                    INSERT INTO "Mapping"."{table_name}" (mapping_key, target_evse_id, physical_reference)
                    VALUES (?, ?, ?)
                    ON CONFLICT (mapping_key) DO UPDATE SET 
                        target_evse_id = EXCLUDED.target_evse_id,
                        physical_reference = EXCLUDED.physical_reference
                '''
                params = (row.mapping_key, target_evse_id, physical_ref)
                success_msg = f"Upserted {table_name} target_evse_id for mapping_key: {row.mapping_key}"
            elif target_connector_id is not None:
                query = f'''
                    UPDATE "Mapping"."{table_name}"
                    SET target_connector_id = ?
                    WHERE mapping_key = ?
                '''
                params = (target_connector_id, row.mapping_key)
                success_msg = f"Updated {table_name} target_connector_id for mapping_key: {row.mapping_key}"

        # Charge365: update PostgreSQL ChargerMapping by SourceChargerID
        elif hasattr(row, "SourceChargerID") and getattr(row, "SourceChargerID", None):
            if target_evse_id is not None:
                query = (
                    """
                    UPDATE "Mapping"."ChargerMapping"
                    SET "TargetEvseID" = ?
                    WHERE "SourceChargerID" = ?
                    """
                )
                params = (target_evse_id, row.SourceChargerID)
                success_msg = f"Updated ChargerMapping TargetEvseID for SourceChargerID: {row.SourceChargerID}"
            elif target_connector_id is not None:
                query = (
                    """
                    UPDATE "Mapping"."ChargerMapping"
                    SET "TargetConnectorID" = ?
                    WHERE "SourceChargerID" = ?
                    """
                )
                params = (target_connector_id, row.SourceChargerID)
                success_msg = f"Updated ChargerMapping TargetConnectorID for SourceChargerID: {row.SourceChargerID}"

        # Default: MSSQL ConnectorMapping by SourceChargePointID
        elif hasattr(row, "SourceChargePointID"):
            if target_evse_id is not None:
                query = (
                    """
                    UPDATE [Mapping].[ConnectorMapping]
                    SET TargetEvseID = ?
                    WHERE SourceChargePointID = ?
                    """
                )
                params = (target_evse_id, row.SourceChargePointID)
                success_msg = f"Updated ConnectorMapping TargetEvseID for SourceChargePointID: {row.SourceChargePointID}"
            elif target_connector_id is not None:
                query = (
                    """
                    UPDATE [Mapping].[ConnectorMapping]
                    SET TargetConnectorID = ?
                    WHERE SourceChargePointID = ?
                    """
                )
                params = (target_connector_id, row.SourceChargePointID)
                success_msg = f"Updated ConnectorMapping TargetConnectorID for SourceChargePointID: {row.SourceChargePointID}"

        if query is None or params is None:
            raise SystemExit("No valid mapping condition found; query not built. Halting execution.")

        cursor.execute(query, params)
        if cursor.rowcount == 0:
            raise SystemExit("Update affected 0 rows; halting execution to prevent missing mapping.")
        conn.commit()
        logging.info(success_msg)

def fetch_existing_evses_and_connectors(target_charge_point_id):
    """Fetch existing EVSEs and Connectors for a specific ChargePoint from Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/charge-points/v2.0/{target_charge_point_id}/evses?include[0]=connectors"
        return requests.get(url, headers=headers)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'fetch_existing_evses_and_connectors')

def process_row(conn_str, row):
    """Processes each EVSE and Connector row."""
    global stats
    target_charge_point_id = row.TargetChargePointID
    target_evse_id = row.TargetEvseID
    target_connector_id = row.TargetConnectorID

    evse_data = {
        "currentType": get_value(row.currentType, str),
        "status": get_value(row.status, str),
        "physicalReference": get_value(row.physicalReference, str),
        "label": get_value(row.label, str),
        "networkId": get_value(row.networkId, str),
        "midMeterCertificationEndYear": get_value(row.midMeterCertificationEndYear, int),
        "tariffGroupId": get_value(row.tariffGroupId, int),
        "allowsReservation": get_value(row.allowsReservation, bool),
        "powerOptions": {
            "maxPower": get_value(row.powerOptions_maxPower, int),
            "maxVoltage": get_value(row.powerOptions_maxVoltage, str),
            "maxAmperage": get_value(row.powerOptions_maxAmperage, int),
            "phases": get_value(row.powerOptions_phases, str),
            "phaseRotation": get_value(row.powerOptions_phaseRotation, str, 'RST'),
            "connectedPhase": get_value(row.powerOptions_connectedPhase, str)
        },
        "externalId": get_value(row.externalId, str)
    }

    # Warn if physicalReference doesn't start with ProjectCode and record it for summary (happy path)
    project_code = row.ProjectCode
    phys_ref = evse_data["physicalReference"]
    if not phys_ref.startswith(project_code):
        warning_msg = (
            f"physicalReference '{phys_ref}' does not start with ProjectCode '{project_code}'"
        )
        logging.warning(warning_msg)
        stats['warnings'].append((row, warning_msg))

    connector_data = {
        "type": get_value(row.connector_type, str),
        "format": get_value(row.connector_format, str),
        "status": get_value(row.connector_status, str)
    }

    try:
        # Fetch existing EVSEs and Connectors if IDs are not provided
        if target_evse_id is None or target_connector_id is None:
            existing_evses = fetch_existing_evses_and_connectors(target_charge_point_id)
            for evse in existing_evses:
                if evse["networkId"] == evse_data["networkId"]:
                    target_evse_id = evse["id"]
                    update_mapping(conn_str, row, target_evse_id=target_evse_id)
                    for connector in evse.get("connectors", []):
                        if connector["type"] == connector_data["type"]:
                            target_connector_id = connector["id"]
                            update_mapping(conn_str, row, target_connector_id=target_connector_id)
                            break
                    break

        # Create or update EVSE
        if target_evse_id is None:
            response_data = create_evse(target_charge_point_id, evse_data, row)
            target_evse_id = response_data['id']
            stats['created_evse'] += 1
            logging.info(f"Created EVSE '{evse_data['physicalReference']}' with id={target_evse_id} (chargePointId={target_charge_point_id})")
            update_mapping(conn_str, row, target_evse_id=target_evse_id)
        else:
            update_evse(target_charge_point_id, target_evse_id, evse_data, row)
            stats['updated_evse'] += 1
            logging.info(f"Updated EVSE '{evse_data['physicalReference']}' (id={target_evse_id}, chargePointId={target_charge_point_id})")

        # Create or update Connector
        if target_connector_id is None:
            response_data = create_connector(target_charge_point_id, target_evse_id, connector_data)
            target_connector_id = response_data['id']
            stats['created_connector'] += 1
            logging.info(f"Created connector id={target_connector_id} (evseId={target_evse_id})")
            update_mapping(conn_str, row, target_connector_id=target_connector_id)
        else:
            update_connector(target_charge_point_id, target_evse_id, target_connector_id, connector_data)
            stats['updated_connector'] += 1

    except Exception as e:
        stats['errors'].append((row, str(e)))
        logging.error(f"Error processing row {row}: {e}")

def main():
    """Main function to fetch and process EVSE and Connectors."""
    global stats
    conn_str = get_db_connection_string()
    rows = fetch_evse_and_connectors(conn_str)
    total_rows = len(rows)
    logging.info(f"Total EVSE and Connectors fetched: {total_rows}")

    for index, row in enumerate(rows, start=1):
        logging.info(f"Processing {index} of {total_rows}...")
        stats['processed'] += 1
        process_row(conn_str, row)

    # Summarize results
    logging.info(f"Processed: {stats['processed']}")
    logging.info(f"Created EVSE: {stats['created_evse']}")
    logging.info(f"Updated EVSE: {stats['updated_evse']}")
    logging.info(f"Created Connector: {stats['created_connector']}")
    logging.info(f"Updated Connector: {stats['updated_connector']}")
    logging.info(f"Warnings: {len(stats['warnings'])}")
    for warning in stats['warnings']:
        logging.warning(f"Warning for row {warning[0]}: {warning[1]}")
    logging.info(f"Errors: {len(stats['errors'])}")
    for error in stats['errors']:
        logging.error(f"Error processing row {error[0]}: {error[1]}")

if __name__ == "__main__":
    try:
        setup_logging("CreateAndUpdateEvseAndConnector")
        main()
    except Exception as e:
        logging.error(f"An error occurred: {e}")
