# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.4.0] - TBD

### Added

- **What `match()` returns** — the CQL `match()` reference now says that leaving out `include=` adds every lookup column except the `column=` key, and that the key column is never added, so read the matched value from the event's `field=`.
- **`.distinct()` in the CEL reference** — the list de-duplication function is now documented with the List comprehensions, with a verified-live note that it keeps the first occurrence of each value in order.
- **HTTP error statuses don't fail the action** — `http-actions.md` and the HTTP Actions use case now say that a 404 or other error status completes the HTTP Action and the next node runs, so a "not found" is handled with a `response_status_code` condition, including inside loops. Verified live.
- **Recon notification trigger** — `trigger-types.md` covers `event: ReconNotification` (needs `version_constraint: ~1`). `validate.py` and `trigger_search.py --fields` flag the release-invalid `EDRs.Email` and `EDRs.HostName`, and point to the paginated Get exposed data records action, which can include a plaintext `password`.
- **1Password secret references for credentials** — `client_id` and `client_secret` in the TOML profile, and `FALCON_CLIENT_ID` / `FALCON_CLIENT_SECRET`, accept `op://` references, read with the 1Password CLI so the secret isn't stored in plain text. `/crowdstrike-falcon-fusion:setup` asks whether you use 1Password and writes the `op://` profile for you. Both references are read in one `op` call, so 1Password asks for approval at most once per script run (or once per session under `op run`).
- **`ca_bundle` profile option for TLS-inspecting proxies** — points the Python client at your organization's CA bundle, which fixes "could not obtain token" on networks that re-sign TLS traffic. It replaces an inherited `REQUESTS_CA_BUNDLE`, and the self-test shows which bundle is in use.
- **The auth self-test shows where credentials and the CA bundle came from** — environment variables or a named profile, so a leftover `FALCON_CLIENT_ID` or `REQUESTS_CA_BUNDLE` is obvious, and a token failure points at the CA bundle. On US-1, US-2, and EU-1, it prints the region FalconPy discovered instead of the default URL, and it says when it's waiting on a 1Password approval.
- **Package mirror support for the Python setup** — uv honors `PIP_INDEX_URL`, and a failed install suggests setting it (or `UV_DEFAULT_INDEX`) when public PyPI is blocked.

### Fixed

