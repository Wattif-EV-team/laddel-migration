import requests
import logging
import pyodbc
from utils.config_utils import get_db_connection_string
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import get_db_connection, get_sql_dialect, quote_identifier, get_value

# Initialize counters and error list
stats = {
    'total': 0,
    'detached': 0,
    'created': 0,
    'updated': 0,
    'parents_set': 0,
    'errors': [],
    'warnings': []
}


def fetch_circuits(conn_str):
    """Fetch circuits from Target.Circuits.

    Returns an empty list and logs a warning if the view does not exist
    (PostgreSQL SQLSTATE 42P01), allowing the caller to skip gracefully.
    """
    try:
        with get_db_connection(conn_str) as conn:
            cursor = conn.cursor()
            dialect = get_sql_dialect(conn)
            target_schema = quote_identifier("Target", dialect)
            table = quote_identifier("Circuits", dialect)
            query = f"SELECT * FROM {target_schema}.{table} ORDER BY mapping_key"
            cursor.execute(query)
            return cursor.fetchall()
    except pyodbc.ProgrammingError as e:
        if e.args and e.args[0] == "42P01":
            logging.warning("View Target.Circuits does not exist — skipping circuits step.")
            return []
        raise


def create_circuit(circuit_data):
    """Create a circuit in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/circuits/v2.0"
        return requests.post(url, headers=headers, json=circuit_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_circuit')


def update_circuit(circuit_id, circuit_data):
    """Update a circuit in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/circuits/v2.0/{circuit_id}"
        return requests.patch(url, headers=headers, json=circuit_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_circuit')


def update_circuit_mapping(conn_str, row, circuit_id):
    """Insert or update circuit_mapping with target_circuit_id.
    
    Uses the mapping_table/mapping_key pattern for generic key-based migrations.
    """
    with get_db_connection(conn_str) as conn:
        cursor = conn.cursor()
        
        # Generic mapping_table/mapping_key pattern (key-based migrations)
        if hasattr(row, "mapping_table") and row.mapping_table:
            table_name = row.mapping_table  # e.g., "circuit_mapping"
            query = f'''
                INSERT INTO "Mapping"."{table_name}" (mapping_key, target_circuit_id)
                VALUES (?, ?)
                ON CONFLICT (mapping_key) DO UPDATE SET target_circuit_id = EXCLUDED.target_circuit_id
            '''
            params = (row.mapping_key, circuit_id)
            success_msg = f"Upserted {table_name} target_circuit_id for mapping_key: {row.mapping_key}"
        else:
            raise SystemExit("No mapping_table found on row; cannot update circuit mapping.")
        
        cursor.execute(query, params)
        if cursor.rowcount == 0:
            raise SystemExit(f"Upsert affected 0 rows for mapping_key: {row.mapping_key}. Halting.")
        conn.commit()
        logging.info(success_msg)


def process_circuit(conn_str, row, pass_number):
    """Process a single circuit: create or update.
    
    Args:
        conn_str: Database connection string
        row: Circuit row from Target.Circuits view
        pass_number: 1 = detach parents, 2 = create/update with full settings, 3 = reattach parents
    """
    global stats
    
    circuit_id = row.target_circuit_id
    
    # Pass 1 and 3 only operate on existing circuits
    if pass_number != 2 and circuit_id is None:
        return
    
    # Build payload conditionally based on pass
    circuit_data = {
        "name": get_value(row.name, str),
    }
    
    # Pass 2 & 3: include electrical settings
    if pass_number >= 2:
        circuit_data.update({
            "phases": get_value(row.phases, str),
            "maxVoltage": get_value(row.maxVoltage, str),
            "maxCurrent": get_value(row.maxCurrent, int),
            "minChargePointCurrent": get_value(row.minChargePointCurrent, int, 6),
            "phaseRotation": get_value(row.phaseRotation, str, "RST"),
            "electricalConfiguration": get_value(row.electricalConfiguration, str, "star"),
            "electricityMeterId": get_value(row.electricityMeterId, int),
        })
    
    # Pass 1 & 2: clear parent; Pass 3: set parent to resolved id
    if pass_number <= 2:
        circuit_data["parentCircuitId"] = None
    else:
        circuit_data["parentCircuitId"] = get_value(row.parentCircuitId, int)

    try:
        if circuit_id is None:
            # Create new circuit (only happens in pass 2)
            response_data = create_circuit(circuit_data)
            circuit_id = response_data['id']
            stats['created'] += 1
            update_circuit_mapping(conn_str, row, circuit_id)
            logging.info(f"Pass {pass_number} - Created circuit '{row.name}' with id={circuit_id}")
        else:
            # Update existing circuit
            update_circuit(circuit_id, circuit_data)
            if pass_number == 1:
                stats['detached'] += 1
                logging.info(f"Pass {pass_number} - Detached parent from circuit '{row.name}' (id={circuit_id})")
            elif pass_number == 3:
                stats['parents_set'] += 1
                logging.info(f"Pass {pass_number} - Set parentCircuitId={circuit_data['parentCircuitId']} on circuit '{row.name}' (id={circuit_id})")
            else:
                stats['updated'] += 1
                logging.info(f"Pass {pass_number} - Updated circuit '{row.name}' (id={circuit_id})")

    except Exception as e:
        stats['errors'].append((row, str(e)))
        logging.error(f"Pass {pass_number} - Error processing circuit '{row.name}': {e}")


PASS_DESCRIPTIONS = {
    1: "Detaching parent references",
    2: "Creating/updating circuits with full settings",
    3: "Reattaching parent references",
}


def main():
    """Main function to create/update circuits in three passes.
    
    Pass 1: Detach parent references (patch parentCircuitId=null only) so that
            electrical settings can be changed without constraint violations.
    Pass 2: Create new circuits and update all circuits with full electrical
            settings (parentCircuitId still null).
    Pass 3: Reattach parent references now that all circuits have consistent
            electrical settings.
    """
    global stats
    conn_str = get_db_connection_string()
    
    for pass_number in [1, 2, 3]:
        # Re-fetch each pass to pick up newly created IDs and resolved parents
        circuits = fetch_circuits(conn_str)
        
        if pass_number == 1:
            stats['total'] = len(circuits)
            logging.info(f"Total circuits to process: {stats['total']}")
        
        # Pass 3: only circuits that need a parent assigned
        if pass_number == 3:
            circuits = [r for r in circuits if r.parentCircuitId]
        
        logging.info("")
        logging.info("=" * 60)
        logging.info(f"Pass {pass_number}: {PASS_DESCRIPTIONS[pass_number]}...")
        logging.info("=" * 60)
        logging.info(f"Circuits in this pass: {len(circuits)}")
        
        for idx, row in enumerate(circuits, 1):
            logging.info(f"Pass {pass_number} - Processing {idx} of {len(circuits)}: {row.name}")
            process_circuit(conn_str, row, pass_number)
    
    # Summary
    logging.info("")
    logging.info("=" * 60)
    logging.info("Summary:")
    logging.info("=" * 60)
    logging.info(f"Total circuits: {stats['total']}")
    logging.info(f"Detached: {stats['detached']}")
    logging.info(f"Created: {stats['created']}")
    logging.info(f"Updated: {stats['updated']}")
    logging.info(f"Parents set: {stats['parents_set']}")
    logging.info(f"Warnings: {len(stats['warnings'])}")
    logging.info(f"Errors: {len(stats['errors'])}")
    
    if stats['warnings']:
        logging.info("")
        logging.info("Warnings:")
        for row, msg in stats['warnings']:
            logging.warning(f"  {row.name}: {msg}")
    
    if stats['errors']:
        logging.info("")
        logging.info("Errors:")
        for row, msg in stats['errors']:
            logging.error(f"  {row.name}: {msg}")


if __name__ == "__main__":
    try:
        setup_logging("CreateOrUpdateCircuits")
        main()
    except Exception as e:
        logging.error(f"An error occurred: {e}")
