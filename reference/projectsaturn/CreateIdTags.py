import pyodbc
import requests
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from utils.config_utils import get_db_connection_string
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import try_get_value, get_db_connection, get_sql_dialect, quote_identifier

# ── Feature flags ───────────────────────────────────────────────────────
ENABLE_UPDATES = False

# ── Statistics ──────────────────────────────────────────────────────────
stats = {
    'total': 0,
    'created': 0,
    'existing_mapped': 0,
    'updated': 0,
    'user_id_mismatch': 0,
    'error_counts': {},   # error_class -> count
}

def _classify_idtag_error(error_msg: str) -> str:
    """Return a short error class from an IdTag processing error message."""
    msg = str(error_msg)
    if 'status code 422' in msg and 'user id is invalid' in msg:
        return 'invalid user id (HTTP 422)'
    if 'status code 422' in msg:
        return 'validation error (HTTP 422)'
    if 'status code 500' in msg:
        return 'server error (HTTP 500)'
    if 'status code' in msg:
        return 'API error (other HTTP)'
    return 'other error'

def _record_error(error_class: str):
    stats['error_counts'][error_class] = stats['error_counts'].get(error_class, 0) + 1


def normalize_uid(uid: str) -> str:
    """Normalize an IdTag UID for consistent case-insensitive lookups.

    Trims whitespace and lowercases the value. Returns empty string for None/empty input.
    """
    if uid is None:
        return ""
    return uid.strip().lower()

def fetch_id_tags(conn_str):
    with get_db_connection(conn_str) as conn:
        dialect = get_sql_dialect(conn)
        target_schema = quote_identifier("Target", dialect)
        id_tags_table = quote_identifier("IdTags", dialect)
        target_id_tags_id = quote_identifier("TargetIdTagsID", dialect)
        user_id = quote_identifier("userId", dialect)
        
        if ENABLE_UPDATES:
            query = f"SELECT * FROM {target_schema}.{id_tags_table} WHERE {user_id} IS NOT NULL"
        else:
            query = f"SELECT * FROM {target_schema}.{id_tags_table} WHERE {target_id_tags_id} IS NULL AND {user_id} IS NOT NULL"
        
        cursor = conn.cursor()
        cursor.execute(query)
        return cursor.fetchall()

