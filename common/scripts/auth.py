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
   FalconPy discovers the API host for US-1, US-2, and EU-1 from the token
   response, so FALCON_BASE_URL (or a profile's base_url) is needed only for
   other clouds, such as US-3 and GovCloud. A short name such as "usgov1" works.
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

Secret-manager references
-------------------------
Either source can hold a 1Password secret reference instead of a plain value
for the client ID or secret, so the secret never sits on disk:

    client_secret = "op://Private/Falcon Fusion skills/credential"

Values starting with op:// are read with the 1Password CLI, all in one
`op inject` call, when credentials are resolved. The resolved values stay in
memory only.

TLS-inspecting proxies
----------------------
On a network that re-signs TLS traffic, set a profile's `ca_bundle` to your
organization's CA bundle (PEM). It's exported as REQUESTS_CA_BUNDLE for this
process, replacing any inherited value:

    ca_bundle = "~/certs/corporate-ca.pem"

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
import subprocess
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
    Return (client_id, client_secret, base_url, ca_bundle, profile) from the
    TOML profile file, or None if unavailable/incomplete.

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
    return client_id, client_secret, base_url, section.get("ca_bundle", ""), profile


def _fail(message):
    """Print an error to stderr and exit, matching get_credentials()."""
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def _is_secret_ref(value):
    return isinstance(value, str) and value.startswith("op://")


def _resolve_secret_refs(fields):
    """
    Return the values in `fields` (a list of (name, value) pairs), replacing
    each 1Password op:// reference with the secret it points to.

    Every reference is resolved in one `op inject` call, so 1Password asks for
    approval once per run rather than once per value. The command runs with
    an argument list (no shell) and the resolved values are never printed.
    """
    refs = [(name, value) for name, value in fields if _is_secret_ref(value)]
    if not refs:
        return [value for _, value in fields]
    names = " and ".join(name for name, _ in refs)
    template = "".join(f"{{{{ {value} }}}}\n" for _, value in refs)
    try:
        result = subprocess.run(
            ["op", "inject"],
            input=template,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError:
        _fail(
            f"{names} is a 1Password reference (op://...), but the 1Password "
            "CLI (op) isn't installed. Install it from "
            "https://developer.1password.com/docs/cli/get-started/ or use a "
            "plain value."
        )
    except subprocess.TimeoutExpired:
        _fail(
            f"Timed out reading {names} from 1Password. Approve the 1Password "
            "prompt, or run `op signin`, and try again."
        )
    resolved = result.stdout.splitlines()
    if (
        result.returncode != 0
        or len(resolved) != len(refs)
        or not all(v.strip() for v in resolved)
    ):
        detail = result.stderr.strip()
        _fail(
            f"Couldn't read {names} from 1Password "
            f"({', '.join(value for _, value in refs)}). Check that op is "
            "signed in (`op signin`) and the reference exists."
            + (f" op said: {detail}" if detail else "")
        )
    secrets = iter(v.strip() for v in resolved)
    return [next(secrets) if _is_secret_ref(value) else value for _, value in fields]


# Where the CA bundle in use came from, for the self-test. Set by
# _apply_ca_bundle() when a profile's ca_bundle replaces an inherited one.
_ca_bundle_note = ""  # pylint: disable=invalid-name


def _apply_ca_bundle(ca_bundle):
    """
    Export a profile's ca_bundle as REQUESTS_CA_BUNDLE for this process. It
    replaces an inherited REQUESTS_CA_BUNDLE, because a bundle named in this
    plugin's profile is more deliberate than one left in the environment.
    """
    global _ca_bundle_note  # pylint: disable=global-statement  # self-test detail
    if not ca_bundle:
        return
    path = os.path.expanduser(ca_bundle)
    if not os.path.isfile(path):
        _fail(f"The profile's ca_bundle file doesn't exist: {path}")
    inherited = os.environ.get("REQUESTS_CA_BUNDLE")
    _ca_bundle_note = (
        "profile ca_bundle, overrides REQUESTS_CA_BUNDLE"
        if inherited and inherited != path
        else "profile ca_bundle"
    )
    os.environ["REQUESTS_CA_BUNDLE"] = path


def describe_ca_bundle():
    """Describe the CA bundle requests will use, for the self-test."""
    for var in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        path = os.environ.get(var)
        if path:
            note = _ca_bundle_note if var == "REQUESTS_CA_BUNDLE" and _ca_bundle_note else var
            return f"{path} ({note})"
    return "default (certifi)"


# ── Credentials ─────────────────────────────────────────────────────────────


# Resolved credentials and where they came from, cached so each op:// value is
# read from 1Password once per process. Cleared by reset_client().
_credentials = None  # pylint: disable=invalid-name
_credentials_source = ""  # pylint: disable=invalid-name


def get_credentials():
    """
    Return (client_id, client_secret, base_url) using the documented
    resolution order: environment variables, then the TOML profile file. The
    first source supplying both an ID and a secret wins.

    Values that are 1Password op:// references are resolved with `op inject`.
    The result is cached for the process. Exits with a clear error if no
    source provides credentials.
    """
    global _credentials, _credentials_source  # pylint: disable=global-statement  # per-process cache
    if _credentials is None:
        _credentials, _credentials_source = _resolve_credentials()
    return _credentials


def get_credentials_source():
    """Describe where get_credentials() found the credentials, for the self-test."""
    get_credentials()
    return _credentials_source


def _describe_source(source, *values):
    """Append "(1Password)" to a source description when a value is an op:// ref."""
    if any(_is_secret_ref(v) for v in values):
        return f"{source} (1Password)"
    return source


def _resolve_credentials():
    """Resolve credentials and describe their source; see get_credentials()."""
    # 1. Environment variables (for CI and overrides). These outrank the TOML
    #    file so a run can be redirected without editing the profile.
    client_id = os.environ.get("FALCON_CLIENT_ID", "")
    client_secret = os.environ.get("FALCON_CLIENT_SECRET", "")
    if client_id and client_secret:
        base_url = os.environ.get("FALCON_BASE_URL", DEFAULT_BASE_URL)
        creds = (
            *_resolve_secret_refs(
                [("FALCON_CLIENT_ID", client_id), ("FALCON_CLIENT_SECRET", client_secret)]
            ),
            base_url.rstrip("/"),
        )
        return creds, _describe_source("environment variables", client_id, client_secret)

    # 2. TOML profile file (canonical; written by the setup command).
    toml_creds = _creds_from_toml()
    if toml_creds:
        client_id, client_secret, base_url, ca_bundle, profile = toml_creds
        _apply_ca_bundle(ca_bundle)
        creds = (
            *_resolve_secret_refs([("client_id", client_id), ("client_secret", client_secret)]),
            base_url.rstrip("/"),
        )
        return creds, _describe_source(
            f"profile {profile} in {TOML_CREDENTIALS_PATH}", client_id, client_secret
        )

    return _fail(
        "FALCON_CLIENT_ID and FALCON_CLIENT_SECRET must be set via "
        "environment variables or the TOML credentials file "
        "(~/.cache/crowdstrike-falcon-fusion/credentials.toml). "
        "Run /crowdstrike-falcon-fusion:setup to configure credentials."
    )


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
    """Reset both shared clients and the cached credentials (useful for testing)."""
    global _workflows_client, _ngsiem_client, _credentials, _credentials_source, _ca_bundle_note  # pylint: disable=global-statement  # reset cached singletons
    _workflows_client = None
    _ngsiem_client = None
    _credentials = None
    _credentials_source = ""
    _ca_bundle_note = ""


TOKEN_FAILURE_HINT = (
    "  Check the client ID, secret, and base URL. If they're right, check the CA\n"
    "  bundle above: behind a TLS-inspecting proxy, it must be your\n"
    "  organization's bundle (set ca_bundle in the profile)."
)

# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("CrowdStrike Auth — self-test (FalconPy)")
    print("─" * 40)
    cid, csec, burl = get_credentials()
    print(f"  Source    : {get_credentials_source()}")
    print(f"  Base URL  : {burl}")
    print(f"  CA bundle : {describe_ca_bundle()}")
    print(f"  Client ID : {cid[:8]}...{cid[-4:]}")
    print(f"  Secret    : {'set' if csec else 'missing'}")
    print()
    try:
        wf_client = get_client()
        if wf_client.token_expired():
            print(
                "  Authentication FAILED: could not obtain token (Workflows)",
                file=sys.stderr,
            )
            print(TOKEN_FAILURE_HINT, file=sys.stderr)
            sys.exit(1)
        print("  Authentication successful (FalconPy Workflows client)")

        ngsiem_client = get_ngsiem_client()
        if ngsiem_client.token_expired():
            print(
                "  Authentication FAILED: could not obtain token (NGSIEM)",
                file=sys.stderr,
            )
            print(TOKEN_FAILURE_HINT, file=sys.stderr)
            sys.exit(1)
        print("  Authentication successful (FalconPy NGSIEM client)")
    # Self-test reports any auth failure cause to the user, so a broad catch is
    # intentional here — narrowing would drop useful diagnostics.
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"\n  Authentication FAILED: {e}", file=sys.stderr)
        sys.exit(1)
