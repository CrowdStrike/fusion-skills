---
name: lookup-enrichment
description: Create and use lookup tables with third-party data for automated detection enrichment in Next-Gen SIEM
source: https://www.crowdstrike.com/tech-hub/ng-siem/falcon-next-gen-siem-creating-a-lookup-table-with-3rd-party-data-for-automated-enrichment/
skills: [authoring, lookup-files]
capabilities: [workflow, lookup-file, enrichment]
---

## When to Use

User wants to enrich detections or events with third-party context held in a reference table —
IP reputation, user risk scores, asset metadata, or an IOC list. A lookup file stores the
reference data in Falcon Next-Gen SIEM, and a CQL `match()` query joins it onto live events so
each detection carries the extra context automatically.

## Pattern

1. **Create the lookup file.** Use the **lookup-files** skill to upload a CSV with a header row
   whose first column is the match key (e.g. `ip,reputation,source`). List first to avoid a
   silent overwrite (`list_lookups.py --search`), then `create_lookup.py --domain falcon`.
2. **Verify it landed.** `get_lookup.py --name "<file>.csv"` confirms the content before any
   query depends on it.
3. **Enrich with `match()`.** In a CQL query (Next-Gen SIEM search or an Event Query action in a
   workflow), join the lookup onto events:
   ```
   match(file="ip-reputation.csv", column=ip, field=src_ip, include=reputation)
   ```
   The matched columns appear as new fields on each event.
4. **Act on the enriched data.** In a workflow, branch on the enriched field with a CEL
   condition (e.g. notify or contain when `reputation == "malicious"`).
5. **Keep the table fresh.** Re-upload with `update_lookup.py` to refresh third-party data;
   the same filename keeps existing `match()` queries working.

## Key Actions

| Step | Tool / Action | Purpose |
|------|---------------|---------|
| Upload reference data | `create_lookup.py` (lookup-files skill) | Stores the third-party table in the `falcon` domain |
| Verify | `get_lookup.py` | Confirms content before queries depend on it |
| Join onto events | CQL `match()` | Enriches events with lookup columns |
| Branch on enrichment | Condition (CEL gateway) | Routes the workflow on the enriched field |
| Refresh | `update_lookup.py` | Updates data without breaking `match()` references |

**How they work together:** the lookup-files skill owns the *file* (create, verify, refresh);
the workflow (authoring skill) owns the *query and the response*. `match()` is the seam between
them — column names are case-sensitive and must match the CSV header exactly. See
`skills/lookup-files/references/cql-match-function.md` for full `match()` syntax.

## When to Route Elsewhere

Stay here for reference-table enrichment driven by `match()`. For live, per-event API lookups
(calling an external reputation API at detection time) use the **http-actions** pattern instead.
If the enrichment needs a custom UI or a serverless function to transform results, route to
foundry-skills (`crowdstrike-falcon-foundry`).
