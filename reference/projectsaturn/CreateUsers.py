import pyodbc
import requests
import json
import logging
import urllib.parse
import uuid
import secrets
import string
import time
from tenacity import retry, stop_after_attempt, wait_exponential
from utils.config_utils import get_db_connection_string, get_api_base_url, get_api_token
from email_validator import validate_email, EmailNotValidError, caching_resolver
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import try_get_value, get_db_connection, get_sql_dialect, quote_identifier

# Get configuration
conn_str = get_db_connection_string()

resolver = caching_resolver(timeout=10)

# ── Feature flags ───────────────────────────────────────────────────────
ENABLE_UPDATES = False

# ── Statistics ──────────────────────────────────────────────────────────
stats = {
    'total': 0,
    'created': 0,
    'existing': 0,
    'updated': 0,
    'error_counts': {},   # error_class -> count
}

def _classify_email_error(error_msg: str) -> str:
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

def handle_api_error(response, method_name):
    try:
        error_message = response.json().get('message', 'No error message provided')
    except ValueError:
        error_message = 'No error message provided'
    raise Exception(f"{method_name} failed with status code {response.status_code}: {error_message}")

def fetch_all_users(conn_str):
    with get_db_connection(conn_str) as conn:
        dialect = get_sql_dialect(conn)
        target_schema = quote_identifier("Target", dialect)
        users_table = quote_identifier("Users", dialect)
        target_user_id = quote_identifier("TargetUserID", dialect)
        
        if ENABLE_UPDATES:
            query = f"SELECT * FROM {target_schema}.{users_table}"
        else:
            query = f"SELECT * FROM {target_schema}.{users_table} WHERE {target_user_id} IS NULL"
        
        cursor = conn.cursor()
        cursor.execute(query)
        return cursor.fetchall()

def fetch_user_by_email(email):
    """Fetch user by email from Ampeco API."""
    def api_call(base_url, headers):
        encoded_email = urllib.parse.quote(email)
        url = f"{base_url}/resources/users/v1.0?filter[email]={encoded_email}"
        return requests.get(url, headers=headers)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'fetch_user_by_email')

