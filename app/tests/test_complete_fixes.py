"""v2 #2: no size cap on patches (docs/changelog still blocked); related call sites listed once before finishing."""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engines.code import gates  # noqa: E402


def _repo(files):
    d = tempfile.mkdtemp()
    for k, v in files.items():
        Path(d, k).parent.mkdir(parents=True, exist_ok=True)
        Path(d, k).write_text(v)
    for c in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "x"]):
        subprocess.run(c, cwd=d, check=True)
    return d


def test_large_source_change_not_blocked():
    d = _repo({"pkg/a.py": "def f():\n    return 1\n"})
    Path(d, "pkg/a.py").write_text("def f():\n" + "".join(f"    x{i} = {i}\n" for i in range(120)) + "    return 1\n")
    assert gates.scope_gate(d, "fix f") is None


def test_changelog_edit_blocked():
    d = _repo({"pkg/a.py": "x = 1\n", "CHANGELOG.rst": "v1\n"})
    Path(d, "CHANGELOG.rst").write_text("v1\nv2\n")
    assert "CHANGELOG" in (gates.scope_gate(d, "fix f") or "")


def test_related_sites_listed():
    d = _repo({"pkg/a.py": "def parse_value(x):\n    return x\n", "pkg/b.py": "from .a import parse_value\ny = parse_value(2)\n"})
    Path(d, "pkg/a.py").write_text("def parse_value(x):\n    return int(x)\n")
    msg = gates.related_sites(d)
    assert msg and "pkg/b.py" in msg