def create_id_tag(tag_data):
    """Creates an Id Tag using the REST API and returns the response data."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/id-tags/v2.0"
        return requests.post(url, headers=headers, json=tag_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_id_tag')

def update_id_tag(id_tag_id, tag_data):
    """Update an existing IdTag in Ampeco API via PATCH."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/id-tags/v2.0/{id_tag_id}"
        return requests.patch(url, headers=headers, json=tag_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_id_tag')

def fetch_id_tags_from_ampeco():
    """Fetch all existing IdTags from Ampeco and return a dict keyed by normalized idTagUid.

    The normalization makes lookups case-insensitive and whitespace-insensitive.
    """
    items = []
    logging.info("Fetching IdTags from Ampeco (paginated)...")

    def api_call(base_url, headers, url=None):
        if not url:
            url = f"{base_url}/resources/id-tags/v2.0"
        return requests.get(url, headers=headers)

    url = None
    page = 0
    while True:
        response = execute_ampeco_api_call(lambda base_url, headers: api_call(base_url, headers, url))
        data = check_ampeco_response(response, 200, 'fetch_id_tags_from_ampeco')
        items.extend(data)
        page += 1
        if page % 25 == 0:
            logging.info(f"Fetched {page} pages... total IdTags so far: {len(items)}")
        url = response.json().get('links', {}).get('next')
        if not url:
            break

    # Build map by normalized idTagUid
    by_uid_normalized = {}
    for item in items:
        raw_uid = item.get('idTagUid')
        if raw_uid:
            key = normalize_uid(raw_uid)
            # Only set if not already present to avoid accidental overwrite if duplicates differ only by case
            if key not in by_uid_normalized:
                by_uid_normalized[key] = item
            else:
                logging.warning(
                    f"Duplicate IdTag differing only by case encountered. Keeping first. existing={by_uid_normalized[key].get('idTagUid')} ignored={raw_uid}"
                )
    return by_uid_normalized

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def execute_query_with_retry(cursor, query, params):
    cursor.execute(query, params)
    return cursor.rowcount

def insert_mapping(conn_str, row, target_id_tags_id, created_with_uid,
                   idtag_created_in_ampeco=None, user_id_match=None):
    with get_db_connection(conn_str) as conn:
        with conn.cursor() as cursor:
            try:
                # Generic mapping_table/mapping_key pattern (key-based migrations like Project Sleet)
                if hasattr(row, 'mapping_table') and row.mapping_table:
                    table_name = row.mapping_table  # e.g., "rfid_mapping"
                    query = f'''
                    INSERT INTO "Mapping"."{table_name}" 
                    (mapping_key, target_idtag_id, created_with_uid,
                     idtag_created_in_ampeco, user_id_match)
                    VALUES (?, ?, ?, ?, ?)
                    '''
                    params = (row.mapping_key, target_id_tags_id, created_with_uid,
                              idtag_created_in_ampeco, user_id_match)
                elif hasattr(row, 'SourceTokenID'):
                    # PostgreSQL format for Charge 365
                    query = """
                    INSERT INTO "Mapping"."TokenMapping" 
                    ("SourceTokenID", "TargetIdTagsID", "CreatedWithUID")
                    VALUES (?, ?, ?)
                    """
                    params = (row.SourceTokenID, target_id_tags_id, created_with_uid)
                elif hasattr(row, 'SourceAccessCardID'):
                    query = """
                    INSERT INTO [Mapping].[AccessCardMapping] 
                    ([SourceAccessCardID], [TargetIdTagsID], [CreatedWithUID])
                    VALUES (?, ?, ?)
                    """
                    params = (row.SourceAccessCardID, target_id_tags_id, created_with_uid)
                else:
                    query = """
                    INSERT INTO [Mapping].[KeyfobMapping] 
                    ([SourceKeyfobId], [TargetIdTagsID], [CreatedWithUID])
                    VALUES (?, ?, ?)
                    """
                    params = (row.SourceKeyfobId, target_id_tags_id, created_with_uid)

                cursor.execute(query, params)
                if cursor.rowcount == 0:
                    raise SystemExit(f"Failed to insert mapping for row: {row}. Halting execution.")
                logging.info(f"Mapping inserted for row: {row}")
            except Exception as e:
                raise SystemExit(f"SQL execution failed: {e}. Row: {row}")

def process_id_tag(conn_str, row, existing_id_tags_by_uid):
    try:
        # ── Update path: row already has a target ID ────────────────────
        if ENABLE_UPDATES and try_get_value(row, 'TargetIdTagsID', int) is not None:
            id_tag_id = try_get_value(row, 'TargetIdTagsID', int)
            update_payload = {
                "idLabel": try_get_value(row, 'idLabel', str),
                "partnerId": try_get_value(row, 'partnerId', int),
                "vehicleType": try_get_value(row, 'vehicleType', str),
                "expireAt": try_get_value(row, 'expireAt', str),
            }
            # Remove keys with None values so the API keeps existing values
            update_payload = {k: v for k, v in update_payload.items() if v is not None}
            update_id_tag(id_tag_id, update_payload)
            stats['updated'] += 1
            logging.info(f"Updated IdTag {id_tag_id} with idLabel")
            return

        tag_data = {
            "status": try_get_value(row, 'status', str),
            "type": try_get_value(row, 'type', str),
            "idTagUid": try_get_value(row, 'idTagUid', str),
            "idLabel": try_get_value(row, 'idLabel', str),
            "userId": try_get_value(row, 'userId', str),
            "externalId": try_get_value(row, 'externalId', str),
            "paymentMethodId": try_get_value(row, 'paymentMethodId', str) or "auto",
            "partnerId": try_get_value(row, 'partnerId', int),
            "vehicleType": try_get_value(row, 'vehicleType', str),
            "expireAt": try_get_value(row, 'expireAt', str),
        }
        # Remove keys with None values so the API applies its own defaults
        tag_data = {k: v for k, v in tag_data.items() if v is not None}
        uid = tag_data["idTagUid"]
        target_user_id = tag_data["userId"]
        # Ensure uid is string for normalization (guard against unexpected numeric types)
        norm_uid = normalize_uid(str(uid) if uid is not None else "")
        existing = existing_id_tags_by_uid.get(norm_uid)
        if existing:
            # Skip creation; map to existing IdTag
            target_id_tags_id = existing['id']
            # Compare user IDs to detect ownership mismatches
            existing_user_id = str(existing.get('userId', '')) if existing.get('userId') is not None else ''
            expected_user_id = str(target_user_id) if target_user_id is not None else ''
            user_id_match = (existing_user_id == expected_user_id) if expected_user_id else None
            if user_id_match is False:
                stats['user_id_mismatch'] += 1
                logging.warning(f"User ID mismatch for IdTag uid='{uid}': expected={expected_user_id}, actual={existing_user_id}")
            insert_mapping(conn_str, row, target_id_tags_id, uid,
                           idtag_created_in_ampeco=False, user_id_match=user_id_match)
            stats['existing_mapped'] += 1
            logging.info(f"Mapped existing IdTag uid='{uid}' to id={target_id_tags_id}")
        else:
            response_data = create_id_tag(tag_data)
            target_id_tags_id = response_data['id']
            insert_mapping(conn_str, row, target_id_tags_id, uid,
                           idtag_created_in_ampeco=True, user_id_match=True)
            # Update in-memory cache to avoid a second create attempt for same (case-insensitive) UID later
            existing_id_tags_by_uid[norm_uid] = response_data
            stats['created'] += 1
            logging.info(f"Created IdTag uid='{uid}' with id={target_id_tags_id}")
    except Exception as e:
        error_class = _classify_idtag_error(e)
        _record_error(error_class)
        logging.error(f"Error processing Id Tag {try_get_value(row, 'idTagUid', str)}: {str(e)}")

def main():
    try:
        conn_str = get_db_connection_string()
        id_tags = fetch_id_tags(conn_str)
        existing_id_tags_by_uid = fetch_id_tags_from_ampeco()
        logging.info(f"Existing IdTags fetched from Ampeco: {len(existing_id_tags_by_uid)}")
        total_tags = len(id_tags)
        stats['total'] = total_tags
        stats['created'] = 0
        stats['existing_mapped'] = 0
        stats['error_counts'] = {}
        for index, tag in enumerate(id_tags, start=1):
            logging.info(f"Processing Id Tag {index} of {total_tags}")
            process_id_tag(conn_str, tag, existing_id_tags_by_uid)
    except Exception as e:
        logging.error(f"An error occurred: {e}")

    # ── Summary ────────────────────────────────────────────────────────────
    total_errors = sum(stats['error_counts'].values())
    logging.info("")
    logging.info("=" * 60)
    logging.info("CreateIdTags Summary:")
    logging.info("=" * 60)
    logging.info(f"Total Id Tags to process: {stats['total']}")
    logging.info(f"Created (new):            {stats['created']}")
    logging.info(f"Mapped (existing):        {stats['existing_mapped']}")
    logging.info(f"Updated:                  {stats['updated']}")
    logging.info(f"User ID mismatches:       {stats['user_id_mismatch']}")
    logging.info(f"Errors:                   {total_errors}")
    if stats['error_counts']:
        logging.info("")
        logging.info("Errors by category:")
        for error_class, count in sorted(stats['error_counts'].items(), key=lambda x: -x[1]):
            logging.info(f"  {error_class:40s} {count}")
    logging.info("=" * 60)

if __name__ == "__main__":
    setup_logging("CreateIdTags")
    main()
