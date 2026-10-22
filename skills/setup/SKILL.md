---
name: setup
description: >
  Configure CrowdStrike Falcon API credentials for the fusion-skills plugin.
  TRIGGER when user asks to set up credentials, configure API access,
  or runs into authentication errors.
version: 1.3.0
updated: 2026-10-10
tags: [fusion, setup, credentials, configuration]
author: CrowdStrike
license: MIT
compatibility: Claude Code >=1.0
metadata:
  category: configuration
---

# Falcon Fusion Credential Setup

> Your role here is **credential setup assistant**.
>
> You configure the Falcon API credentials every other skill depends on. These
> credentials grant workflow and SIEM access to a live CID.
>
> **Required steps:**
> 1. Check whether credentials already resolve (Step 1). If they do, you are done.
> 2. If not, ask whether the user keeps the credentials in 1Password. If they do, write a profile of `op://` references (Step 2a). Otherwise, create the credentials file from the template (Step 2b) and ask the user to paste their ID and secret into it **using their own editor**.
> 3. Verify connectivity (Step 3).
>
> **Don't:**
> - Ask the user to type or paste their client secret **into the chat**. It would
>   land in the conversation transcript. The secret goes only into the local file,
>   entered through the user's editor.
> - Print, echo, or repeat a secret you happen to see in the file.
> - Search 1Password yourself (`op item list`, `op item get`). Ask the user for the item name instead. `op item get` without a `jq` filter also prints the secret values.
> - Suggest `export FALCON_CLIENT_SECRET=...` for interactive use — it leaks the
>   secret into shell history. (Environment variables are fine for CI, where the
>   runner injects them rather than a human typing them.)

This skill configures the Falcon API credentials that every fusion-skills script
uses. Credentials are stored in a per-profile TOML file at
`~/.cache/crowdstrike-falcon-fusion/credentials.toml` (multi-cloud capable), and
the secret is entered through the user's own editor (or kept in 1Password and referenced from the file) — never through the chat.

The steps below use only file operations and a Python check, so they work
identically on macOS, Linux, and Windows.

> **Running the scripts.** Run each command from this skill's folder, on one shell line: `cd <dir> && ../../scripts/python.sh ../../common/scripts/auth.py`. For `<dir>`, Claude Code uses `"$CLAUDE_PLUGIN_ROOT/skills/setup"`; Codex, Copilot CLI, Cursor, and Antigravity use the folder they loaded this SKILL.md from (e.g. `~/.agents/skills/setup`). The wrapper bootstraps its own Python venv.

## Step 1 — Check for existing credentials

First check, without running any script, whether there's anything to test: does `~/.cache/crowdstrike-falcon-fusion/credentials.toml` exist, or are `FALCON_CLIENT_ID` and `FALCON_CLIENT_SECRET` set? Check the variables without printing their values. If neither is the case, nothing is configured. Go straight to Step 2a, and don't run the self-test, because it would only report the missing credentials as an error.

Otherwise, run the auth self-test:

```bash
../../scripts/python.sh ../../common/scripts/auth.py
```

- **"Authentication successful"** for both clients → credentials are configured. Report success and stop.
- **An error about missing credentials** (for example, the file exists but has no usable profile) → continue to Step 2a.
- **An authentication failure** (creds present but rejected) → the values are wrong; go to Step 2a or 2b and have the user correct them.

## Step 2a — Offer 1Password

Before writing the template, ask: "Do you keep your Falcon API client ID and secret in 1Password, or want to?" If they don't, or aren't sure, skip to Step 2b.

If they do, the profile holds 1Password secret references instead of the values. References aren't secrets, so you can write the file yourself and the user doesn't need to edit it. Build the references from the user's answers. Don't call `op` to look up or search for the item: every call is slow and may need its own approval, and the self-test in Step 3 verifies the references anyway.

