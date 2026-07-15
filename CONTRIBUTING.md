![CrowdStrike Falcon](/images/cs-logo.png?raw=true)

# Contributing to fusion-skills

Thanks for your interest in improving `fusion-skills`. This document covers how to report issues, submit changes, and the bar a change must clear before it merges.

## Reporting Issues

Open an issue on GitHub for bugs, documentation fixes, and enhancement requests. Please format bug reports as an [MCVE](https://stackoverflow.com/help/minimal-reproducible-example) (Minimal, Complete, Verifiable Example) — see [SUPPORT.md](SUPPORT.md). Never include real credentials, customer data, or CID-specific identifiers in an issue.

## Pull Requests

1. Fork the repo and create a feature branch from `main`.
2. Make focused, single-purpose changes — smaller PRs review faster.
3. Run both test scripts locally and confirm they pass (see Testing below).
4. Update the relevant `SKILL.md`, reference docs, and `CHANGELOG.md` as needed.
5. Open the PR with a clear description of what changed and why.

### Merge Criteria

A change merges when it:

- Passes `test-hooks.sh` and `test-skill.sh` with no failures.
- Keeps every Python script syntactically valid and self-contained (shared auth via `common/scripts/auth.py` only).
- Preserves the discipline rules — no hardcoded credentials, no placeholder action IDs, every action carries a `version_constraint`.
- Updates `CHANGELOG.md` for any user-facing change.
- Has at least one maintainer approval.

## Testing

Unit tests (Python) — set up a virtual environment first, then run pytest:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
pytest tests/          # full suite, all API calls mocked (no credentials needed)
```

Fast bash checks (no venv or credentials needed):

```bash
./test-hooks.sh        # Hook routing and cross-plugin advisory unit tests
./test-validate.sh     # SKILL.md frontmatter, Python syntax, reference-doc presence
```

All must exit 0 before a PR is ready for review. If you change a script, add or update its `tests/test_*.py`. If you change a hook, add a case in `test-hooks.sh`. If you add a skill or reference doc, extend `test-validate.sh` to cover it.

## Adding a New Skill

Follow the established pattern so the orchestrator and tests stay consistent:

1. Create `<skill>/SKILL.md` with the standard frontmatter (`name`, `description` with TRIGGER / DO NOT TRIGGER, `version`, `tags`, `author`, `license`, `compatibility`, `metadata.category`).
2. Open the body with a System Injection Block (role + immediate actions + MUST NOT).
3. Put executable scripts in `<skill>/scripts/` and import auth from `common/scripts/auth.py`.
4. Put long-form docs in `<skill>/references/` and link them from a Reading Guide table.
5. Add a routing entry to the `workflows` orchestrator decision tree.
6. Add any new reference docs to the `check_ref` list in `test-skill.sh`.

## Code Standards

- **Python**: docstrings on every script and function; `argparse` for CLI flags; a `--json` mode for machine-readable output where it makes sense.
- **Credentials**: loaded from environment variables or the TOML profile via `auth.py`, never hardcoded.
- **Dependencies**: pin versions, with the documented exception of `crowdstrike-falconpy`, which stays unpinned per CrowdStrike guidance.

---

<p align="center">WE STOP BREACHES</p>
