import logging
import secrets
import urllib.parse
import pyodbc
import requests
from email_validator import validate_email, EmailNotValidError, caching_resolver
from utils.config_utils import get_db_connection_string
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import get_db_connection, get_sql_dialect, quote_identifier

# ── Configuration ───────────────────────────────────────────────────────
DRY_RUN = False  # Set to False for production — when True, no API calls or DB writes

resolver = caching_resolver(timeout=10)

# ── Statistics ──────────────────────────────────────────────────────────
stats = {
    'total': 0,
    'created': 0,
    'existing': 0,
    'skipped_cross_org': 0,
    'error_counts': {},   # error_class -> count
    'errors': [],         # (email, message) tuples for end-of-run replay
    'warnings': [],       # (email, message) tuples for end-of-run replay
}

# ── Cache: partner_id → list of admin dicts from API ───────────────────
_partner_admins_cache: dict[int, list[dict]] = {}


def _classify_email_error(error_msg) -> str:
    """Return a short, domain-free error class for an EmailNotValidError message."""
    msg = str(error_msg)
    if 'does not exist' in msg:
        return 'domain does not exist'
    if 'does not accept email' in msg:
        return 'domain does not accept email'
    if 'does not have a dot' in msg:
        return 'domain missing dot'
    if 'There is no at-sign' in msg or 'must have an @-sign' in msg:
        return 'missing @-sign'
    if 'not valid' in msg.lower():
        return 'invalid email syntax'
    return 'email validation error (other)'


def _record_error(error_class: str):
    stats['error_counts'][error_class] = stats['error_counts'].get(error_class, 0) + 1


def generate_friendly_password(segments=3, segment_length=4, separator="-"):
    """Generate a human-friendly password that avoids ambiguous characters."""
    allowed_chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghkmnpqrstuvwxyz23456789"
    password_segments = [
        "".join(secrets.choice(allowed_chars) for _ in range(segment_length))
        for _ in range(segments)
    ]
    return separator.join(password_segments)


def preprocess_email(email):
    """Clean, validate, and normalize an email address."""
    cleaned_email = ''.join(ch for ch in email if ord(ch) >= 32 and ch not in [' ', '\u00A0'])
    validated_email = validate_email(cleaned_email, check_deliverability=True, dns_resolver=resolver)
    return validated_email.normalized


def fetch_all_partner_admins(conn_str):
    """Fetch all unprocessed partner admin rows from Target view.

    Returns an empty list and logs a warning if the view does not exist
    (PostgreSQL SQLSTATE 42P01), allowing the caller to skip gracefully.
    """
    try:
        with get_db_connection(conn_str) as conn:
            dialect = get_sql_dialect(conn)
            target_schema = quote_identifier("Target", dialect)
            table = quote_identifier("PartnerAdmins", dialect)
            target_admin_id = quote_identifier("TargetPartnerAdminID", dialect)
            query = f"SELECT * FROM {target_schema}.{table} WHERE {target_admin_id} IS NULL"
            cursor = conn.cursor()
            cursor.execute(query)
            return cursor.fetchall()
    except pyodbc.ProgrammingError as e:
        if e.args and e.args[0] == "42P01":
            logging.warning("View Target.PartnerAdmins does not exist — skipping partner admins step.")
            return []
        raise


