"""
AttachChargePointToCircuit.py - Reconcile charge-point ↔ circuit assignments

PURPOSE:
    Reads the desired state from Target.ChargePointCircuitAttachment (which now
    includes ALL active chargers at migrated locations) and reconciles against
    the live Ampeco state.  Rows with target_circuit_id = NULL mean "should not
    be on any circuit."

    Per charge point the script decides:
      - desired NULL, current NULL  → skip
      - desired NULL, current set   → detach
      - desired == current          → skip
      - desired set, current NULL   → attach
      - desired set, current differs → detach old + attach new (reassign)

USAGE:
    1. Set DRY_RUN = True to preview changes (default)
    2. Run: python AttachChargePointToCircuit.py
    3. Review log output
    4. Set DRY_RUN = False to apply changes
    5. Run again to execute
"""

import requests
import logging
import pyodbc
from utils.config_utils import get_db_connection_string
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import get_db_connection, get_sql_dialect, quote_identifier

# ── Configuration ──────────────────────────────────────────────────────────
DRY_RUN = False  # Set to True to preview changes without applying

# ── Counters ───────────────────────────────────────────────────────────────
stats = {
    'total': 0,
    'skipped': 0,
    'attached': 0,
    'detached': 0,
    'reassigned': 0,
    'errors': [],
}


# ── Database ───────────────────────────────────────────────────────────────

def fetch_desired_state(conn_str):
    """Fetch desired circuit state for every active charge point at migrated locations.

    Returns all rows where target_charge_point_id IS NOT NULL.
    Rows with target_circuit_id = NULL represent charge points that should NOT
    be attached to any circuit.

    Returns an empty list and logs a warning if the view does not exist
    (PostgreSQL SQLSTATE 42P01), allowing the caller to skip gracefully.
    """
    try:
        with get_db_connection(conn_str) as conn:
            cursor = conn.cursor()
            dialect = get_sql_dialect(conn)
            target_schema = quote_identifier("Target", dialect)
            table = quote_identifier("ChargePointCircuitAttachment", dialect)
            query = f"""
                SELECT * FROM {target_schema}.{table}
                WHERE target_charge_point_id IS NOT NULL
            """
            cursor.execute(query)
            return cursor.fetchall()
    except pyodbc.ProgrammingError as e:
        if e.args and e.args[0] == "42P01":
            logging.warning("View Target.ChargePointCircuitAttachment does not exist — skipping circuit attachment step.")
            return []
        raise


# ── Ampeco API helpers ─────────────────────────────────────────────────────

def get_current_circuit_id(charge_point_id):
    """Return the circuitId a charge point is currently attached to, or None.

    Calls GET /resources/charge-points/v2.0/{id}?include[0]=smartCharging
    """
    def api_call(base_url, headers):
        url = (
            f"{base_url}/resources/charge-points/v2.0/{charge_point_id}"
            f"?include[0]=smartCharging"
        )
        return requests.get(url, headers=headers)

    response = execute_ampeco_api_call(api_call)
    data = check_ampeco_response(response, 200, 'get_current_circuit_id')
    # data is the charge-point object; smartCharging may or may not be present
    smart_charging = data.get("smartCharging") if data else None
    if smart_charging:
        return smart_charging.get("circuitId")
    return None


def attach_charge_point_to_circuit(charge_point_id, circuit_id, priority=1):
    """POST /actions/circuit/v2.0/{circuitId}/attach-charge-point — 202, no body."""
    def api_call(base_url, headers):
        url = f"{base_url}/actions/circuit/v2.0/{circuit_id}/attach-charge-point"
        data = {
            "chargePointId": charge_point_id,
            "priority": priority
        }
        return requests.post(url, headers=headers, json=data)

    response = execute_ampeco_api_call(api_call)
    check_ampeco_response(response, 202, 'attach_charge_point_to_circuit', expect_body=False)


def detach_charge_point_from_circuit(circuit_id, charge_point_id):
    """POST /actions/circuit/v2.0/{circuitId}/detach-charge-point/{chargePointId} — 202, no body."""
    def api_call(base_url, headers):
        url = (
            f"{base_url}/actions/circuit/v2.0/{circuit_id}"
            f"/detach-charge-point/{charge_point_id}"
        )
        return requests.post(url, headers=headers)

    response = execute_ampeco_api_call(api_call)
    check_ampeco_response(response, 202, 'detach_charge_point_from_circuit', expect_body=False)


# ── Reconciliation logic ──────────────────────────────────────────────────

