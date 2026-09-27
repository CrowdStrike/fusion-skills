---
name: export-query-results-csv
description: Export Falcon Next-Gen SIEM query results to CSV inside a Falcon Fusion workflow using the Event Query action's file_csv output, then write it to a lookup file for CQL match() enrichment
source: https://www.crowdstrike.com/tech-hub/ng-siem/exporting-falcon-next-gen-siem-query-results-to-csv-with-falcon-foundry/
skills: [authoring, deployment, execution]
capabilities: [workflow, event-query, csv-export]
---

## When to Use

User wants a workflow that runs a Next-Gen SIEM query, gets the results as CSV, and writes them
to a lookup table so later CQL `match()` queries can enrich detections. The source post covers
both a Foundry function approach and a Fusion workflow approach; this use case is the workflow
path.

## Pattern

1. **Choose a trigger.** Scheduled for periodic exports, or On demand for ad-hoc runs.
2. **Add the Event Query action.** Configure an `Inline.QueryEvent` action. The query and its
   window live in `inline_configuration.config` (`search_query`, `repo_or_view`, `start`, `end`),
   not in `properties`; see
   [event-query-action.md](../skills/authoring/references/event-query-action.md) for the full
   shape. Set `workflow_export_event_query_results_to_csv: true` so the action produces its
   `file_csv` output, and keep `output_files_only: false` so the JSON result fields stay populated
   for downstream actions too. End the query in `| tail(x)` so the export isn't cut off at the
   default 200 rows.
3. **Wire the CSV to a lookup file.** Pass `file_csv` into the Create lookup file action with
   `lookup_file_content_type: file`:

   ```yaml
   actions:
     QueryEvents:
       id: cdf5c3e0d69f156eaaf56c1f5d3f1b66   # Event Query (Inline.QueryEvent)
       class: Inline.QueryEvent
       name: Export process events
       version_constraint: ~1
       next:
         - CreateLookup
       properties:
         logscale_search_start_time: 1 day
         output_files_only: false                          # keep the JSON results too
         workflow_csv_header_fields: []                    # empty = all fields
         workflow_export_event_query_results_to_csv: true  # populates file_csv
       inline_configuration:
         config:
           description: ''
           end: now
           repo_or_view: search-all
           search_name: Export process events
           search_query: '#event_simpleName=ProcessRollup2 | select([ComputerName, FileName]) | tail(10000)'
           start: 24h
           tags: []
     CreateLookup:
       id: 51c4db34ab30465f796d7550f3e3e97b   # Create lookup file
       name: Create lookup file
       version_constraint: ~1
       properties:
         lookup_file_content_file: ${data['QueryEvents.file_csv']}
         lookup_file_content_type: file
         lookup_file_name: process_export.csv
         lookup_file_repo: search-all
   ```

4. **Enrich later with match().** Once the CSV lands in the lookup table, join it to events:
   `| match(file="process_export.csv", field=ComputerName)`.
5. **Validate, then deploy.** Run `validate.py`, then import and release to the CID.

## Key Actions

| Action | Type | Purpose |
|--------|------|---------|
| Event Query | `Inline.QueryEvent` | Runs the query and, with CSV export on, exposes a `file_csv` output. `version_constraint: ~1` |
| Create lookup file | `51c4db34ab30465f796d7550f3e3e97b` | Writes the CSV to a Next-Gen SIEM lookup table (see the lookup-files skill). `version_constraint: ~1` |

## Common Pitfalls

- **Empty JSON results downstream:** if "Output files only" is `true`, only the CSV file is
  available and JSON result fields are empty. Set it to `false` to keep both.
- **Truncated exports:** Event Query results stop at 200 rows unless the query ends in
  `| tail(x)` / `| head(x)` (up to 10,000) or raises `table(...)`'s `limit`, so a lookup file built
  from an uncapped query silently misses rows. See the row-cap note in
  [event-query-action.md](../skills/authoring/references/event-query-action.md).
- **Lookup file limits:** 10 MB max, 5 uploads per 30 seconds. Split large exports across files.

## When to Route Elsewhere

Use the Fusion workflow path when the query and export live inside a single pipeline. Build a
Foundry function (route to foundry-skills) when you need Python-level control — the post uses
`FoundryLogScale.execute_dynamic()` (sync or async), `csv.DictWriter(extrasaction='ignore')` for
conversion, and `upload_file()` / `PutObject()` for delivery to lookup files or collections.
