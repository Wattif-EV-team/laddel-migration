import os
import pyodbc
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def get_db_connection_string():
    """Returns the database connection string."""
    return os.getenv('SQL_SERVER_CONN_STR')

def get_sender_email():
    """Returns the sender email."""
    return os.getenv('sender_email')

def get_api_base_url():
    """Returns the base URL for the API."""
    return os.getenv('API_URL_BASE')

def get_api_token():
    """Returns the API token."""
    return os.getenv('API_TOKEN')

def get_aws_config_from_env():
    """Returns AWS configuration from environment variables."""
    aws_config = {
        'AWS_ACCESS_KEY': os.getenv('AWS_ACCESS_KEY'),
        'AWS_SECRET_KEY': os.getenv('AWS_SECRET_KEY'),
        'AWS_REGION': os.getenv('AWS_REGION')
    }
    return aws_config

def get_smartcharge_credentials():
    """Returns the username, password, appID, and appToken."""
    return {
        'username': os.getenv('smartcharge_username'),
        'password': os.getenv('smartcharge_password'),
        'appID': os.getenv('smartcharge_appID'),
        'appToken': os.getenv('smartcharge_appToken')
    }

def get_cache_folder_path():
    """Returns the root path to the cache folder."""
    return os.getenv('DATA_CACHE_PATH')
