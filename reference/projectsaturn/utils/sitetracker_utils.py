import os
import json
import logging
from urllib.parse import quote as urlquote

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dotenv import load_dotenv

load_dotenv()

# Module-level token cache
_token_cache = {"access_token": None}

API_VERSION = "v63.0"


def _get_env(name):
    """Get a required environment variable or raise."""
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def get_sitetracker_token():
    """Authenticate via OAuth2 password grant. Caches token for session."""
    if _token_cache["access_token"]:
        return _token_cache["access_token"]

    token_url = _get_env("SITETRACKER_TOKEN_URL")
    payload = {
        "grant_type": "password",
        "client_id": _get_env("SITETRACKER_CLIENT_ID"),
        "client_secret": _get_env("SITETRACKER_CLIENT_SECRET"),
        "username": _get_env("SITETRACKER_USERNAME"),
        "password": _get_env("SITETRACKER_PASSWORD"),
    }
    resp = requests.post(token_url, data=payload)
    if resp.status_code != 200:
        raise Exception(f"SiteTracker auth failed ({resp.status_code}): {resp.text}")

    token_data = resp.json()
    _token_cache["access_token"] = token_data["access_token"]
    logging.info(f"SiteTracker authenticated as {payload['username']}")
    return _token_cache["access_token"]


def invalidate_token():
    """Clear cached token (e.g., on 401)."""
    _token_cache["access_token"] = None


def get_sitetracker_headers():
    """Get auth headers for SiteTracker API calls."""
    return {
        "Authorization": f"Bearer {get_sitetracker_token()}",
        "Content-Type": "application/json",
    }


def _get_instance_url():
    return _get_env("SITETRACKER_INSTANCE_URL")


def _base_url():
    return f"{_get_instance_url()}/services/data/{API_VERSION}"


# ── SOQL ─────────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type(requests.exceptions.ConnectionError))
def sitetracker_soql_query(soql):
    """Run a SOQL query and return parsed JSON response."""
    url = f"{_base_url()}/query?q={urlquote(soql, safe='+,')}"
    resp = requests.get(url, headers=get_sitetracker_headers())
    if resp.status_code == 401:
        invalidate_token()
        resp = requests.get(url, headers=get_sitetracker_headers())
    resp.raise_for_status()
    return resp.json()


def sitetracker_soql_query_all(soql):
    """Run a SOQL query and paginate through all results."""
    result = sitetracker_soql_query(soql)
    records = result.get("records", [])

    while not result.get("done", True):
        next_url = f"{_get_instance_url()}{result['nextRecordsUrl']}"
        resp = requests.get(next_url, headers=get_sitetracker_headers())
        resp.raise_for_status()
        result = resp.json()
        records.extend(result.get("records", []))

    return records


# ── CRUD ─────────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type(requests.exceptions.ConnectionError))
def sitetracker_create(sobject, payload):
    """POST a new record. Returns response dict with 'id' and 'success'."""
    url = f"{_base_url()}/sobjects/{sobject}/"
    resp = requests.post(url, headers=get_sitetracker_headers(), json=payload)
    if resp.status_code == 401:
        invalidate_token()
        resp = requests.post(url, headers=get_sitetracker_headers(), json=payload)
    if resp.status_code not in (200, 201):
        raise Exception(f"SiteTracker CREATE {sobject} failed ({resp.status_code}): {resp.text}")
    return resp.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type(requests.exceptions.ConnectionError))
def sitetracker_update(sobject, record_id, payload):
    """PATCH an existing record. Returns None on success (204)."""
    url = f"{_base_url()}/sobjects/{sobject}/{record_id}"
    resp = requests.patch(url, headers=get_sitetracker_headers(), json=payload)
    if resp.status_code == 401:
        invalidate_token()
        resp = requests.patch(url, headers=get_sitetracker_headers(), json=payload)
    if resp.status_code != 204:
        raise Exception(f"SiteTracker UPDATE {sobject}/{record_id} failed ({resp.status_code}): {resp.text}")
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type(requests.exceptions.ConnectionError))
def sitetracker_read(sobject, record_id):
    """GET a single record by ID."""
    url = f"{_base_url()}/sobjects/{sobject}/{record_id}"
    resp = requests.get(url, headers=get_sitetracker_headers())
    if resp.status_code == 401:
        invalidate_token()
        resp = requests.get(url, headers=get_sitetracker_headers())
    resp.raise_for_status()
    return resp.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type(requests.exceptions.ConnectionError))
def sitetracker_delete(sobject, record_id):
    """DELETE a record by ID. Returns None on success (204). Raises on failure."""
    url = f"{_base_url()}/sobjects/{sobject}/{record_id}"
    resp = requests.delete(url, headers=get_sitetracker_headers())
    if resp.status_code == 401:
        invalidate_token()
        resp = requests.delete(url, headers=get_sitetracker_headers())
    if resp.status_code == 404:
        logging.warning(f"SiteTracker DELETE {sobject}/{record_id}: already deleted (404)")
        return None
    if resp.status_code != 204:
        raise Exception(f"SiteTracker DELETE {sobject}/{record_id} failed ({resp.status_code}): {resp.text}")
    return None


# ── Dedup / Lookup Helpers ───────────────────────────────────────────────────

