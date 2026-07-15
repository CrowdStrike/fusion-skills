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
2. **Add the Event Query action.** Configure an `Inline.QueryEvent` action with your LogScale
   query. Set **"Output files only: false"** so the JSON result fields stay populated for
   downstream actions in addition to the CSV file.
3. **Wire the CSV to a lookup file.** The Event Query exposes a `file_csv` output. Pass it into a
   Create Lookup File action:

   ```yaml
   actions:
     QueryEvents:
       id: cdf5c3e0d69f156eaaf56c1f5d3f1b66   # Event Query (Inline.QueryEvent)
       version_constraint: ~1
       properties:
         query: "#event_simpleName=ProcessRollup2 | select(ComputerName, FileName)"
         time_range: "24h"
     CreateLookup:
       id: <create-lookup-file-action-id>     # discover via action_search.py
       properties:
         file_csv: ${data['QueryEvents.file_csv']}
         filename: "process_export.csv"
         repository: "search-all"
   ```

4. **Enrich later with match().** Once the CSV lands in the lookup table, join it to events:
   `| match(file="process_export.csv", field=ComputerName)`.
5. **Validate, then deploy.** Run `validate.py`, then import and release to the CID.

## Key Actions

| Action | Type | Purpose |
|--------|------|---------|
| Event Query | `Inline.QueryEvent` | Runs the query and exposes a `file_csv` output. `version_constraint: ~1` |
| Create Lookup File | Lookup action | Writes the CSV to a Next-Gen SIEM lookup table (see the lookup-files skill) |

## Common Pitfalls

- **Empty JSON results downstream:** if "Output files only" is `true`, only the CSV file is
  available and JSON result fields are empty. Set it to `false` to keep both.
- **Lookup file limits:** 10 MB max, 5 uploads per 30 seconds. Split large exports across files.

## When to Route Elsewhere

Use the Fusion workflow path when the query and export live inside a single pipeline. Build a
Foundry function (route to foundry-skills) when you need Python-level control — the post uses
`FoundryLogScale.execute_dynamic()` (sync or async), `csv.DictWriter(extrasaction='ignore')` for
conversion, and `upload_file()` / `PutObject()` for delivery to lookup files or collections.