def process_reconciliation(row):
    """Reconcile a single charge point's circuit assignment against desired state."""
    global stats

    charge_point_id = row.target_charge_point_id
    desired_circuit_id = row.target_circuit_id          # may be None
    priority = getattr(row, 'priority', 1) or 1
    label = getattr(row, 'charger_name', None) or f"CP {charge_point_id}"

    try:
        current_circuit_id = get_current_circuit_id(charge_point_id)

        # ── Case 1: desired NULL, current NULL → nothing to do
        if desired_circuit_id is None and current_circuit_id is None:
            logging.info(f"  {label}: no circuit desired, none attached → skip")
            stats['skipped'] += 1
            return

        # ── Case 2: desired NULL, current set → detach
        if desired_circuit_id is None and current_circuit_id is not None:
            logging.info(
                f"  {label}: should NOT be on circuit, currently on {current_circuit_id} → detach"
            )
            if not DRY_RUN:
                detach_charge_point_from_circuit(current_circuit_id, charge_point_id)
            stats['detached'] += 1
            return

        # ── Case 3: desired == current → already correct
        if desired_circuit_id == current_circuit_id:
            logging.info(
                f"  {label}: already on correct circuit {current_circuit_id} → skip"
            )
            stats['skipped'] += 1
            return

        # ── Case 4: desired set, current differs or NULL → (detach old +) attach new
        if current_circuit_id is not None:
            # Reassign: detach from old, then attach to new
            logging.info(
                f"  {label}: on circuit {current_circuit_id}, should be on "
                f"{desired_circuit_id} → reassign (detach + attach)"
            )
            if not DRY_RUN:
                detach_charge_point_from_circuit(current_circuit_id, charge_point_id)
                attach_charge_point_to_circuit(charge_point_id, desired_circuit_id, priority)
            stats['reassigned'] += 1
        else:
            # Simple attach
            logging.info(
                f"  {label}: not on any circuit, should be on "
                f"{desired_circuit_id} → attach"
            )
            if not DRY_RUN:
                attach_charge_point_to_circuit(charge_point_id, desired_circuit_id, priority)
            stats['attached'] += 1

    except Exception as e:
        stats['errors'].append((charge_point_id, desired_circuit_id, label, str(e)))
        logging.error(
            f"  {label}: ERROR reconciling CP {charge_point_id} "
            f"(desired circuit {desired_circuit_id}): {e}"
        )


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    """Reconcile charge-point ↔ circuit assignments against desired state."""
    global stats
    mode = "DRY RUN" if DRY_RUN else "LIVE"
    logging.info(f"Mode: {mode}")
    logging.info("")

    conn_str = get_db_connection_string()
    rows = fetch_desired_state(conn_str)

    # Warn about rows without a target charge point (should be excluded by query,
    # but double-check in case view semantics change)
    no_cp = [r for r in rows if r.target_charge_point_id is None]
    if no_cp:
        logging.warning(
            f"Skipping {len(no_cp)} charge points not yet created in target CSMS"
        )
        rows = [r for r in rows if r.target_charge_point_id is not None]

    stats['total'] = len(rows)
    logging.info(f"Total charge points to reconcile: {stats['total']}")

    if stats['total'] == 0:
        logging.warning("No charge points found. Ensure circuits and charge points have been created first.")
        return

    logging.info("")
    logging.info("=" * 60)
    logging.info(f"Reconciling charge-point ↔ circuit assignments ({mode})...")
    logging.info("=" * 60)

    for idx, row in enumerate(rows, 1):
        logging.info(f"[{idx}/{stats['total']}] ChargePoint {row.target_charge_point_id}")
        process_reconciliation(row)

    # ── Summary ────────────────────────────────────────────────────────────
    logging.info("")
    logging.info("=" * 60)
    logging.info(f"Summary ({mode}):")
    logging.info("=" * 60)
    logging.info(f"Total charge points:  {stats['total']}")
    logging.info(f"Attached:             {stats['attached']}")
    logging.info(f"Detached:             {stats['detached']}")
    logging.info(f"Reassigned:           {stats['reassigned']}")
    logging.info(f"Skipped (no change):  {stats['skipped']}")
    logging.info(f"Errors:               {len(stats['errors'])}")

    if stats['errors']:
        logging.info("")
        logging.info("Errors:")
        for cp_id, circuit_id, label, msg in stats['errors']:
            logging.error(f"  {label} (CP {cp_id}) → Circuit {circuit_id}: {msg}")

    if DRY_RUN:
        logging.info("")
        logging.info("⚠  DRY_RUN is enabled — no changes were made. Set DRY_RUN = False to apply.")


if __name__ == "__main__":
    try:
        setup_logging("AttachChargePointToCircuit")
        main()
    except Exception as e:
        logging.error(f"An error occurred: {e}")