def create_user(user_data):
    """Create a user in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/users/v1.0"
        return requests.post(url, headers=headers, json=user_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_user')

def update_user(user_id, user_data):
    """Update an existing user in Ampeco API via PATCH."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/users/v1.0/{user_id}"
        return requests.patch(url, headers=headers, json=user_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_user')

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def execute_query_with_retry(cursor, query, params):
    cursor.execute(query, params)
    return cursor.rowcount

def insert_mapping(conn_str, row, email, target_user_id, account_created_in_ampeco, one_time_password=None):
    with get_db_connection(conn_str) as conn:
        with conn.cursor() as cursor:
            try:
                # Generic mapping_table/mapping_key pattern (key-based migrations like Project Sleet)
                if hasattr(row, 'mapping_table') and row.mapping_table:
                    table_name = row.mapping_table  # e.g., "user_mapping"
                    query = f'''
                    INSERT INTO "Mapping"."{table_name}" 
                    (mapping_key, target_user_id, created_with_email, 
                    account_created_in_ampeco, one_time_password)
                    VALUES (?, ?, ?, ?, ?)
                    '''
                    params = (
                        row.mapping_key,
                        target_user_id, 
                        email, 
                        account_created_in_ampeco, 
                        one_time_password
                    )
                # Charge 365 CPMS - identified by presence of both SourceAccountID and SourceUserID
                elif hasattr(row, 'SourceAccountID') and hasattr(row, 'SourceUserID'):
                    # PostgreSQL format for Charge 365 CPMS
                    query = """
                    INSERT INTO "Mapping"."UserMapping" 
                    ("SourceAccountID", "SourceUserID", "CreatedWithEmail", "TargetUserID", 
                    "AccountCreatedInAmpeco", "OneTimePassword")
                    VALUES (?, ?, ?, ?, ?, ?)
                    """
                    params = (
                        row.SourceAccountID, 
                        row.SourceUserID, 
                        email, 
                        target_user_id, 
                        account_created_in_ampeco, 
                        one_time_password
                    )
                # Current CPMS - identified by presence of SourceCustomerID
                elif hasattr(row, 'SourceCustomerID'):
                    # MS SQL format for Current CPMS
                    query = """
                    INSERT INTO [Mapping].[CustomerMapping] 
                    ([SourceCustomerID], [CreatedWithEmail], [TargetUserID], [AccountCreatedInAmpeco], [OneTimePassword])
                    VALUES (?, ?, ?, ?, ?)
                    """
                    params = (row.SourceCustomerID, email, target_user_id, account_created_in_ampeco, one_time_password)
                # EV Connect CPMS - identified by presence of SourceUserId
                else:
                    # MS SQL format for EV Connect CPMS
                    query = """
                    INSERT INTO [Mapping].[UserMapping] 
                    ([SourceUserId], [CreatedWithEmail], [TargetUserID], [AccountCreatedInAmpeco], [OneTimePassword])
                    VALUES (?, ?, ?, ?, ?)
                    """
                    params = (row.SourceUserId, email, target_user_id, account_created_in_ampeco, one_time_password)

                cursor.execute(query, params)
                if cursor.rowcount == 0:
                    raise SystemExit(f"Failed to insert mapping for email {email}. Halting execution.")
                logging.info(f"Mapping inserted for email: {email}")
            except Exception as e:
                raise SystemExit(f"SQL execution failed: {e}. Email: {email}, Row: {row}")

def generate_friendly_password(segments=3, segment_length=4, separator="-"):
    """
    Generate a human-friendly password that avoids ambiguous characters.
    
    Parameters:
    - segments (int): Number of segments in the password.
    - segment_length (int): Length of each segment.
    - separator (str): Character used to separate segments.
    
    Returns:
    - str: Secure but easy-to-read password.
    """
    allowed_chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghkmnpqrstuvwxyz23456789"  # No 0, O, o, 1, I, l
    password_segments = [
        "".join(secrets.choice(allowed_chars) for _ in range(segment_length))
        for _ in range(segments)
    ]
    
    return separator.join(password_segments)

def preprocess_email(email):
    """
    Preprocess the email by removing control characters, spaces, and no-break spaces,
    then validate and normalize the email address.
    
    Parameters:
    - email (str): The email address to preprocess.
    
    Returns:
    - str: The normalized email address.
    
    Raises:
    - EmailNotValidError: If the email address is not valid.
    """
    cleaned_email = ''.join(ch for ch in email if ord(ch) >= 32 and ch not in [' ', '\u00A0'])
    validated_email = validate_email(cleaned_email, check_deliverability=True, dns_resolver=resolver)
    return validated_email.normalized

def process_user(conn_str, row):
    try:
        # ── Update path: row already has a target ID ────────────────────
        if ENABLE_UPDATES and try_get_value(row, 'TargetUserID', int) is not None:
            user_id = try_get_value(row, 'TargetUserID', int)
            update_payload = {
                "options": {
                    "sessionsAllowed": try_get_value(row, 'options_sessionsAllowed', str, "single_session")
                }
            }
            update_user(user_id, update_payload)
            stats['updated'] += 1
            logging.info(f"Updated user {user_id} with sessionsAllowed")
            return

        normalized_email = preprocess_email(row.email)
        api_users = fetch_user_by_email(normalized_email)
        api_user = api_users[0] if api_users else None

        if api_user:
            logging.info(f"Mapping added for existing user with email: {normalized_email}, ID: {api_user['id']}")
            insert_mapping(conn_str, row, normalized_email, api_user['id'], False)
            stats['existing'] += 1
        else:
            password = generate_friendly_password()
            user_data = {
                "email": normalized_email,
                "password": password,
                "emailVerified": try_get_value(row, 'emailVerified', str),
                "requirePasswordReset": try_get_value(row, 'requirePasswordReset', bool),
                "first_name": try_get_value(row, 'first_name', str, normalized_email, clean=True),
                "middle_name": try_get_value(row, 'middle_name', str, clean=True),
                "last_name": try_get_value(row, 'last_name', str, '-', clean=True),
                "phone": try_get_value(row, 'phone', str),
                "country": try_get_value(row, 'country', str),
                "city": try_get_value(row, 'city', str, "-", clean=True),
                "post_code": try_get_value(row, 'post_code', str, "-", clean=True),
                "address": try_get_value(row, 'address', str, clean=True),
                # "vehicle_no": try_get_value(row, 'vehicle_no', str),
                # "personal_id": try_get_value(row, 'personal_id', str),
                "externalId": try_get_value(row, 'externalId', str),
                "options": {
                    "sessionsAllowed": try_get_value(row, 'options_sessionsAllowed', str, "single_session")
                },
                "receiveNewsAndPromotions": try_get_value(row, 'receiveNewsAndPromotions', bool, default=False),
            }
            api_user = create_user(user_data)
            logging.info(f"User created with email: {normalized_email}, ID: {api_user['id']}")
            insert_mapping(conn_str, row, normalized_email, api_user['id'], True, password)
            stats['created'] += 1
    except EmailNotValidError as e:
        error_class = _classify_email_error(e)
        _record_error(error_class)
        logging.error(f"Invalid email for user {row.email}: {str(e)}")
    except Exception as e:
        _record_error('api/other error')
        logging.error(f"Error processing user {row.email}: {str(e)}")

def main():
    try:
        conn_str = get_db_connection_string()
        users = fetch_all_users(conn_str)
        total_users = len(users)
        stats['total'] = total_users
        stats['created'] = 0
        stats['existing'] = 0
        stats['error_counts'] = {}
        for index, user in enumerate(users, start=1):
            logging.info(f"Processing user {index} of {total_users}")
            process_user(conn_str, user)
    except Exception as e:
        logging.error(f"An error occurred: {e}")

    # ── Summary ────────────────────────────────────────────────────────────
    total_errors = sum(stats['error_counts'].values())
    logging.info("")
    logging.info("=" * 60)
    logging.info("CreateUsers Summary:")
    logging.info("=" * 60)
    logging.info(f"Total users to process: {stats['total']}")
    logging.info(f"Created (new):          {stats['created']}")
    logging.info(f"Mapped (existing):      {stats['existing']}")
    logging.info(f"Updated:                {stats['updated']}")
    logging.info(f"Errors:                 {total_errors}")
    if stats['error_counts']:
        logging.info("")
        logging.info("Errors by category:")
        for error_class, count in sorted(stats['error_counts'].items(), key=lambda x: -x[1]):
            logging.info(f"  {error_class:40s} {count}")
    logging.info("=" * 60)

if __name__ == "__main__":
    setup_logging("CreateUsers")
    main()
