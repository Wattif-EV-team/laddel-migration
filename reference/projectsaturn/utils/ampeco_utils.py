import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from utils.config_utils import get_api_base_url, get_api_token

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def execute_ampeco_api_call(api_call_lambda):
    """Executes an Ampeco API call with retry logic for network errors."""
    base_url = get_api_base_url()
    headers = {
        'accept': 'application/json',
        'authorization': f'Bearer {get_api_token()}',
        'content-type': 'application/json'
    }
    return api_call_lambda(base_url, headers)

def check_ampeco_response(response, expected_status_code, method_name, expect_body=True):
    """Checks the response for errors and raises an exception if necessary."""
    if response.status_code != expected_status_code:
        error_message = extract_error_message(response)
        raise Exception(f"{method_name} failed with status code {response.status_code}: {error_message}")

    if not expect_body:
        return None

    try:
        body = response.json()
    except ValueError as exc:
        raise Exception(f"{method_name} expected a JSON body but none was returned") from exc

    return body.get('data')

def extract_error_message(response):
    """Extracts the error message from the response."""
    try:
        return response.json().get('message', response.text)
    except ValueError:
        return response.text

