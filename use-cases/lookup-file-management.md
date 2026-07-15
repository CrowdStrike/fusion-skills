---
name: lookup-file-management
description: Manage Next-Gen SIEM lookup files from inside a Falcon Fusion workflow — check metadata, then create-if-absent or overwrite/append/update — so a scheduled or email-driven job keeps a reference table current
source: https://www.reddit.com/r/crowdstrike/comments/1u2eg8d/workflow_wednesday_building_dynamic_lookup_files/
example: skills/authoring/examples/tutorials/intro-lookup-file-actions.yaml, skills/authoring/examples/tutorials/intro-receive-email-trigger.yaml
skills: [authoring, deployment, lookup-files]
capabilities: [workflow, lookup-file]
---

## When to Use

User wants a workflow that maintains a Next-Gen SIEM lookup file as a side effect of running —
creating the file the first time and overwriting, appending, or updating it on later runs. The
canonical shape is a metadata check followed by a branch: if the file does not exist, create it;
if it does, overwrite/append/update. This is grounded in two Content Library tutorial playbooks:

- **Primary reference** — `skills/authoring/examples/tutorials/intro-lookup-file-actions.yaml`
  (On demand trigger; `Get lookup file metadata` → conditions on `lookup_file_exists` and the
  chosen `operation` → create / overwrite / append / update). Read this for the full structure.
- **Email-driven variation** — `skills/authoring/examples/tutorials/intro-receive-email-trigger.yaml`
  creates a lookup file from a `.csv` email attachment via a Signal trigger (`event: MonitoredEmail`),
  gating on the attachment name ending in `.csv`.

**Real-world corroboration:** the "Building Dynamic Lookup Files" Workflow Wednesday post shows
the same metadata-check-then-branch pattern driven by a **Scheduled** trigger — pull Tor relay IPs
with a Cloud HTTP Request, then `Get lookup file metadata` → condition `lookup_file_exists == false`
→ `Create lookup file`, else `Overwrite lookup file`. Swap the On demand trigger in the primary
example for a Scheduled trigger to build it. The lookup-action IDs below come from the example YAML.

## Pattern

1. **Trigger.** The primary example uses an On demand trigger taking `filename` (must end in `.csv`)
   and an `operation` enum (`overwrite`/`append`/`update`). For the WW scenario use a Scheduled
   trigger; for the attachment case use a `Receive email` Signal trigger (`event: MonitoredEmail`,
   `version_constraint: ~1`).
2. **Check existence.** Call `Get lookup file metadata` (`lookup_file_repo: third-party`). It exposes
   `lookup_file_exists` and `lookup_file_name` for downstream references like
   `${data['GetLookupFileMetadata.lookup_file_name']}`.
3. **Branch on existence + operation.** Conditions gate on `GetLookupFileMetadata.lookup_file_exists`
   (`:false` vs `:true`) combined with the requested `operation` (e.g.
   `lookup_file_exists:true+operation:'overwrite'`).
4. **Write the file.** Route to the matching action: create when absent; otherwise overwrite, append,
   or update. `update` uses `lookup_file_csv_key_columns` (e.g. `Employee_ID`) to match rows.
   Content is passed as `lookup_file_content_text` (`content_type: text`) or, for the attachment case,
   `lookup_file_content_file` with `content_type: file`.
5. **Validate, then deploy.** Run `validate.py`, then import and release to the CID.

## Key Actions

All IDs and constraints are copied from `intro-lookup-file-actions.yaml` (create-from-attachment
and Print data are also confirmed in `intro-receive-email-trigger.yaml`). Note the same action ID
covers a few named variants (e.g. append CSV-only and append-to-existing share one ID).

| Action | `id` | version_constraint |
|--------|------|--------------------|
| Get lookup file metadata | `ace50afa2ea8438162f098b621f790fb` | `~1` |
| Create a new lookup file | `51c4db34ab30465f796d7550f3e3e97b` | `~1` |
| Overwrite lookup file | `3fa82584a1c9103b21fb80477102a05b` | `~1` |
| Append lookup file | `fe8acd4b4b2a30759745b2fcc6335306` | `~1` |
| Update lookup file (CSV, keyed) | `bd97b8cdd275c4aa09564189f725ae24` | `~1` |
| Print data | `aadbf530e35fc452a032f5f8acaaac2a` | `~1` |

The Receive email trigger uses `event: MonitoredEmail`, `type: Signal`, `version_constraint: ~1`,
and requires the O365 Monitored Mailbox app from the CrowdStrike Store. The Scheduled/Cloud HTTP
pieces of the WW variation are not in these two example YAMLs — discover the HTTP action ID with
`action_search.py` and confirm its `config_id` in the target CID; never invent one.

## When to Route Elsewhere

This keeps lookup-file maintenance inside the workflow. When you need to create, inspect, or refresh
lookup files outside a workflow (from the CLI, in bulk, or during development), use the **lookup-files**
skill's scripts directly. To *query* an existing lookup in CQL for detection enrichment, see the
`lookup-enrichment` use-case and the `match()` reference.