1. Ask whether they already have a 1Password item for the API client. If not, walk them through creating one:
   1. In the Falcon console, go to **Support and resources** > **Client Management** > **Create API client**. Name it something specific, such as "Falcon Fusion skills", so it isn't mistaken for another client and reused with broader scopes. Grant **Workflow** (read and write) and, for the `lookup-files` skill, **NGSIEM Lookup Files** (read and write). The secret is shown only once, so keep the page open.
   2. In the 1Password app, create an **API Credential** item with the same name as the API client (for example, "Falcon Fusion skills"). Paste the client ID into `username` and the client secret into `credential`, and save.

   They paste both values into 1Password, never into the chat.
2. Ask for the item's name, its vault, and which cloud they're on (US-1, US-2, EU-1, US-3, or GovCloud). The CLI can name a vault differently from the app. In a 1Password Business account, the personal vault the app shows as "Employee" is `Private` to `op`, so use `Private` for it.
3. Build the references as `op://<vault>/<item>/username` and `op://<vault>/<item>/credential`. On a Login item instead of an API Credential item, the secret field is `password`.
4. If `~/.cache/crowdstrike-falcon-fusion/credentials.toml` doesn't exist, write it with that profile. If it does, add the profile as a new section and leave the existing ones alone. For example:

   ```toml
   default = "us-2"

   [us-2]
   client_id = "op://Private/Falcon Fusion skills/username"
   client_secret = "op://Private/Falcon Fusion skills/credential"
   ```

   Set `base_url` only on clouds other than US-1, US-2, and EU-1 (see the template in Step 2b). Apply the same `chmod` as in Step 2b, then go to Step 3. Tell the user that 1Password will ask them to authorize the CLI when the self-test runs.

If the self-test reports that a vault, item, or field doesn't exist, ask the user to run the reference-listing command in [Keeping the secret in 1Password](#keeping-the-secret-in-1password) in their own terminal and paste its output, which contains only `op://` paths. Then fix the profile. `RequestDelegatedSession: cannot setup session` means the 1Password app needs them to sign in to their account again.