- **`import_workflows.py --replace` validates before it deletes** — a replacement that fails validation no longer leaves the CID with neither version.
- **The duplicate check fails closed** — if `import_workflows.py` can't list existing definitions it stops instead of importing anyway (`--skip-duplicate-check` overrides), and the name is parsed from the YAML so a trailing comment can't slip a duplicate past it.
- **Delete and release report HTTP errors** — `delete_workflow.py`, `release_workflow.py`, and the `--replace` delete now fail on any 4xx or 5xx status, not just a populated `errors` array.
- **`trigger_workflow.py --wait` exits non-zero when a run doesn't succeed** — it exits non-zero unless the run reaches `Completed` or `Succeeded`, and an execute response with no execution ID is an error.
- **The deployment skill says to release before testing** — the execute API rejects a disabled definition, so a test run needs a release first.
- **`--autofill` sends a real SHA-256 for `sha256` inputs** — EICAR's 64-character value instead of the 32-character MD5.
- **The auth self-test no longer prints part of the client secret** — it reports only whether a secret is set.
- **`trigger_search.py --fields` no longer marks `bool` fields as lists** — they show as scalars instead of a guessed `(list?)` from plural names.
- **`base_url` is optional on US-1, US-2, and EU-1** — FalconPy discovers those regions, so it's needed only on other clouds such as US-3 and GovCloud.
- **The setup guidance points to Client Management** — the console's new name for API clients and keys.
- **Python 3.13 through 3.15 is accepted** — the managed venv setup was capped at 3.14.
- **Setup doesn't open with an error on a fresh install** — it checks for a credentials file or env vars before running the self-test, so a first run goes straight to configuring credentials. When you use 1Password, it asks for the item name instead of searching your vaults, which was slow and needed extra approvals.
- **A failed first install no longer leaves a broken venv** — scripts used to run in a venv with no packages and fail with `No module named 'falconpy'`; the wrapper now rebuilds a venv whose install didn't finish.
- **API errors no longer look like empty results** — a 401, 403, 429, or 5xx while listing workflows, lookup files, or actions used to read as "nothing found", which let an import skip the duplicate check and told the agent an action didn't exist. The scripts now report the error and exit non-zero. `query_workflows.py --check-name` and `--check-yaml` exit 2 on an API error, since 1 already means the name is free.
- **`--replace` replaces every copy, and only exact names** — it deletes all definitions with exactly the workflow's name instead of one of them, and won't delete a name that differs only in case. It can't be combined with `--skip-duplicate-check`, and two files with the same name in one import batch no longer both get imported.
- **Deletes always need confirmation** — `delete_workflow.py --json` requires `--yes` instead of deleting without asking, and `--name` matches the exact name, so `--name probe` no longer deletes a workflow called `Probe`. `cleanup_workflows.py --pattern` and `--all-test` require `--yes` (or preview with `--dry-run`), and `--all-test` matches only the test harness's `<name>-run-<N>` names.
- **`trigger_workflow.py --json` prints only JSON on stdout** — progress lines go to stderr. Without `--params` or `--autofill` it exits with an error instead of waiting for interactive input. `--autofill` stops on an API error instead of executing with no inputs filled.
- **`monitor_execution.py` works when launched with a bare `python`** — it used to restart as `get_execution_results.py` when the managed venv had to take over.
- **`validate.py` reports a missing file instead of crashing**, and its `version_constraint` hint suggests the action's own major version rather than `~1`.
- **The cross-plugin advisory fires in Claude Code** — it now recognizes namespaced skill names such as `crowdstrike-falcon-fusion:workflows`.
- **The hooks always exit 0** — they used to error on every prompt when `jq` was missing or the input wasn't JSON. Their marker files moved from a fixed name in shared `/tmp` to a private per-user directory.
- **Broken commands in the docs** — the README Quick Start, the cache-refresh tip, `GEMINI.md`'s orchestrator import, the lookup enrichment use case's nonexistent `--domain` flag, and a reference to a nonexistent `execute.py` are fixed. The deployment skill documents the `--check-name` exit codes correctly and lists `export_workflow.py`.

## [1.3.0] - 2026-10-01

### Added

- **Event Query 200-row default cap documented.** The event-query reference, the event-queries use case, and the CSV export use case now note that Event Query results stop at 200 rows by default. End the query with `| tail(x)` or `| head(x)` to raise that to as many as 10,000 rows, the most a workflow can retrieve per search, or pass a `limit` when the query ends in `table(...)`. The CSV export use case's example now uses the `inline_configuration.config` shape that release requires, turns on CSV export so `file_csv` is populated, writes it with the real Create lookup file action and properties, and ends its query in `tail(10000)`. The note also covers the misdiagnosis to watch for: a workflow that accumulates query results into a `WorkflowCustomVariable` can look stuck at 200 rows, so check the query cap before suspecting variable or payload limits. The CSV export use case and the lookup-files format reference now point large or growing lookup files at incremental `update_lookup_file_entries()` updates in bounded batches instead of re-uploading the whole file.
- **`.transformList()` in the CEL reference** — the index-aware list transform (`list.transformList(i, v, expr)`) is now documented alongside `.map()`/`.filter()`, with guidance to prefer it over a workflow Loop when reshaping an array in a single action (e.g. adding multiple events/detections to a case, or building a delimited string with `.join(...)`).
- **Request Human Input Entra ID note** — the human-in-the-loop use case now records that the responder authenticates through Entra ID (including business users who are not Falcon administrators), so approvers must be able to sign in with Entra ID for the approval gate to work.
- **Agent invocation use case now shows the action YAML.** `charlotte-agent-invocation` documents the published AgentWorks agent action's real contract, each detail of which broke a real deploy or run in testing: the input key is `input` (not `prompt`); `version_constraint` is `~1`, because the agent action is versioned and `~0` fails the deploy with "Action was not found" even when the ID is exact; and the reply is read as `${data['<action>.response']}`, a plain string with no `cs.json.decode()`. It now includes a full invoke-and-print YAML example, and the `charlotte-ai-action.md` reference warns against carrying the LLM Completion action's `~0` over to an agent action. The use case also notes that a standalone workflow uses the CID-specific agent action ID, not the Foundry `ai_agents.<name>` alias, which only resolves inside a Falcon Foundry app.

