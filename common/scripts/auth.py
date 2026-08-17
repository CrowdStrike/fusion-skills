"""
Shared CrowdStrike authentication using the FalconPy SDK.

This is the single source of truth for API auth across every fusion-skills
sub-skill (authoring, deployment, execution, lookup-files). It exposes two
FalconPy service clients built from the same credentials:

    get_client()         -> FalconPy Workflows client (Fusion workflow APIs)
    get_ngsiem_client()  -> FalconPy NGSIEM client (Next-Gen SIEM lookup files)

Credentials are never hardcoded. Run directly to verify them against both
clients:

    python auth.py

Credential resolution order (first source that supplies both an ID and a
secret wins)
-----------------------------------------------------------------------------
1. Environment variables: FALCON_CLIENT_ID, FALCON_CLIENT_SECRET, and the
   optional FALCON_BASE_URL. Use these for CI and one-off overrides.
2. TOML profile file at ~/.cache/crowdstrike-falcon-fusion/credentials.toml.
   The profile used is the one named by the FALCON_PROFILE environment
   variable, or the file's top-level `default` key when FALCON_PROFILE is
   unset. Example:

       default = "us-1"

       [us-1]
       client_id = "abc123..."
       client_secret = "xyz789..."
       base_url = "https://api.crowdstrike.com"

   Parsed with the standard-library `tomllib` (Python 3.11+), falling back to
   the `tomli` package on 3.10. If neither is importable, the TOML step is
   skipped silently.

Run `/crowdstrike-falcon-fusion:setup` to configure credentials interactively
(it writes the TOML profile).

Import contract for sibling scripts
-----------------------------------
Each skill's scripts/ directory is three levels below the repo root
(e.g. skills/authoring/scripts/), so the shared module lives at
../../../common/scripts relative to the importing script. Add that directory to
sys.path anchored to the importing file's own location, then import normally:

    import sys, os
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            "..", "..", "..", "common", "scripts",
        ),
    )
    from auth import get_client          # Workflows client (default)
    # or, for lookup-files scripts:
    from auth import get_ngsiem_client   # NGSIEM client

Anchoring to __file__ (not the current working directory) makes the import
work no matter where the script is launched from. No package install or
symlink is required.

Note: the FalconPy version is intentionally left unpinned in requirements so
installs automatically receive the latest SDK updates. This module imports the
classes lazily inside the client factories so a missing dependency surfaces
only when a client is actually requested, not at import time.
"""

import os
import re
import sys

# Fix Windows console encoding so the Unicode box characters below render.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_BASE_URL = "https://api.crowdstrike.com"

# Minimum supported FalconPy version. delete_definitions /
# WorkflowDefinitionsDelete (used by workflow cleanup) only exists in FalconPy
# >= 1.6.3. On a stale install the scripts otherwise fail late with a cryptic
# AttributeError; the version guard below turns that into an early, clear error.
# The dependency itself stays unpinned per CrowdStrike guidance — this is a
# runtime floor, not a requirements pin.
MIN_FALCONPY = (1, 6, 3)

# TOML credentials file written by the setup skill.
TOML_CREDENTIALS_PATH = os.path.expanduser(
    "~/.cache/crowdstrike-falcon-fusion/credentials.toml"
)


# ── TOML profile loader ───────────────────────────────────────────────────────


def _load_toml(path):
    """
    Parse a TOML file into a dict.

    Uses the stdlib `tomllib` (Python 3.11+) and falls back to the third-party
    `tomli` package on 3.10. Returns None if no TOML parser is available, the
    file is missing, or it cannot be parsed — never raises.
    """
    try:
        import tomllib as toml_parser  # pylint: disable=import-outside-toplevel
    except ModuleNotFoundError:
        try:
            import tomli as toml_parser  # pylint: disable=import-outside-toplevel
        except ModuleNotFoundError:
            return None  # No TOML parser available — skip TOML silently.

    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            return toml_parser.load(f)
    except (OSError, ValueError):
        # ValueError covers tomllib.TOMLDecodeError (a subclass).
        return None


def _creds_from_toml():
    """
    Return (client_id, client_secret, base_url) from the TOML profile file,
    or None if unavailable/incomplete.

    The profile is chosen by FALCON_PROFILE, falling back to the file's
    top-level `default` key.
    """
    data = _load_toml(TOML_CREDENTIALS_PATH)
    if not isinstance(data, dict):
        return None

    profile = os.environ.get("FALCON_PROFILE") or data.get("default")
    if not profile:
        return None

    section = data.get(profile)
    if not isinstance(section, dict):
        return None

    client_id = section.get("client_id", "")
    client_secret = section.get("client_secret", "")
    if not client_id or not client_secret:
        return None

    base_url = section.get("base_url", DEFAULT_BASE_URL)
    return client_id, client_secret, base_url


# ── Credentials ─────────────────────────────────────────────────────────────


