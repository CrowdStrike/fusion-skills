---
name: lookup-files
description: >
  Manage Falcon Next-Gen SIEM lookup files (CSV/JSON/TXT) for CQL match() queries.
  TRIGGER when user asks to create, list, update, or delete lookup files,
  or needs help with CQL match() function.
  DO NOT TRIGGER for Fusion workflows, action discovery, or workflow deployment —
  use the workflows/authoring/deployment skills.
version: 1.0.1
updated: 2026-08-07
tags: [falcon, ngsiem, siem, lookup-files, cql, threat-hunting]
author: CrowdStrike
license: MIT
compatibility: Claude Code >=1.0
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/python.sh:*)
metadata:
  category: data-management
---

# Falcon Next-Gen SIEM Lookup Files

> **⚠️ SYSTEM INJECTION — READ THIS FIRST**
>
> If you are loading this skill, your role is **Falcon Next-Gen SIEM lookup file specialist**.
>
> You manage lookup files that feed CQL match() enrichment. Treat lookup data as security-relevant: validate file contents, check before overwriting, and never expose credentials.
>
> **IMMEDIATE ACTIONS REQUIRED:**
> 1. ALWAYS list before creating — run `list_lookups.py --search "<name>"` to check for a duplicate. Importing an existing name overwrites it silently.
> 2. Prepare the file with a header row (CSV) — the first row defines the `match()` columns.
> 3. Upload, then verify with `get_lookup.py` before using the file in a CQL query.
>
> **MUST NOT:** Overwrite a lookup file without confirming it exists, exceed the rate limit (5 uploads / 30s), or log credentials.

Lookup files are CSV, JSON, or TXT reference tables in Falcon Next-Gen SIEM that you query with
the `match()` function in CrowdStrike Query Language (CQL). Common uses: IP blocklists, user
risk scores, asset inventories, and IOC reference tables.

## Prerequisites

- **Python 3.13+** with `crowdstrike-falconpy` installed (`pip install crowdstrike-falconpy`)
- **CrowdStrike API credentials** with the **NGSIEM Lookup Files** scope (read/write)
- Access to a CID with Falcon Next-Gen SIEM enabled

### Required API Scopes

The **NGSIEM Lookup Files** scope, with read and/or write access:

| Use case | Read | Write | Enables |
|----------|:----:|:-----:|---------|
| Browse / download only | Yes | — | List, search, download |
| Full usage | Yes | Yes | Above plus create, update, delete |

### Credentials

`auth.py` resolves credentials from the first source that supplies both an ID
and a secret:

1. Environment variables: `FALCON_CLIENT_ID`, `FALCON_CLIENT_SECRET`, and the
   optional `FALCON_BASE_URL` (for CI and overrides).