### Fixed

- **The skill-router reminder no longer follows you into unrelated sessions.** After a prompt matched Fusion keywords, the router added "Fusion plugin reminder" to every tool call until a skill loaded, and because its marker file was global and never reset, the reminder leaked into later prompts and other sessions. The marker is now per session and reset on every prompt, and the reminder fires once per detected prompt. Loading a skill no longer deletes the marker, so the cross-plugin note about standalone Fusion workflows fires reliably when a Foundry skill loads during a Fusion prompt.
- **The skill router no longer fires on prompts that only mention the repo or a generic workflow.** Its keyword matcher treated bare `fusion` as a Fusion noun (so "the fusion-skills repo" or "the fusion plugin" matched) and accepted an action verb anywhere before a noun anywhere (so "run the tests…", "write a changelog…" matched), while `create workflow` / `build a workflow` matched CI/GitHub Actions workflows and other non-Fusion work. The matcher now requires a Fusion-qualified noun within three words of an action verb (in either order), drops bare `fusion` and the over-broad `write`/`run` verbs, and no longer treats a generic "create/build a workflow" as Fusion intent unless the prompt is about security response (a detection, incident, containment, Okta, severity, and so on) and doesn't mention CI, GitHub, GitLab, or a Foundry app capability, so "create a workflow triggered by an EPP detection alert" still matches. Phrases and nouns are whole words, so "soaring", "redeploy to cider", "my_action_search_helper", and "create an ansible playbook" do not match, and "run fusion plugin tests" is not an explicit skill request. "crowdstrike actions we should automate" still matches. Direct product phrases (`fusion workflow`, `soar workflow`, `build a playbook`, …) still trigger.
- **Bundled hook guidance now works across Claude Code, Codex, and Cursor.** The router no longer tells another assistant to use Claude Code's `Skill` tool, cross-plugin routing recognizes an enabled Foundry plugin in Codex's configuration and in Cursor's plugin cache, and install guidance points each host at its own plugin browser. A Codex turn does not treat Claude's plugin registry as an install, so a sibling disabled in Codex is still offered as an install. Cursor injects top-level `additional_context`, so the hooks emit that shape there. A `.cursor-plugin/plugin.json` points Cursor at `hooks/hooks-cursor.json` so it does not try to parse the Claude hook manifest. Antigravity's plugin directory is recognized the same way. The README also documents Codex's `/hooks` trust step.
- **The workflows skill uses `~<major>` for `version_constraint`.** It still said `~1` for any action with a `semantic_version`, contradicting the authoring skill; Charlotte AI at `0.0.100` needs `~0`.
- **One copy of the workflow reference docs.** The workflows skill's copies of the authoring references had drifted: its `trigger-types.md` lacked the release-verified NG-SIEM trigger section and its `json-structure.md` used `~2` for Send email instead of `~1`. The workflows skill now reads the authoring references directly.
- **`validate.py` no longer blocks the supported way to consume Inline.Python JSON output.** The validator flagged `${cs.json.decode(data['<action>.output_stdout']).field}` as a release-time failure, but a live probe (released and executed clean) confirmed the platform accepts it — matching the official SOAR guidance to `print(json.dumps(result))` and decode downstream with `cs.json.decode(...)`. The false-positive guard is removed and `inline-python-action.md` now documents the decode-and-dot-index pattern (guard with `cs.json.valid(...)` when stdout may be non-JSON).
- **`validate.py` no longer truncates hyphenated `WorkflowCustomVariable` names, false-flagging them as undeclared.** Its reference-scanning regex (`WorkflowCustomVariable\.(\w+)`) stopped at the first `-`, so a hyphenated variable like `resolved-user-id` was checked against declared names as just `resolved` and reported as undefined even though it was declared in full. The character class now includes `-`, extending the match only when the character after the hyphen is a letter or underscore (not a digit) — so a hyphenated name still matches in full, while unspaced CEL subtraction like `WorkflowCustomVariable.retries-1` (i.e. "retries minus 1") isn't swallowed into a single bogus token.
- **`validate.py` recognizes `Inbound webhook` as a valid trigger type.** Fusion added a fifth `trigger.type` value alongside On demand, Signal, Scheduled, and SubModel, but the validator's allowlist still had only four — so a valid webhook-triggered workflow was rejected with `Invalid trigger type ... Must be one of`. The type is now accepted (it carries a `webhook_config` block and no `event` field), and the trigger-type references document its shape.
- **`trigger_search.py` lists and describes the `Inbound webhook` trigger type.** Follow-up to the validator fix: the discovery tool's built-in catalog still had only four types, so `--list` omitted Inbound webhook and `--type "Inbound webhook"` errored, contradicting the authoring skill that points users at it. The catalog now includes it with its `webhook_config` shape.
- **`validate.py` recognizes `summary` as a valid top-level key.** Fusion supports a console execution-summary template (`summary: |- ${data['WorkflowCustomVariable.workflow-result']}...`), but the validator's top-level allowlist didn't include it, so an otherwise-valid workflow using it was rejected with `Unknown top-level key(s): summary`. `summary` is now accepted alongside the other console-export keys (`disconnected_nodes`, `output_fields`).
- **`validate.py` flags a fabricated `@example.com`-style email seeded into a `WorkflowCustomVariable`.** A model that can't source a real recipient sometimes assigns a placeholder address to a custom variable (a `CreateVariable` `default` or an `UpdateVariable` setter) and references it in a Send email `to:`. The email dead-ends at runtime — and because `to:` then holds only a `${data[...]}` reference, it slipped past the existing recipient check. The validator now flags the fake value at its source; declare the variable without a hardcoded default (configured in the console) or bind it from a trigger parameter.

