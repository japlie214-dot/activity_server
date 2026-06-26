# tools/textstats/tool.py
"""TextStats tool — two Activities: count, then hash."""
import hashlib
from tools import Tool
from server.accumulator import Activity
from .config import TOOL_NAME
from .docs import TOOL_DESCRIPTION, TOOL_DOCS, TOOL_OUTPUT_EXAMPLE


@Activity("textstats.count")
def count(acc, text: str) -> dict:
    """Count words, characters, and sentences."""
    words = text.split()
    sentences = [s.strip() for s in text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    return {
        "char_count": len(text),
        "word_count": len(words),
        "sentence_count": len(sentences),
        "avg_word_length": round(sum(len(w) for w in words) / max(len(words), 1), 2),
    }


@Activity("textstats.hash")
def hash_text(acc, text: str) -> str:
    """SHA-256 hash the raw text for change detection."""
    return hashlib.sha256(text.encode()).hexdigest()


class TextStatsTool(Tool):
    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to analyze"},
        },
        "required": ["text"],
    }

    def execute(self, arguments: dict, acc=None) -> dict:
        text = arguments["text"]
        stats = count(acc, text)
        stats["sha256"] = hash_text(acc, text)
        return stats

    @classmethod
    def docs(cls) -> dict:
        return {
            "summary": cls.description,
            "description": TOOL_DOCS,
            "input_schema": cls.input_schema,
            "output_example": TOOL_OUTPUT_EXAMPLE,
        }