def create_partner_admin(target_partner_id, admin_data):
    """Create a partner admin via Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/partners/v2.0/{target_partner_id}/admins"
        return requests.post(url, headers=headers, json=admin_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_partner_admin')


def fetch_partner_admins(partner_id):
    """Fetch all admins for a partner from Ampeco API.
    
    Results are cached per partner_id for the duration of the script run
    to avoid redundant API calls when multiple emails share the same partner.
    """
    if partner_id in _partner_admins_cache:
        return _partner_admins_cache[partner_id]

    def api_call(base_url, headers):
        url = f"{base_url}/resources/partners/v2.0/{partner_id}/admins"
        return requests.get(url, headers=headers)

    response = execute_ampeco_api_call(api_call)
    admins = check_ampeco_response(response, 200, 'fetch_partner_admins')
    _partner_admins_cache[partner_id] = admins
    return admins


def find_existing_admin_by_email(partner_id, email):
    """Look up an existing admin by email within a specific partner.
    
    Returns the admin dict if found, None otherwise.
    Uses cached partner admin list to avoid redundant API calls.
    """
    existing_admins = fetch_partner_admins(partner_id)
    email_lower = email.lower()
    for admin in existing_admins:
        if admin.get('email', '').lower() == email_lower:
            return admin
    return None


def insert_mapping(conn_str, row, target_partner_admin_id, email, account_created_in_ampeco, one_time_password=None):
    """Insert a mapping row using row.mapping_table and row.mapping_key."""
    table_name = row.mapping_table
    with get_db_connection(conn_str) as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    f'''INSERT INTO "Mapping"."{table_name}"
                       (mapping_key, target_partner_id, target_partner_admin_id,
                        created_with_email, account_created_in_ampeco, one_time_password)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (row.mapping_key, row.target_partner_id, target_partner_admin_id,
                     email, account_created_in_ampeco, one_time_password)
                )
                if cursor.rowcount == 0:
                    raise SystemExit(f"Failed to insert mapping for {email}. Halting.")
                logging.info(f"Mapping inserted for {email} → partner_admin_id={target_partner_admin_id}")
            except Exception as e:
                raise SystemExit(f"SQL execution failed: {e}. Email: {email}, Row: {row}")


def process_admin(conn_str, row):
    """Process a single partner admin row."""
    email = row.email
    target_partner_id = row.target_partner_id
    all_partner_ids = row.all_target_partner_ids

    # 1. Cross-org conflict — target_partner_id is NULL
    if target_partner_id is None:
        msg = (
            f"Cannot create admin for {email}: tied to multiple org groups "
            f"with partner IDs {all_partner_ids}. Manual resolution required."
        )
        logging.error(msg)
        stats['errors'].append((email, msg))
        _record_error('cross-org conflict')
        stats['skipped_cross_org'] += 1
        return

    # 2. Validate email
    try:
        normalized_email = preprocess_email(email)
    except EmailNotValidError as e:
        error_class = _classify_email_error(e)
        _record_error(error_class)
        msg = f"Invalid email {email}: {e}"
        logging.error(msg)
        stats['errors'].append((email, msg))
        return

    # 3. Check for existing admin on this partner before creating
    if not DRY_RUN:
        existing = find_existing_admin_by_email(target_partner_id, normalized_email)
        if existing:
            admin_id = existing['id']
            logging.info(
                f"Found existing partner admin {normalized_email} → id={admin_id} "
                f"on partner {target_partner_id}"
            )
            insert_mapping(conn_str, row, admin_id, normalized_email, False)
            stats['existing'] += 1
            return

    # 4. Generate password
    password = generate_friendly_password()

    # 5. Build API payload
    admin_data = {
        "name": row.name,
        "email": normalized_email,
        "password": password,
        "passwordConfirmation": password,
        "adminType": row.adminType,
        "roleId": row.roleId,
        "locale": row.locale,
    }

    if DRY_RUN:
        logging.info(f"[DRY RUN] Would create admin for {normalized_email} on partner {target_partner_id}: {admin_data['name']}")
        stats['created'] += 1
        return

    # 6. Call API
    try:
        result = create_partner_admin(target_partner_id, admin_data)
        if not result or 'id' not in result:
            raise RuntimeError(f"API returned unexpected result for {normalized_email}: {result}")
        admin_id = result['id']
        logging.info(f"Created partner admin {normalized_email} → id={admin_id} on partner {target_partner_id}")

        # 7. Insert mapping
        insert_mapping(conn_str, row, admin_id, normalized_email, True, password)
        stats['created'] += 1

    except Exception as e:
        error_msg = str(e)
        if '422' in error_msg and 'already been taken' in error_msg:
            _record_error('email taken (other partner)')
            msg = (
                f"Admin {normalized_email} exists in Ampeco but not on partner {target_partner_id} — "
                f"likely assigned to another partner. Manual resolution required."
            )
            logging.warning(msg)
            stats['warnings'].append((normalized_email, msg))
        elif '422' in error_msg:
            _record_error('validation error (422)')
            msg = f"Error creating admin {normalized_email} on partner {target_partner_id}: {e}"
            logging.error(msg)
            stats['errors'].append((normalized_email, msg))
        else:
            _record_error('api/other error')
            msg = f"Error creating admin {normalized_email} on partner {target_partner_id}: {e}"
            logging.error(msg)
            stats['errors'].append((normalized_email, msg))


def main():
    conn_str = get_db_connection_string()

    if DRY_RUN:
        logging.info("=" * 60)
        logging.info("DRY RUN MODE — no API calls or database writes")
        logging.info("=" * 60)

    admins = fetch_all_partner_admins(conn_str)
    if not admins:
        logging.info("No partner admins to process.")
        return

    total = len(admins)
    stats['total'] = total
    stats['created'] = 0
    stats['existing'] = 0
    stats['skipped_cross_org'] = 0
    stats['error_counts'] = {}
    stats['errors'] = []
    stats['warnings'] = []

    logging.info(f"Found {total} partner admin(s) to process")

    for index, admin in enumerate(admins, start=1):
        logging.info(f"Processing {index}/{total}: {admin.email}")
        process_admin(conn_str, admin)

    # ── Summary ────────────────────────────────────────────────────────
    total_errors = sum(stats['error_counts'].values())
    logging.info("")
    logging.info("=" * 60)
    logging.info("CreatePartnerAdmins Summary:")
    logging.info("=" * 60)
    logging.info(f"Total to process:       {stats['total']}")
    logging.info(f"Created:                {stats['created']}" + (" (dry run)" if DRY_RUN else ""))
    logging.info(f"Found existing:         {stats['existing']}")
    logging.info(f"Skipped (cross-org):    {stats['skipped_cross_org']}")
    logging.info(f"Errors:                 {total_errors}")
    if stats['error_counts']:
        logging.info("")
        logging.info("Errors by category:")
        for error_class, count in sorted(stats['error_counts'].items(), key=lambda x: -x[1]):
            logging.info(f"  {error_class:40s} {count}")

    if stats['warnings']:
        logging.info("")
        logging.info("Warnings:")
        for email, msg in stats['warnings']:
            logging.warning(f"  {email}: {msg}")

    if stats['errors']:
        logging.info("")
        logging.info("Errors:")
        for email, msg in stats['errors']:
            logging.error(f"  {email}: {msg}")

    logging.info("=" * 60)


if __name__ == "__main__":
    setup_logging("CreatePartnerAdmins")
    main()
