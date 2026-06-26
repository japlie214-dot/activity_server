# tools/crypto/tool.py
"""Crypto tool — two Activities: prepare, then hash."""
import hashlib
from tools import Tool
from server.accumulator import Activity
from .config import TOOL_NAME, DEFAULT_SALT
from .docs import TOOL_DESCRIPTION, TOOL_DOCS, TOOL_OUTPUT_EXAMPLE


@Activity("crypto.prepare")
def prepare(acc, plaintext: str, salt: str) -> str:
    """Combine salt and plaintext into the pre-hash payload."""
    return f"{salt}:{plaintext}"


@Activity("crypto.hash")
def hash_payload(acc, payload: str) -> str:
    """SHA-256 hash the payload and return hex digest."""
    return hashlib.sha256(payload.encode()).hexdigest()


class CryptoTool(Tool):
    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    input_schema = {
        "type": "object",
        "properties": {
            "plaintext": {"type": "string", "description": "String to encrypt"},
            "salt": {"type": "string", "description": "Optional salt"},
        },
        "required": ["plaintext"],
    }

    def execute(self, arguments: dict, acc=None) -> dict:
        plaintext = arguments["plaintext"]
        salt = arguments.get("salt", DEFAULT_SALT)
        payload = prepare(acc, plaintext, salt)
        hashed = hash_payload(acc, payload)
        return {"plaintext": plaintext, "encrypted": hashed, "algorithm": "sha256-hex", "salt": salt}

    @classmethod
    def docs(cls) -> dict:
        return {
            "summary": cls.description,
            "description": TOOL_DOCS,
            "input_schema": cls.input_schema,
            "output_example": TOOL_OUTPUT_EXAMPLE,
        }
