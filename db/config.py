# db/config.py
"""
Database configuration — loads from .env at project root.
All settings are configurable via environment variables.
"""
import os
from pathlib import Path

# Load .env manually (no python-dotenv dependency for db layer)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data")))

# Turso operational
TURSO_URL = os.getenv("TURSO_URL", "").strip()
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()
TURSO_LOCAL_PATH = os.getenv("TURSO_LOCAL_PATH", str(DATA_DIR / "operational.db"))

# Cloud (Snowflake — real connection, no mock)
SNOWFLAKE_ACCOUNT = os.getenv("SNOWFLAKE_ACCOUNT", "").strip()
SNOWFLAKE_USER = os.getenv("SNOWFLAKE_USER", "").strip()
SNOWFLAKE_PRIVATE_KEY_PATH = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH", "").strip()
SNOWFLAKE_WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE", "").strip()
SNOWFLAKE_DATABASE = os.getenv("SNOWFLAKE_DATABASE", "").strip()
SNOWFLAKE_SCHEMA = os.getenv("SNOWFLAKE_SCHEMA", "").strip()

# Telemetry
TELEMETRY_DB_PATH = os.getenv("TELEMETRY_DB_PATH", str(DATA_DIR / "telemetry.db"))

# Artifacts
ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", str(DATA_DIR / "artifacts"))

# Retry
CLOUD_RETRY_ATTEMPTS = int(os.getenv("CLOUD_RETRY_ATTEMPTS", "5"))
CLOUD_RETRY_BASE_WAIT = float(os.getenv("CLOUD_RETRY_BASE_WAIT", "1.0"))

# Schema validation
SCHEMA_BACKUP_SUFFIX = os.getenv("SCHEMA_BACKUP_SUFFIX", "_backup")

# Long polling
LONG_POLL_TIMEOUT = int(os.getenv("LONG_POLL_TIMEOUT", "3600"))

# Content preview
CONTENT_PREVIEW_LENGTH = int(os.getenv("CONTENT_PREVIEW_LENGTH", "200"))