## [1.2.0] - 2026-09-08

### Added

- **Installable from the OpenAI/Codex, Cursor, and GitHub Copilot marketplaces.** Beyond the Anthropic marketplace, the plugin is now published to the OpenAI/Codex curated CLI marketplace (`codex plugin add crowdstrike-falcon-fusion@openai-api-curated`; ChatGPT-authenticated Codex installs via `/plugins`), the Cursor marketplace, and the GitHub Copilot (awesome-copilot) directory. The skills-only bundle now ships the square interface icon the OpenAI directory requires, and the README install table links each live listing plus the Falcon Fusion workflows walkthrough.
- **CEL timestamp and time-math functions** in the CEL expressions reference — `cs.timestamp.parse(str, 'RFC3339')` plus the live-verified Unix-epoch-millisecond idiom (`int((… - timestamp('1970-01-01T00:00:00Z')).getMilliseconds())`) and `duration(...)` windowing. Includes a case-management pattern for building dynamic, time-scoped Event Search deep links from a detection's `Trigger.ObservedTime`.

### Fixed

- **`monitor_execution.py` and `trigger_workflow.py --wait` no longer misreport a successful execution as a failure/timeout.** Both polled only for a `Succeeded` terminal status, but live testing against the execution-results API showed a normal successful execution reports `Completed` — so every successful run polled to its full timeout and exited non-zero. `get_execution_results.py`'s shared `TERMINAL_STATUSES` now includes `completed`, and a new `SUCCESS_STATUSES` set (both scripts' single source of truth for the exit-code decision) treats `succeeded` and `completed` as success.
- **`validate.py` now flags `WorkflowCustomVariable.<name>` references to variables that nothing declares** — a release-only failure. A reference to a custom variable that no `CreateVariable` (or `UpdateVariable` setter) declares imports and validates cleanly, then fails at release with `property "..." contains unknown variable "WorkflowCustomVariable.<name>"`. The validator now collects declared variable names and reports an undeclared reference before you deploy.
- **`validate.py` no longer rejects valid action IDs that aren't 32-char hex.** The action-ID check assumed every ID was a 32-character hex string (or a `<hex>_<hex>` / `<hex>~<hex>` compound), but real catalog actions carry other shapes — a 26-character ULID joined to a hex id (custom IOC / API-integration actions), unequal compound halves (event query actions), and longer hex strings (RTR actions). Those imported fine yet were flagged as invalid locally. IDs are now treated as opaque catalog identifiers, so the check still rejects placeholders (UPPER_SNAKE tokens, punctuation, all-same-character) without blocking real IDs. Docs updated to describe action IDs as identifiers you look up rather than "32-char hex".

