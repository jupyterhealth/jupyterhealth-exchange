"""
Configuration management for JHE Universal MCP Server
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# JupyterHealth Exchange Configuration
JHE_BASE_URL = os.getenv("JHE_BASE_URL", "https://jhe.fly.dev")
JHE_AUTHORIZE_URL = f"{JHE_BASE_URL}/o/authorize/"
JHE_TOKEN_URL = f"{JHE_BASE_URL}/o/token/"
JHE_API_BASE = f"{JHE_BASE_URL}/api/v1"

# OAuth Client Configuration
CLIENT_ID = os.getenv("JHE_CLIENT_ID", "jhe-universal-mcp")
CLIENT_SECRET = os.getenv("JHE_CLIENT_SECRET", "")
OIDC_RSA_PRIVATE_KEY = os.getenv("OIDC_RSA_PRIVATE_KEY", "")

# Local callback server configuration
CALLBACK_PORT = int(os.getenv("CALLBACK_PORT", "8765"))
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"

# Token cache configuration
TOKEN_CACHE_DIR = Path(os.getenv("TOKEN_CACHE_DIR", "~/.jhe_mcp")).expanduser()
TOKEN_CACHE_PATH = TOKEN_CACHE_DIR / "token_cache.json"

# Database connection
DB_CONN = os.getenv("JHE_DB_CONN")

# Ensure token cache directory exists
TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def validate_config():
    """Validate required configuration is present"""
    errors = []

    if not CLIENT_ID:
        errors.append("JHE_CLIENT_ID is required")

    # Either CLIENT_SECRET or OIDC_RSA_PRIVATE_KEY must be provided
    if not CLIENT_SECRET and not OIDC_RSA_PRIVATE_KEY:
        errors.append("Either JHE_CLIENT_SECRET or OIDC_RSA_PRIVATE_KEY is required")

    if not DB_CONN:
        errors.append("JHE_DB_CONN is required")

    if errors:
        raise ValueError(f"Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))

    return True
