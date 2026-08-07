"""
Shared test fixtures and helpers for fusion-skills tests.

All tests mock HTTP responses — no CrowdStrike API credentials are needed.
"""

import os
import sys

import pytest

# Add every scripts directory to sys.path so tests can import the modules
# by name (matching how the scripts import each other at runtime).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT_DIRS = [
    os.path.join(_ROOT, "common", "scripts"),
    os.path.join(_ROOT, "skills", "authoring", "scripts"),
    os.path.join(_ROOT, "skills", "deployment", "scripts"),
    os.path.join(_ROOT, "skills", "execution", "scripts"),
    os.path.join(_ROOT, "skills", "lookup-files", "scripts"),
    os.path.join(_ROOT, "scripts"),
]
for _d in _SCRIPT_DIRS:
    if _d not in sys.path:
        sys.path.insert(0, _d)

import auth  # noqa: E402  # must follow sys.path setup


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Ensure no real credentials or TOML files leak into tests."""
    for var in (
        "FALCON_CLIENT_ID",
        "FALCON_CLIENT_SECRET",
        "FALCON_BASE_URL",
        "FALCON_PROFILE",
    ):
        monkeypatch.delenv(var, raising=False)
    # Point the TOML credentials path at a nonexistent file so tests never read
    # a real profile on the developer's machine.
    monkeypatch.setattr(auth, "TOML_CREDENTIALS_PATH", "/nonexistent/credentials.toml")
    auth.reset_client()
    yield
    auth.reset_client()


@pytest.fixture
def fake_credentials(monkeypatch):
    """Set fake credentials for tests that need auth to succeed."""
    monkeypatch.setenv("FALCON_CLIENT_ID", "fake_client_id_1234567890abcdef")
    monkeypatch.setenv("FALCON_CLIENT_SECRET", "fake_secret_abcdef1234567890")
    monkeypatch.setenv("FALCON_BASE_URL", "https://api.crowdstrike.com")
    auth.reset_client()