def normalize_org_number(raw):
    """Strip spaces, dashes, dots from org number. Return None if empty/invalid."""
    if not raw:
        return None
    cleaned = raw.replace(" ", "").replace("-", "").replace(".", "").strip()
    return cleaned if cleaned else None


def escape_soql(value):
    """Escape single quotes for SOQL WHERE clauses."""
    if value is None:
        return ""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_site_by_project_code(project_code):
    """Find a Site by Site_ID__c. Returns record dict or None."""
    soql = (
        f"SELECT Id, Name, Site_ID__c, sitetracker__Site_Status__c, Owner_Type__c "
        f"FROM sitetracker__Site__c "
        f"WHERE Site_ID__c = '{escape_soql(project_code)}' LIMIT 1"
    )
    result = sitetracker_soql_query(soql)
    if result["totalSize"] == 0:
        return None
    return result["records"][0]


def find_account_by_org_number(org_number):
    """Find an Account by Business_Registration_Number__c.
    
    Tries the normalized value first. If not found, also tries common formatted
    variants (with spaces, e.g. '123 456 789') since pre-existing records in
    SiteTracker may have been entered in non-normalized form.
    """
    normalized = normalize_org_number(org_number)
    if not normalized:
        return None

    # Build list of variants to search: normalized, then formatted with spaces
    variants = [normalized]
    if len(normalized) == 9 and normalized.isdigit():
        # Norwegian org number: try "XXX XXX XXX" format
        variants.append(f"{normalized[:3]} {normalized[3:6]} {normalized[6:]}")

    for variant in variants:
        soql = (
            f"SELECT Id, Name, Business_Registration_Number__c, Type "
            f"FROM Account "
            f"WHERE Business_Registration_Number__c = '{escape_soql(variant)}' LIMIT 1"
        )
        result = sitetracker_soql_query(soql)
        if result["totalSize"] > 0:
            return result["records"][0]

    return None


def find_account_by_name(name):
    """Find an Account by exact Name (SOQL is case-insensitive). Returns record or None."""
    if not name:
        return None
    soql = (
        f"SELECT Id, Name, Business_Registration_Number__c, Type "
        f"FROM Account "
        f"WHERE Name = '{escape_soql(name.strip())}' LIMIT 1"
    )
    result = sitetracker_soql_query(soql)
    if result["totalSize"] == 0:
        return None
    return result["records"][0]


def find_site_relation(site_id, account_id, role):
    """Find a Site Relation by Site + Account + Role triple. Returns record or None."""
    soql = (
        f"SELECT Id, Site__c, Company__c, Site_Relation_Role__c, "
        f"Company__r.Name, Company__r.Business_Registration_Number__c "
        f"FROM Site_Relation__c "
        f"WHERE Site__c = '{escape_soql(site_id)}' "
        f"AND Company__c = '{escape_soql(account_id)}' "
        f"AND Site_Relation_Role__c = '{escape_soql(role)}' LIMIT 1"
    )
    result = sitetracker_soql_query(soql)
    if result["totalSize"] == 0:
        return None
    return result["records"][0]


def find_site_relations_by_role(site_id, role):
    """Find all Site Relations for a Site with a given role. Returns list of records."""
    soql = (
        f"SELECT Id, Site__c, Company__c, Site_Relation_Role__c, "
        f"Company__r.Name, Company__r.Business_Registration_Number__c "
        f"FROM Site_Relation__c "
        f"WHERE Site__c = '{escape_soql(site_id)}' "
        f"AND Site_Relation_Role__c = '{escape_soql(role)}'"
    )
    result = sitetracker_soql_query(soql)
    return result.get("records", [])


def snapshot_record(sobject, record_id):
    """Read a full record for before-state logging. Returns dict or None on error."""
    try:
        record = sitetracker_read(sobject, record_id)
        # Remove Salesforce metadata
        record.pop("attributes", None)
        return record
    except Exception as e:
        logging.warning(f"Failed to snapshot {sobject}/{record_id}: {e}")
        return None


def log_field_diffs(label, snapshot, payload):
    """Compare snapshot (before) vs payload (new) and log per-field diffs as WARNING.
    
    Returns the number of fields that differ.
    """
    if not snapshot:
        return 0

    diff_count = 0
    for field, new_value in payload.items():
        old_value = snapshot.get(field)
        # Normalize comparison: treat None and empty string as equivalent
        old_norm = old_value if old_value not in (None, "") else None
        new_norm = new_value if new_value not in (None, "") else None
        # Also normalize numeric types for comparison
        if old_norm is not None and new_norm is not None:
            try:
                if float(old_norm) == float(new_norm):
                    continue
            except (TypeError, ValueError):
                pass
        if str(old_norm) != str(new_norm):
            diff_count += 1
            logging.warning(f"    {label} field {field}: '{old_value}' → '{new_value}'")

    return diff_count


def find_site_by_name(name):
    """Find a Site by Name. Returns record dict or None."""
    if not name:
        return None
    soql = (
        f"SELECT Id, Name, Site_ID__c, sitetracker__Site_Status__c, Owner_Type__c "
        f"FROM sitetracker__Site__c "
        f"WHERE Name = '{escape_soql(name.strip())}' LIMIT 1"
    )
    result = sitetracker_soql_query(soql)
    if result["totalSize"] == 0:
        return None
    return result["records"][0]