## [1.1.0] - 2026-08-19

### Added

- **`foundry-redirect` skill** — declines Falcon Foundry app requests (a UI page, serverless function, collection, `manifest.yml`, or custom third-party API integration) and points to the sibling `crowdstrike-falcon-foundry` plugin. Its description matches Foundry-app language, so the redirect is reachable on every assistant, and it yields to the Foundry plugin's own skill when that plugin is installed.
- **Skills run on assistants other than Claude Code** (Codex, Copilot CLI, Cursor, Antigravity). Scripts resolve their own location, so the managed-venv bootstrap works when a skill is launched through a `~/.agents/skills/` symlink instead of failing with a missing-`falconpy` error. Each `SKILL.md` shows a portable invocation that works whether or not the Claude-only `${CLAUDE_PLUGIN_ROOT}` is set.
- **Agent Plugins manifests for non-Claude assistants** — A root `plugin.json` conforming to the [Agent Plugins](https://agent-plugins.org) 1.0.0 spec and a Codex-flavored `.codex-plugin/plugin.json`, so Codex, Copilot CLI, Cursor, and Antigravity CLI can discover the plugin alongside the existing Claude manifest. CI validates both and keeps their versions in lockstep with `.claude-plugin/plugin.json`.
- **Install instructions for five AI coding assistants** — Claude Code, Codex, Copilot CLI, Cursor, and Antigravity CLI each get documented install commands (`--plugin-dir` for Claude Code, Copilot CLI, and Cursor; a `~/.agents/skills/` symlink for Codex and Antigravity CLI). In a live-tenant run, Claude Code, Codex, Copilot CLI, and Cursor each authored a valid workflow from the canonical prompt and imported it to the tenant (left disabled until released). Antigravity CLI loads the skills, but its weekly quota was exhausted before a full author-and-import run, so its end-to-end path is not yet verified.
- US-3 cloud region in credential setup: a `[us-3]` profile example (`https://api.us-3.crowdstrike.com`) in the setup skill's multi-cloud block and the README region notes, alongside a `[us-gov-1]` example that was also missing.
- Throttling reference in the execution skill: explains that a workflow stuck "in progress" may be throttled (Fusion paces an action past a volume limit, auto-retrying up to 6 hours) rather than failed, how to recognize it on the execution detail view, and when sustained throttling signals a workflow-design issue.
- Deduplicate and Rate Limit action reference plus a worked tutorial example, covering all six Deduplicate and four Rate Limit activities — the `definition`/`cid` scope values (labeled "Workflow" and "CID" in the console), the atomic claim, metadata handoff, and the builder's save-time validation. The example deduplicates third-party NG-SIEM detections into a single case.

### Fixed

- Authoring-doc corrections from a Fusion engineer's review: event and system variables (`Trigger.CID`, `Workflow.Execution.ID`, `Workflow.Definition.Name`, etc.) are documented in the `${data['...']}` form like any other field, and the action `name:` is described as a relabelable display label — renaming it doesn't break references, which resolve by node key and action `id`.
- **Documented that `version_constraint` decides an action's output-path shape.** Unpinned, a reference carries the action's namespace (`${data['DeviceQuery.Device.query.devices']}`); pinned at `~1` the same field collapses (`${data['DeviceQuery.devices']}`). The two forms are mutually exclusive, so pinning an action without shortening its `${data['...']}` references imports cleanly and then fails at release with `property "..." contains unknown variable`. Only class-based actions require a `version_constraint` at import; others release fine without one and keep the longer paths.
- **`validate.py` now catches four release-only failures** — workflows that import cleanly and pass server-side validation, then fail when you release them:
  - a pinned action referenced by its long, unpinned output path (the mismatch documented above);
  - MITRE ATT&CK trigger fields (`Trigger.Detection.MitreAttack.Tactic`/`.Technique`) on the NG-SIEM trigger — advertised by trigger discovery but rejected at release; source them from the hydrated detection instead (`trigger_search.py --fields` now marks them "NOT release-valid");
  - a `default: true` gateway node with no expression or `else:` (`exclusive gateway ... has no condition set`); put the fallthrough in an `else:` on the expression-bearing condition;
  - a missing/empty or fake-domain `to` recipient on the Send email and Request human input actions; use a variable the user supplies (trigger parameter, `WorkflowCustomVariable`, or a prior step's output), not a hardcoded `@example.com`-style address that no CID delivers to.
- Fixed three shipped examples so they release cleanly: `intro-receive-email-trigger`, `intro-lookup-file-actions`, and `network-contain-endpoint-on-detection` (converted off `default: true` gateways, with its approval emails taking a configurable `${WorkflowCustomVariable.approver_email}` recipient).

## [1.0.1] - 2026-08-07

### Fixed

- Relocated helper scripts from `bin/` to `scripts/` so the plugin installs on claude.ai and Cowork. A top-level `bin/` directory is added to the CLI's PATH but isn't shown on the web admin approval surface, so those hosts rejected the plugin. The scripts are internal helpers, not entry points, and moving them out of `bin/` clears the block. Command-line installs were unaffected.

### Changed

- Renamed the deployment skill from `deploy` to `deployment` so it matches its sibling skills (`authoring`, `execution`) and the `foundry-skills` convention. The command is now `/crowdstrike-falcon-fusion:deployment`; the picker no longer rewrites `:deploy` to `:deployment` on submit.

## [1.0.0] - 2026-07-30

First public release of Falcon Fusion Skills — AI coding assistant skills for building CrowdStrike Falcon Fusion workflows. Describe the automation you want in plain language and your assistant discovers the real action IDs from your tenant, writes the YAML, validates it against the platform schema, imports it to your CID, and runs it.

### Skills

- **workflows** — Orchestrator and single entry point. Reads your intent and coordinates authoring, deployment, and execution.
- **authoring** — Live action discovery, workflow YAML authoring, and schema validation. Covers conditional routing, HTTP Actions, Event Queries, inline Python, and Charlotte AI summaries.
- **deployment** — Import to a CID, release, delete, and duplicate cleanup.
- **execution** — Trigger a workflow, monitor it, and retrieve results for debugging.
- **lookup-files** — Create, list, update, and delete the Next-Gen SIEM lookup files behind CQL `match()` queries.
- **setup** — Guided credential configuration stored in a local profile, with the secret typed into your own editor rather than the chat.

### Validation

- **Local validator** — Catches the mistakes that otherwise surface only at release time or as a silently empty result: bad action IDs, malformed references, the wrong trigger shape, and an Event Query pointed at data that isn't reliably there. You find out in seconds instead of after a failed deploy, and every check traces back to a real failure we hit and fixed.

### Examples

- 25 workflow examples from the CrowdStrike Content Library, spanning threat intel, identity response, notifications, response actions, Next-Gen SIEM, and tutorials. Every one imports cleanly and opens in the Falcon visual editor, so they double as working references.

### Use Cases

- 15 pattern-matchable use cases that map common requests (enrich a detection, close duplicate detections, respond to an NG-SIEM detection) to the workflow that solves them, each grounded in a bundled example or a published CrowdStrike walkthrough.

### Editor and CLI Support

- Tested with Claude Code. Experimental setup instructions for Codex, Copilot CLI, Cursor, and Antigravity CLI, written from each tool's own documentation but not yet verified end to end. The skills are plain markdown, so any assistant that reads local files can use them.
