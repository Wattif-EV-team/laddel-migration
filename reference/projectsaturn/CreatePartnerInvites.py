import pyodbc
import requests
import json
import logging
from utils.config_utils import get_db_connection_string
from utils.log_utils import setup_logging
from collections import defaultdict
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import get_db_connection, get_value, get_sql_dialect, quote_identifier  # Updated import

# Initialize statistics and error list
stats = {
    'total_invites': 0,
    'invites_created': 0,
    'reinvited': 0
}
errors = []

def fetch_all_partner_invites_grouped():
    """Fetch all Partner Invites from the database and group them by TargetUserID."""
    grouped_invites = defaultdict(list)
    conn_str = get_db_connection_string()  # Fetch connection string
    if not conn_str:
        raise SystemExit("Database connection string is not configured.")
    with get_db_connection(conn_str) as conn:  # Use get_db_connection
        cursor = conn.cursor()
        dialect = get_sql_dialect(conn)
        target_schema = quote_identifier("Target", dialect)
        table = quote_identifier("PartnerInvites", dialect)
        target_user_id = quote_identifier("TargetUserID", dialect)
        target_partner_id = quote_identifier("TargetPartnerID", dialect)
        query = (
            f"SELECT DISTINCT * FROM {target_schema}.{table} "
            f"WHERE {target_partner_id} IS NOT NULL AND {target_user_id} IS NOT NULL"
        )
        cursor.execute(query)
        for row in cursor.fetchall():
            grouped_invites[row.TargetUserID].append(row)
    return grouped_invites

def fetch_user_by_id(user_id):
    """Fetch user by ID from Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/users/v1.0/{user_id}?include[0]=partnerInvites"
        return requests.get(url, headers=headers)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'fetch_user_by_id')

def create_partner_invite(invite_data):
    """Create a Partner Invite using the Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/partner-invites/v1.0"
        return requests.post(url, headers=headers, json=invite_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_partner_invite')

def delete_partner_invite(invite_id):
    """Delete a Partner Invite using the Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/partner-invites/v1.0/{invite_id}"
        return requests.delete(url, headers=headers)

    response = execute_ampeco_api_call(api_call)
    if response.status_code != 204:
        raise Exception(f"delete_partner_invite failed with status code {response.status_code}: {response.text}")

def process_user_invites(user_id, rows):
    """Process all partner invites for a single user."""
    try:
        user = fetch_user_by_id(user_id)
        partner_invites = user.get('partnerInvites', [])
        
        for row in rows:
            partner_invite = next((invite for invite in partner_invites if invite['partnerId'] == row.TargetPartnerID), None)
            
            if not partner_invite or partner_invite['status'] in ['sent', 'pending'] or \
               (partner_invite['options']['allowCorporateAccountBilling'] is False and row.options_allowCorporateAccountBilling) or \
               (partner_invite['options']['allowAccessToPrivateChargePoints'] is False and row.options_allowAccessToPrivateChargePoints):
                if partner_invite:
                    delete_partner_invite(partner_invite['id'])
                    stats['reinvited'] += 1
                
                invite_data = {
                    "partnerId": get_value(row.partnerId, int),
                    "options": {
                        "allowCorporateAccountBilling": get_value(row.options_allowCorporateAccountBilling, bool),
                        "limitCorporateAccountBillingToPartnerChargePoints": get_value(row.options_limitCorporateAccountBillingToPartnerChargePoints, bool),
                        "allowAccessToPrivateChargePoints": get_value(row.options_allowAccessToPrivateChargePoints, bool)
                    },
                    "sendViaEmail": get_value(row.sendViaEmail, bool),
                    "email": user['email'],
                    "language": get_value(row.language, str)
                }
                response_data = create_partner_invite(invite_data)
                invite_id = response_data.get('id', '?')
                logging.info(f"Created partner invite id={invite_id} for userId={user_id} partnerId={row.TargetPartnerID}")
                stats['invites_created'] += 1
    except Exception as e:
        error_message = str(e)
        logging.error(f"Error processing partner invites for user {user_id}: {error_message}")
        errors.append({'user_id': user_id, 'error': error_message})

def main():
    """Main function to fetch and process partner invites."""
    try:
        grouped_invites = fetch_all_partner_invites_grouped()
        total_users = stats['total_invites'] = len(grouped_invites)
        logging.info(f"Total users with partner invites fetched: {total_users}")

        for index, (user_id, rows) in enumerate(grouped_invites.items(), start=1):
            logging.info(f"Processing partner invites for user {user_id} ({index} of {total_users})...")
            process_user_invites(user_id, rows)

        # Print stats
        logging.info("Stats:")
        for key, value in stats.items():
            logging.info(f"{key}: {value}")

        # Print errors
        if errors:
            logging.info("Errors:")
            for error in errors:
                logging.info(f"User ID: {error['user_id']}, Error: {error['error']}")
    except Exception as e:
        logging.error(f"An error occurred: {e}")

if __name__ == "__main__":
    setup_logging("CreatePartnerInvites")
    main()
