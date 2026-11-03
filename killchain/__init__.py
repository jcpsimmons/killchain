"""killchain: split a build so a frontier agent never sees the risky half.

The prompt is partitioned by *refusal risk*, not confidentiality. Anything a
commercial coding agent would refuse, hedge, simplify, or guess at is routed to
an abliterated orchestrator model, which then implements it locally. A frontier
agent (Claude Code, Codex) builds the rest against stubs.
"""

def _version() -> str:
    """Read the version from installed metadata.

    A hardcoded string here drifts from pyproject.toml, and a drifted version
    makes PyPI reject the upload with "file already exists" on every retry.
    Falls back to pyproject.toml when running from an uninstalled source tree,
    and to "0+unknown" if neither is available.
    """
    try:
        from importlib.metadata import version
        return version("killchain")
    except Exception:
        pass
    try:
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        return tomllib.loads(pyproject.read_text())["project"]["version"]
    except Exception:
        return "0+unknown"


__version__ = _version()
__all__ = ["__version__"]
