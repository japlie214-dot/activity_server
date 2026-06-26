# tools/artifact_saver/tool.py
"""Artifact Saver tool — three Activities: prepare, write file, record in DB."""
import os
from datetime import datetime, timezone
from pathlib import Path
from tools import Tool
from server.accumulator import Activity
from db.config import ARTIFACTS_DIR
from .config import TOOL_NAME
from .docs import TOOL_DESCRIPTION, TOOL_DOCS, TOOL_OUTPUT_EXAMPLE


@Activity("artifact_saver.prepare")
def prepare(acc, content: str, filename: str) -> tuple:
    """Resolve filename and ensure artifacts directory exists."""
    if not filename:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"artifact_{ts}"
    if not filename.endswith(".txt"):
        filename += ".txt"

    artifacts_dir = Path(ARTIFACTS_DIR)
    if not os.path.isabs(str(artifacts_dir)):
        artifacts_dir = Path(__file__).resolve().parent.parent.parent / artifacts_dir
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    return filename, artifacts_dir


@Activity("artifact_saver.write_file")
def write_file(acc, content: str, filename: str, artifacts_dir: Path) -> dict:
    """Write content to the artifact file and return metadata."""
    filepath = artifacts_dir / filename
    filepath.write_text(content, encoding="utf-8")
    size_bytes = filepath.stat().st_size
    return {"filepath": str(filepath), "size_bytes": size_bytes}


@Activity("artifact_saver.record_db")
def record_db(acc, filename: str, filepath: str, size_bytes: int, content: str):
    """Record the artifact in the database via DualWriter (cloud-synced)."""
    from server.app import get_server
    server = get_server()
    if server and server.dual_writer:
        server.dual_writer.write("artifacts", {
            "tool_name": "artifact_saver",
            "filename": filename,
            "filepath": filepath,
            "size_bytes": size_bytes,
            "content_preview": content[:200],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })


class ArtifactSaverTool(Tool):
    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    input_schema = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Text content to save"},
            "filename": {"type": "string", "description": "Filename (without .txt)"},
        },
        "required": ["content"],
    }

    def execute(self, arguments: dict, acc=None) -> dict:
        content = arguments["content"]
        filename = arguments.get("filename", "")

        filename, artifacts_dir = prepare(acc, content, filename)
        file_meta = write_file(acc, content, filename, artifacts_dir)
        record_db(acc, filename, file_meta["filepath"], file_meta["size_bytes"], content)

        return {
            "saved": True,
            "filename": filename,
            "filepath": file_meta["filepath"],
            "size_bytes": file_meta["size_bytes"],
            "content_preview": content[:200],
        }

    @classmethod
    def docs(cls) -> dict:
        return {
            "summary": cls.description,
            "description": TOOL_DOCS,
            "input_schema": cls.input_schema,
            "output_example": TOOL_OUTPUT_EXAMPLE,
        }
