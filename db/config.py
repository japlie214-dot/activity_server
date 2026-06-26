# db/config.py
"""
Database configuration — loads from .env at project root.
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
DATA_DIR = PROJECT_ROOT / "data"

# Turso operational
TURSO_URL = os.getenv("TURSO_URL", "").strip()
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()
# If no remote URL, use local file
TURSO_LOCAL_PATH = str(DATA_DIR / "operational.db")

# Cloud (Snowflake mock)
CLOUD_DB_PATH = os.getenv("CLOUD_DB_PATH", str(DATA_DIR / "cloud_snowflake.db"))

# Telemetry
TELEMETRY_DB_PATH = os.getenv("TELEMETRY_DB_PATH", str(DATA_DIR / "telemetry.db"))

# Artifacts
ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", str(DATA_DIR / "artifacts"))

# Retry
CLOUD_RETRY_ATTEMPTS = int(os.getenv("CLOUD_RETRY_ATTEMPTS", "5"))
CLOUD_RETRY_BASE_WAIT = float(os.getenv("CLOUD_RETRY_BASE_WAIT", "1.0"))
