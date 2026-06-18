import logging
import sys
import os
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ANSI color codes for console output
class LogColors:
    RESET = '\033[0m'
    RED = '\033[91m'        # ERROR
    YELLOW = '\033[93m'     # WARNING
    BLUE = '\033[94m'       # INFO
    CYAN = '\033[96m'       # DEBUG
    GREEN = '\033[92m'      # (optional for success messages)
    
class ColoredConsoleFormatter(logging.Formatter):
    """Custom formatter that adds colors based on log level and strips extra info"""
    
    COLORS = {
        logging.DEBUG: LogColors.CYAN,
        logging.INFO: LogColors.BLUE,
        logging.WARNING: LogColors.YELLOW,
        logging.ERROR: LogColors.RED,
        logging.CRITICAL: LogColors.RED,
    }
    
    def format(self, record):
        # Get color for this log level
        color = self.COLORS.get(record.levelno, LogColors.RESET)
        # Simple format: just the message with color
        return f"{color}{record.getMessage()}{LogColors.RESET}"

def setup_logging(script_name):
    # Create a folder per day using yyyy-mm-dd format
    log_folder = os.path.join(os.path.dirname(__file__), '..', 'log', datetime.now().strftime('%Y-%m-%d'))
    os.makedirs(log_folder, exist_ok=True)
    
    # Use ScriptName.HHmm.log file name
    timestamp = datetime.now().strftime('%H%M')
    log_filename = f"{script_name}.{timestamp}.log"
    log_filepath = os.path.join(log_folder, log_filename)
    
    # Add a header with date and selected .env values
    cli_args = ' '.join(sys.argv[1:]) if len(sys.argv) > 1 else '(none)'
    header = (
        r"""
     __      __         __    __  .__  _____  
    /  \    /  \_____ _/  |__/  |_|__|/ ____\ 
    \   \/\/   /\__  \\   __\   __\  \   __\  
     \        /  / __ \|  |  |  | |  ||  |    
      \__/\  /  (____  /__|  |__| |__||__|    
           \/        \/     Migration Tool    
""" + f"""
Script: {script_name}
Args: {cli_args}
Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
DATA_CACHE_PATH: {os.getenv('DATA_CACHE_PATH')}
SQL_SERVER_CONN_STR: {os.getenv('SQL_SERVER_CONN_STR').split('PWD=')[0] if os.getenv('SQL_SERVER_CONN_STR') else ''}
API_URL_BASE: {os.getenv('API_URL_BASE')}
smartcharge_username: {os.getenv('smartcharge_username')}
""")
    with open(log_filepath, 'w') as log_file:
        log_file.write(header + '\n')
    # Output header to console as well
    print(header)

    # Force UTF-8 encoding on console output for Windows
    if sys.stdout.encoding != 'utf-8':
        try:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        except Exception:
            # Fallback: just continue with default encoding
            pass

    # Create formatters
    # File format: timestamp - filename:lineno - funcName - level - message
    file_formatter = logging.Formatter(
        "%(asctime)s - %(filename)s:%(lineno)d - %(funcName)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S"
    )
    
    # Console format: just the message with colors
    console_formatter = ColoredConsoleFormatter()
    
    # Create handlers
    file_handler = logging.FileHandler(log_filepath, encoding='utf-8')
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)  # Log everything to file
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)  # Only INFO and above to console
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture all levels
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # ── Silence noisy third-party loggers ─────────────────────────────
    # We only want DEBUG from our own code; third-party libraries should
    # only log WARNING and above (so retries and failures still show).
    _noisy_loggers = [
        # HTTP connection pools (urllib3 / requests)
        "urllib3",
        "urllib3.connectionpool",
        "requests",
        # httpx / httpcore trace output
        "httpx",
        "httpcore",
        "httpcore.http2",
        "httpcore.connection",
        # hpack (HTTP/2 header encoding)
        "hpack",
        "hpack.hpack",
        "hpack.table",
        # Azure Identity / MSAL
        "azure",
        "azure.identity",
        "azure.core.pipeline.policies.http_logging_policy",
        "msal",
        # Microsoft Graph SDK
        "msgraph",
        "msgraph_core",
        "kiota_http",
        "kiota_authentication_azure",
        # asyncio internals
        "asyncio",
    ]
    for logger_name in _noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