See [Keeping the secret in 1Password](#keeping-the-secret-in-1password) for how `auth.py` reads the references.

## Step 2b — Create the credentials file and have the user fill it in

Create `~/.cache/crowdstrike-falcon-fusion/credentials.toml` **only if it does not
already exist** (never overwrite existing profiles). Write this template with the
Write tool:

```toml
# CrowdStrike Falcon API credentials for fusion-skills.
# Fill in client_id and client_secret below, then save this file.
# Either can be a 1Password reference instead, e.g. "op://Private/Falcon Fusion skills/credential".
#
# Create an API client in the Falcon console:
#   Support and resources -> Client Management -> Create API client
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
# base_url is optional on US-1, US-2, and EU-1: FalconPy discovers the region.
# Set it for any other cloud, such as US-3 or GovCloud.
base_url = "https://api.us-2.crowdstrike.com"

# Add more clouds as needed (change `default` above to switch):
# [us-1]
# client_id = ""
# client_secret = ""
# base_url = "https://api.crowdstrike.com"
#
# [us-3]
# client_id = ""
# client_secret = ""
# base_url = "https://api.us-3.crowdstrike.com"
#
# [eu-1]
# client_id = ""
# client_secret = ""
# base_url = "https://api.eu-1.crowdstrike.com"
#
# [us-gov-1]
# client_id = ""
# client_secret = ""
# base_url = "https://api.laggar.gcw.crowdstrike.com"
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
> paste your **client ID** and **client secret** into the `us-2` section (on any other
> cloud, such as US-3 or GovCloud, also set `base_url`), and save. Then tell me to verify — don't paste the
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
../../scripts/python.sh ../../common/scripts/auth.py
```

A successful run prints where the credentials came from (environment variables or a named profile, marked "(1Password)" when it used `op://` references), the resolved base URL, a masked client ID, and "Authentication successful" for both the Workflows and Next-Gen SIEM clients. If it fails, the client ID, secret, or base URL is wrong. If the source isn't the one the user expects, a `FALCON_CLIENT_ID`/`FALCON_CLIENT_SECRET` left in the environment is outranking the profile. Otherwise, ask the user to correct the file and re-run. If the values are right and it still reports "could not obtain token", the network may be re-signing TLS traffic; see [TLS-inspecting proxies](#tls-inspecting-proxies).

## Keeping the secret in 1Password

If the user keeps API secrets in 1Password and can't store them in plain text, `client_id` and `client_secret` can each hold a 1Password secret reference instead of the value (the same works for `FALCON_CLIENT_ID` and `FALCON_CLIENT_SECRET`).

Have the user create an **API Credential** item in the 1Password app (for example, "Falcon Fusion skills"), put the client ID in its `username` field and the client secret in its `credential` field, and then reference both fields in the profile:

```toml
[us-2]
client_id = "op://Private/Falcon Fusion skills/username"
client_secret = "op://Private/Falcon Fusion skills/credential"
```

`auth.py` reads each `op://` value with the [1Password CLI](https://developer.1password.com/docs/cli/get-started/) and keeps the results in memory only. The user needs `op` installed and signed in. Both references are read in one `op` call, so 1Password asks for approval at most once per script run. A reference takes the form `op://<vault>/<item>/<field>`; see [secret reference syntax](https://developer.1password.com/docs/cli/secret-reference-syntax/). Plain values keep working as before.

Building a workflow runs many scripts, so in a long session those approvals add up. To approve once per session instead, the user can start the assistant under `op run` with the references in environment variables. `op run` resolves them at launch, and the scripts then read plain values without calling `op`:

```bash
FALCON_CLIENT_ID="op://Private/Falcon Fusion skills/username" \
FALCON_CLIENT_SECRET="op://Private/Falcon Fusion skills/credential" \
op run -- claude
```

Substitute the assistant's own command for `claude`. The tradeoff: the resolved secrets are in the environment of the assistant and every process it starts for the whole session.

The CLI's vault and field names can differ from what the app shows (a vault the app labels "Employee" may be `Private` to `op`, and a Login item names its secret field `password` rather than `credential`). Have the user print the exact references with this command, which shows no secret values. Don't run `op item get` without the `jq` filter, because that prints them:

```bash
op item get "<item name>" --format json | jq -r '.fields[].reference'
```

If `op` fails with `RequestDelegatedSession: cannot setup session`, the 1Password app needs the user to sign in to their account again.

## TLS-inspecting proxies

On a network that intercepts and re-signs TLS traffic, the self-test fails with "could not obtain token" even though the credentials are correct. Point the profile at the organization's CA bundle (a PEM file):

```toml
[us-2]
ca_bundle = "~/certs/corporate-ca.pem"
```

`auth.py` exports it as `REQUESTS_CA_BUNDLE` for that run, replacing any `REQUESTS_CA_BUNDLE` inherited from the shell, since a stale one left over from another tool is a common cause of this failure. The self-test's `CA bundle` line shows the bundle in use and where it came from, for example `~/certs/corporate-ca.pem (profile ca_bundle, overrides REQUESTS_CA_BUNDLE)`.

If the plugin's Python setup can't reach public PyPI on the same network, set `PIP_INDEX_URL` (or `UV_DEFAULT_INDEX` for uv) to the organization's package mirror and run `../../scripts/setup-python-venv.sh` again.

## Credential resolution order

`auth.py` resolves credentials from the first source that supplies both an ID and
a secret:

1. **Environment variables** — `FALCON_CLIENT_ID`, `FALCON_CLIENT_SECRET`, and the
   optional `FALCON_BASE_URL`. Intended for CI, where the runner injects them.
2. **TOML profile file** — `~/.cache/crowdstrike-falcon-fusion/credentials.toml`,
   using the profile named by `FALCON_PROFILE` or the file's `default` key.

Either source can use `op://` references (see [Keeping the secret in 1Password](#keeping-the-secret-in-1password)).

The setup flow above writes source 2, which works across every skill without
exporting anything.

## Multiple clouds (profiles)

Add more `[profile]` sections to the TOML file (for example `us-2` or `eu-1`) and
change the `default` key, or select one per run:

```bash
FALCON_PROFILE=eu-1 ../../scripts/python.sh ../../common/scripts/auth.py
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
