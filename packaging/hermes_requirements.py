"""Print the requirements the bundled Hermes needs: its dependencies plus the acp and mcp extras.
Hermes refuses to be built as a wheel; Meshpool runs it from source (app/vendor/hermes), so only its dependencies
are installed into the bundled Python. Usage: python hermes_requirements.py path/to/hermes-agent > req.txt"""
import sys
import tomllib
from pathlib import Path

p = tomllib.loads((Path(sys.argv[1]) / "pyproject.toml").read_text(encoding="utf-8"))["project"]
extras = p.get("optional-dependencies") or {}
for r in [*p.get("dependencies", []), *extras.get("acp", []), *extras.get("mcp", [])]:
    print(r)