def get_credentials():
    """
    Return (client_id, client_secret, base_url) using the documented
    resolution order: environment variables, then the TOML profile file. The
    first source supplying both an ID and a secret wins.

    Exits with a clear error if no source provides credentials.
    """
    # 1. Environment variables (for CI and overrides). These outrank the TOML
    #    file so a run can be redirected without editing the profile.
    client_id = os.environ.get("FALCON_CLIENT_ID", "")
    client_secret = os.environ.get("FALCON_CLIENT_SECRET", "")
    if client_id and client_secret:
        base_url = os.environ.get("FALCON_BASE_URL", DEFAULT_BASE_URL)
        return client_id, client_secret, base_url.rstrip("/")

    # 2. TOML profile file (canonical; written by the setup command).
    toml_creds = _creds_from_toml()
    if toml_creds:
        client_id, client_secret, base_url = toml_creds
        return client_id, client_secret, base_url.rstrip("/")

    print(
        "ERROR: FALCON_CLIENT_ID and FALCON_CLIENT_SECRET must be set via "
        "environment variables or the TOML credentials file "
        "(~/.cache/crowdstrike-falcon-fusion/credentials.toml). "
        "Run /crowdstrike-falcon-fusion:setup to configure credentials.",
        file=sys.stderr,
    )
    sys.exit(1)


# ── FalconPy version guard ────────────────────────────────────────────────────


def _falconpy_version(falconpy):
    """Return the installed FalconPy version as a (major, minor, patch) tuple.

    Reads `falconpy.version()` (a string like "1.6.3"), falling back to the
    `__version__` attribute. Parses defensively: only the leading numeric
    dotted components are used, and anything unparseable yields (0, 0, 0) so the
    guard fails closed (treats an unknown version as too old).
    """
    raw = ""
    version_fn = getattr(falconpy, "version", None)
    if callable(version_fn):
        try:
            raw = version_fn() or ""
        except Exception:  # pylint: disable=broad-exception-caught
            raw = ""
    if not raw:
        raw = getattr(falconpy, "__version__", "") or ""

    match = re.match(r"\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(raw))
    if not match:
        return (0, 0, 0)
    return tuple(int(part) if part else 0 for part in match.groups())


def _check_falconpy_version(falconpy):
    """Raise RuntimeError if the installed FalconPy is older than MIN_FALCONPY."""
    found = _falconpy_version(falconpy)
    if found < MIN_FALCONPY:
        floor = ".".join(str(n) for n in MIN_FALCONPY)
        found_str = ".".join(str(n) for n in found)
        raise RuntimeError(
            f"FalconPy >= {floor} required (found {found_str}). "
            "Upgrade with `pip install -U crowdstrike-falconpy`, or run this "
            "script via the project virtualenv: .venv/bin/python"
        )


# ── FalconPy clients ─────────────────────────────────────────────────────────
#
# Each service client is cached as its own module-level singleton so that
# requesting one never forces construction of the other. Both are built from
# the same credentials.

# Module-level client singletons. pylint flags these as constants (UPPER_CASE),
# but they are deliberately mutable caches reset by reset_client().
_workflows_client = None  # pylint: disable=invalid-name
_ngsiem_client = None  # pylint: disable=invalid-name


def get_client():
    """
    Return a shared FalconPy Workflows client, creating it on first use.

    This is the default client for Fusion workflow scripts (authoring,
    deployment, execution) and preserves the existing import call sites.
    """
    global _workflows_client  # pylint: disable=global-statement  # lazy singleton cache
    if _workflows_client is None:
        # Imported lazily (version intentionally unpinned) so a missing falconpy
        # dependency surfaces only when a client is requested, not at import time.
        import falconpy  # pylint: disable=import-outside-toplevel

        _check_falconpy_version(falconpy)
        client_id, client_secret, base_url = get_credentials()
        _workflows_client = falconpy.Workflows(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
        )
    return _workflows_client


def get_ngsiem_client():
    """
    Return a shared FalconPy NGSIEM client, creating it on first use.

    Used by the lookup-files sub-skill for Falcon Next-Gen SIEM operations.
    """
    global _ngsiem_client  # pylint: disable=global-statement  # lazy singleton cache
    if _ngsiem_client is None:
        # Imported lazily (version intentionally unpinned) so a missing falconpy
        # dependency surfaces only when a client is requested, not at import time.
        import falconpy  # pylint: disable=import-outside-toplevel

        _check_falconpy_version(falconpy)
        client_id, client_secret, base_url = get_credentials()
        _ngsiem_client = falconpy.NGSIEM(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
        )
    return _ngsiem_client


def reset_client():
    """Reset both shared clients (useful for testing)."""
    global _workflows_client, _ngsiem_client  # pylint: disable=global-statement  # reset cached singletons
    _workflows_client = None
    _ngsiem_client = None


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("CrowdStrike Auth — self-test (FalconPy)")
    print("─" * 40)
    cid, csec, burl = get_credentials()
    print(f"  Base URL  : {burl}")
    print(f"  Client ID : {cid[:8]}...{cid[-4:]}")
    print(f"  Secret    : {'*' * 8}...{csec[-4:]}")
    print()
    try:
        wf_client = get_client()
        if wf_client.token_expired():
            print(
                "  Authentication FAILED: could not obtain token (Workflows)",
                file=sys.stderr,
            )
            sys.exit(1)
        print("  Authentication successful (FalconPy Workflows client)")

        ngsiem_client = get_ngsiem_client()
        if ngsiem_client.token_expired():
            print(
                "  Authentication FAILED: could not obtain token (NGSIEM)",
                file=sys.stderr,
            )
            sys.exit(1)
        print("  Authentication successful (FalconPy NGSIEM client)")
    # Self-test reports any auth failure cause to the user, so a broad catch is
    # intentional here — narrowing would drop useful diagnostics.
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"\n  Authentication FAILED: {e}", file=sys.stderr)
        sys.exit(1)
