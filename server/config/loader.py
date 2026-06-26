# server/config/loader.py
"""
Server configuration — loads from .env, exposes typed settings.
"""
import os
import sys
from pathlib import Path

_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

HOST = os.getenv("SERVER_HOST", "0.0.0.0")
PORT = int(os.getenv("SERVER_PORT", "8080"))

# Platform: explicit from .env, or auto-detect
_raw_platform = os.getenv("SERVER_PLATFORM", "").strip().lower()
if _raw_platform in ("windows", "win", "win32"):
    PLATFORM = "windows"
elif _raw_platform in ("linux", "posix"):
    PLATFORM = "linux"
else:
    PLATFORM = "windows" if sys.platform == "win32" else "linux"
