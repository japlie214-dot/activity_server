# tools/__init__.py
"""
Tool base class, auto-registration registry, and tool loader.

To add a new tool:
  1. Create tools/{name}/config.py with TOOL_NAME
  2. Create tools/{name}/desc.py with TOOL_DESCRIPTION
  3. Create tools/{name}/tool.py with a Tool subclass
  4. That's it — __init_subclass__ auto-registers it.

Call load_all_tools() in your entry point to trigger registration.
"""

import importlib
import json
import pkgutil
from typing import Any, Dict


TOOL_REGISTRY: Dict[str, "Tool"] = {}


class Tool:
    """Base class for MCP tools. Subclass + set name to auto-register."""
    name = ""
    description = ""
    input_schema: dict = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.name:
            TOOL_REGISTRY[cls.name] = cls()

    def execute(self, arguments: dict, acc=None) -> Any:
        raise NotImplementedError

    @classmethod
    def docs(cls) -> dict:
        """Return structured documentation for this tool.

        Override in subclasses to provide rich docs. The returned dict may contain:
          - summary: one-line description
          - description: full markdown documentation
          - input_schema: JSON Schema dict
          - output_example: example output dict
        """
        return {
            "summary": cls.description,
            "description": f"## {cls.name}\n\n{cls.description}",
            "input_schema": cls.input_schema,
            "output_example": {},
        }

    @classmethod
    def docs_markdown(cls) -> str:
        """Render documentation as a markdown string for HTTP responses."""
        d = cls.docs()
        parts = [d.get("description", cls.description)]

        # Output example
        output = d.get("output_example")
        if output:
            parts.append("### Output Example\n")
            parts.append("```json")
            parts.append(json.dumps(output, indent=2))
            parts.append("```")

        return "\n".join(parts)


def load_all_tools():
    """Import all tool modules to trigger __init_subclass__ registration."""
    import tools as tools_pkg
    for _importer, mod_name, _ispkg in pkgutil.walk_packages(
            tools_pkg.__path__, prefix=tools_pkg.__name__ + "."):
        if mod_name.endswith(".tool"):
            importlib.import_module(mod_name)
