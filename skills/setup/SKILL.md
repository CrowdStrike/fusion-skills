---
name: setup
description: >
  Configure CrowdStrike Falcon API credentials for the fusion-skills plugin.
  TRIGGER when user asks to set up credentials, configure API access,
  or runs into authentication errors.
version: 1.0.1
updated: 2026-08-07
tags: [fusion, setup, credentials, configuration]
author: CrowdStrike
license: MIT
compatibility: Claude Code >=1.0
metadata:
  category: configuration
---

# Falcon Fusion Credential Setup

> **⚠️ SYSTEM INJECTION — READ THIS FIRST**
>
> If you are loading this skill, your role is **credential setup assistant**.
>
> You configure the Falcon API credentials every other skill depends on. These
> credentials grant workflow and SIEM access to a live CID.
>
> **IMMEDIATE ACTIONS REQUIRED:**
> 1. Check whether credentials already resolve (Step 1). If they do, you are done.
> 2. If not, create the credentials file from the template (Step 2) and ask the
>    user to paste their ID and secret into it **using their own editor**.
> 3. Verify connectivity (Step 3).
>
> **MUST NOT:**
> - Ask the user to type or paste their client secret **into the chat**. It would
>   land in the conversation transcript. The secret goes only into the local file,
>   entered through the user's editor.
> - Print, echo, or repeat a secret you happen to see in the file.
> - Suggest `export FALCON_CLIENT_SECRET=...` for interactive use — it leaks the
>   secret into shell history. (Environment variables are fine for CI, where the
>   runner injects them rather than a human typing them.)

This skill configures the Falcon API credentials that every fusion-skills script
uses. Credentials are stored in a per-profile TOML file at
`~/.cache/crowdstrike-falcon-fusion/credentials.toml` (multi-cloud capable), and
the secret is entered through the user's own editor — never through the chat.

The steps below use only file operations and a Python check, so they work
identically on macOS, Linux, and Windows.

## Step 1 — Check for existing credentials

Run the auth self-test. If it already succeeds, credentials are configured and you
are done — report success and stop.

```bash
python "${CLAUDE_PLUGIN_ROOT}/common/scripts/auth.py"
```

- **"Authentication successful"** for both clients → done.
- **An error about missing credentials** → continue to Step 2.
- **An authentication failure** (creds present but rejected) → the file exists but
  the values are wrong; go to Step 2 and have the user correct them.

## Step 2 — Create the credentials file and have the user fill it in

Create `~/.cache/crowdstrike-falcon-fusion/credentials.toml` **only if it does not
already exist** (never overwrite existing profiles). Write this template with the
Write tool:

```toml
# CrowdStrike Falcon API credentials for fusion-skills.
# Fill in client_id and client_secret below, then save this file.
#
# Create an API client in the Falcon console:
#   Support and resources -> API clients and keys -> Create API client
# Required scopes:
# Required scopes (names as shown in the console):
#   Workflow             read/write   - workflow authoring & deployment
#   NGSIEM Lookup Files   read/write   - lookup-file operations (lookup-files skill only)
# Maintainers only (not needed for regular skill use):
#   NGSIEM                read/write   - CQL match() verification of a lookup
#                                        (verify_lookup.py / verify-workflows.sh --lookup-dir)

default = "us-2"

[us-2]
client_id = ""
client_secret = ""
base_url = "https://api.us-2.crowdstrike.com"

# Add more clouds as needed (change `default` above to switch):
# [us-1]
# client_id = ""
# client_secret = ""
# base_url = "https://api.crowdstrike.com"
#
# [eu-1]
# client_id = ""
# client_secret = ""
# base_url = "https://api.eu-1.crowdstrike.com"
```

After creating the file, restrict its permissions (skip on Windows, where the user
profile directory is already access-controlled):

```bash
chmod 700 ~/.cache/crowdstrike-falcon-fusion
chmod 600 ~/.cache/crowdstrike-falcon-fusion/credentials.toml
```

Then tell the user, in your own words:

> I created your credentials file at
> `~/.cache/crowdstrike-falcon-fusion/credentials.toml`. Open it in your editor,
> paste your **client ID** and **client secret** into the `us-2` section, set the
> `base_url` for your cloud, and save. Then tell me to verify — don't paste the
> secret here.

**Offer to open the file for them.** Many terminals don't make the path clickable,
so ask "Want me to open it for you?" and, if yes, run the opener for their OS:

```bash
# macOS
open ~/.cache/crowdstrike-falcon-fusion/credentials.toml
# Linux
xdg-open ~/.cache/crowdstrike-falcon-fusion/credentials.toml
# Windows
explorer.exe %USERPROFILE%\.cache\crowdstrike-falcon-fusion\credentials.toml
```

Pick the command for the user's platform (check `uname` / the OS if unsure). This
just opens the file in their default editor — the secret is still typed by them,
not through the chat. Do **not** ask them to paste the secret into the chat.

## Step 3 — Verify connectivity

Once the user says they have saved the file, re-run the self-test:

```bash
python "${CLAUDE_PLUGIN_ROOT}/common/scripts/auth.py"
```

A successful run prints the resolved base URL, a masked client ID, and
"Authentication successful" for both the Workflows and Next-Gen SIEM clients. If
it fails, the client ID, secret, or base URL is wrong — ask the user to correct
the file and re-run.

## Credential resolution order

`auth.py` resolves credentials from the first source that supplies both an ID and
a secret:

1. **Environment variables** — `FALCON_CLIENT_ID`, `FALCON_CLIENT_SECRET`, and the
   optional `FALCON_BASE_URL`. Intended for CI, where the runner injects them.
2. **TOML profile file** — `~/.cache/crowdstrike-falcon-fusion/credentials.toml`,
   using the profile named by `FALCON_PROFILE` or the file's `default` key.

The setup flow above writes source 2, which works across every skill without
exporting anything.

## Multiple clouds (profiles)

Add more `[profile]` sections to the TOML file (for example `us-2` or `eu-1`) and
change the `default` key, or select one per run:

```bash
FALCON_PROFILE=eu-1 python "${CLAUDE_PLUGIN_ROOT}/common/scripts/auth.py"
```

## Required API scopes

The API client needs the **Workflow** scope (read/write) for workflow authoring
and deployment. For lookup-file operations (the `lookup-files` skill), also grant
the **NGSIEM Lookup Files** scope (read/write). Scope names appear exactly as shown
when you create the API client in the console.

Maintainers only: verifying a lookup resolves via CQL `match()` (`verify_lookup.py`
or `verify-workflows.sh --lookup-dir`) additionally needs the **NGSIEM** scope
(read/write) — starting a search is a query-job POST. Regular use of the skills
does not require it.