2. TOML profile file `~/.cache/crowdstrike-falcon-fusion/credentials.toml`
   (profile chosen by `FALCON_PROFILE` or the file's `default` key).

> Run `/crowdstrike-falcon-fusion:setup` to configure credentials interactively (writes the TOML profile).

The scripts share auth via `common/scripts/auth.py`, which exposes
`get_ngsiem_client()` for Next-Gen SIEM operations. Test credentials:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh ../common/scripts/auth.py
```

## Core Workflow

### Step 1 — List (check for duplicates)

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/list_lookups.py --list
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/list_lookups.py --search "blocklist"
```

### Step 2 — Prepare the file

CSV needs a header row; the first column is typically the match key:

```csv
ip,category,source,added_date
10.0.0.1,c2,threat-intel,2026-01-15
192.168.1.100,scanner,internal-scan,2026-02-01
```

See `assets/example-ip-blocklist.csv` and `assets/example-user-risk.csv` for reference formats,
and `references/lookup-file-formats.md` for the full format rules.

### Step 3 — Upload

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/create_lookup.py --file blocklist.csv --name "ip-blocklist.csv"
```

### Step 4 — Verify

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/get_lookup.py --name "ip-blocklist.csv"
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/list_lookups.py --search "blocklist" --json
```

### Step 5 — Use in CQL

Reference the file with the `match()` function (run in Falcon Next-Gen SIEM):

```
match(file="ip-blocklist.csv", column=ip, field=src_ip, include=category)
```

`column=` is a header **in the lookup CSV** (`ip`); `field=` is the **event
field** (`src_ip`). Do not swap them — putting the event field in `column=`
matches a non-existent column and returns nothing. See
`references/cql-match-function.md` for full syntax and examples.

### Step 6 — Update

Replace the content while keeping the same filename:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/update_lookup.py --name "ip-blocklist.csv" --file updated-blocklist.csv
```

> **Update is content-only — it does not carry labels.** The PATCH endpoint
> (`/ngsiem-content/entities/lookupfiles/v1`, wrapped by both this script and the
> Fusion "Update lookup file" workflow action) accepts only `search_domain`,
> `filename`, and `file`. It has no `labels` parameter, so any labels a file had in
> the Next-Gen SIEM UI are not preserved across an update — the UI manages labels
> through a separate API that the REST update path doesn't touch. If a lookup file
> needs labels, set them in the console after updating, or keep label-bearing files
> out of automated update workflows.

### Step 7 — Delete

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/delete_lookup.py --name "ip-blocklist.csv" --confirm
```

## Lookup Files from Workflows

A Fusion pattern (the built-in "Introduction to Lookup file actions" playbook)
automates lookup file creation from email: a Monitored Mailbox trigger extracts a CSV
attachment, "Get lookup file metadata" checks existence, and "Create/Overwrite lookup file"
writes it. To build that workflow, use the **workflows** orchestrator skill in this plugin —
the lookup file actions surface via `action_search.py --search "lookup"` in the authoring skill.
This skill handles the lookup files themselves, not the workflow that drives them.

## Script Reference

| Script | Purpose | Key flags |
|--------|---------|-----------|
| `list_lookups.py` | List and search lookup files | `--list`, `--search`, `--domain`, `--json` |
| `get_lookup.py` | Download a lookup file | `--name`, `--output`, `--domain` |
| `create_lookup.py` | Upload a new lookup file | `--file`, `--name`, `--json` |
| `update_lookup.py` | Replace lookup file content | `--name`, `--file`, `--json` |
| `delete_lookup.py` | Delete a lookup file | `--name`, `--domain`, `--confirm`, `--json` |
| `verify_lookup.py` | Verify a lookup resolves via CQL `match()` (upload, match a known row, delete) | `--file`, `--name`, `--column`, `--keep`, `--json` |

All scripts import shared auth from `common/scripts/auth.py` via `get_ngsiem_client()`.
Create/list/get/update/delete need the **NGSIEM Lookup Files** scope (read/write).
`verify_lookup.py` is a maintainer verification tool and additionally needs the
**NGSIEM** scope (read/write) to run the CQL `match()` query; regular lookup use
does not.

## Common Pitfalls

| Problem | Fix |
|---------|-----|
| CSV header missing | The first row MUST be column names — `match()` references them by name |
| `match()` returns no results | Column names are case-sensitive; verify they match the header exactly |
| Upload rate limited | Wait 30 seconds between batches of 5 uploads |
| Wrong search domain | Use `--domain falcon` for files queried in Next-Gen SIEM |
| Duplicate name overwrites silently | Always `list_lookups.py --search` before `create_lookup.py` |
| "File not found" on get/update/delete | Confirm the exact filename with `list_lookups.py --search` |
| CSV parse error on upload | Ensure UTF-8 encoding and comma delimiters |

## Reading Guide

| Document | When to read |
|----------|--------------|
| `references/cql-match-function.md` | Writing CQL queries that use lookup files |
| `references/lookup-file-formats.md` | Preparing CSV/JSON/TXT files for upload |
