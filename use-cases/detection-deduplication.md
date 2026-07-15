---
name: detection-deduplication
description: Automatically identify and close duplicate Next-Gen SIEM detections by running an Event Query that uses defineTable()/match() to find prior alerts sharing the same underlying events, then tag, comment, and set the duplicate detection to closed while recording the original detection it duplicates
source: CrowdStrike Content Library playbook "Close Duplicate Next-Gen SIEM Detections Automatically" (https://falcon.crowdstrike.com/login/?unilogin=true&next=/content-library/details/global:fusion_playbook:e066f4a1dee649c4965c31f76838cad2)
example: skills/authoring/examples/ngsiem/close-duplicate-detections.yaml
skills: [authoring, deployment, execution]
capabilities: [workflow, event-query, deduplication]
---

## When to Use

User wants to cut alert fatigue by auto-closing Next-Gen SIEM detections that duplicate an earlier
one. This is common when overlapping correlation-rule intervals and search windows cause the same
underlying activity to fire repeatedly (the exact pain called out in the Workflow Wednesday
"NG-SIEM Correlation Rule Alerts" post). The workflow queries the event store for previous alerts
that share the current detection's child event IDs, and if it finds one, it tags, comments, and
closes the duplicate.

This is grounded in the real Content Library playbook
`skills/authoring/examples/ngsiem/close-duplicate-detections.yaml`. Read that file for the full
structure: an NG-SIEM detection trigger, an Event Query (`Inline.QueryEvent`) whose CQL uses
`defineTable()` and `match()` for dedup, a CEL condition on the results, and a sequential loop that
tags/comments/closes each duplicate. Note for Falcon Complete customers: this playbook can limit
detection visibility for the Falcon Complete team; consult your Security Advisor before enabling.

## Pattern

1. **Trigger on the NG-SIEM detection (Signal).** The trigger is `Signal` with
   `event: Investigatable/NGSIEM` and `version_constraint: ~1`.
2. **Create working variables.** "Create variable" (class `CreateVariable`) declares a schema with
   `alerted_before_detections` (array), `is_duplicate_detection` (boolean), and
   `original_detection_id` (string) to carry state through the workflow.
3. **Query for prior alerts with the same events.** The "Duplicate NG-SIEM Detections Query" action
   (`Inline.QueryEvent`, `cdf5c3e0d69f156eaaf56c1f5d3f1b66`, `version_constraint: ~1`) runs against
   `repo_or_view: search-all`. Its CQL builds a lookup of previous detections' child event IDs with
   `defineTable(query={ #repo=xdr_indicatorsrepo | report_name=?detection_name Ngsiem.alert.id!=?detection_id ... | groupBy([Ngsiem.child.event.id], function=collect([alerted_before, previous_alert_id])) }, include=[...], name="previous_detection_event_ids")`,
   then pulls the current detection's events and `match(file="previous_detection_event_ids", field=[Ngsiem.child.event.id], include=[alerted_before, previous_alert_id], strict=false)`
   to flag events already alerted on. `detection_id` and `detection_name` are passed in as query
   args from the trigger.
4. **Condition on a duplicate hit.** A CEL condition checks
   `data['DuplicateNGSIEMDetectionsQuery.results'].size() > 0 && data['DuplicateNGSIEMDetectionsQuery.results'].filter(e, e.alerted_before == true).size() > 0`.
   Only detections with a prior match continue.
5. **Collect the prior alert IDs.** "Update variable" (class `UpdateVariable`) sets
   `alerted_before_detections` by filtering the query results to `alerted_before == true` and
   splitting/deduping the `previous_alert_id` values.
6. **Loop over each duplicate, sequentially.** A `For each alerted_before_detections; Sequentially`
   loop runs a second Event Query, "Get Detection Status" (same `Inline.QueryEvent` id
   `cdf5c3e0d69f156eaaf56c1f5d3f1b66`), to check whether the detection is already closed
   (`last_status != "closed"`), then marks `is_duplicate_detection: true` and records
   `original_detection_id`.
7. **Tag, comment, and close.** On the duplicate branch: "Add tag to alert"
   (`6de8a462880ad419680ed5c291b9413f`) adds a `Duplicate Detection` tag, "Add comment to detection"
   (`7b77cb5d5ff2651cc51c7c4c610d54d1`) records the auto-close with a link to the original, and
   "Set detection status" (`beb56cc40d334583671ca91e6e390056`) sets `status: closed`.
8. **Validate, then deploy.** Run `validate.py`, then import and release to the CID.

## Key Actions

Every id and `version_constraint` below is copied directly from the source YAML.

| Action | `id` | version_constraint |
|--------|------|--------------------|
| Event Query (`Inline.QueryEvent`) | `cdf5c3e0d69f156eaaf56c1f5d3f1b66` | `~1` |
| Create variable | `702d15788dbbffdf0b68d8e2f3599aa4` | `~1` (class `CreateVariable`) |
| Update variable | `6c6eab39063fa3b72d98c82af60deb8a` | `~1` (class `UpdateVariable`) |
| Add tag to alert | `6de8a462880ad419680ed5c291b9413f` | `~0` (example omits it; correct explicit value is `~0` per the action catalog, no semantic_version) |
| Add comment to detection | `7b77cb5d5ff2651cc51c7c4c610d54d1` | `~0` |
| Set detection status | `beb56cc40d334583671ca91e6e390056` | `~0` |

The two `Inline.QueryEvent` actions carry `class: Inline.QueryEvent` and `~1`, which matches the
authoring skill's rule that this native action uses `~1`. "Set detection status" is `~0` in the
source, a useful reminder that a sophisticated-looking action can still be `~0`: the constraint is
`~<major>` of the action's `semantic_version` (here it has none, so `~0`), not a guess from the
action's name.

## When to Route Elsewhere

The dedup logic lives entirely in the Event Query's CQL and the workflow's loop, so this stays in
Falcon Fusion. Move to a Foundry function only if you need to persist a cross-workflow dedup ledger, run
transformations too complex for CQL, or pair the dedup with a UI or collection.
