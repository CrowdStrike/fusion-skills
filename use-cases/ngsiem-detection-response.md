---
name: ngsiem-detection-response
description: Automatically respond to a Next-Gen SIEM detection in a Falcon Fusion workflow - filter by source, hydrate the detection with an event query, extract fields, gate on a condition, then summarize with Charlotte AI and email the team
source: CrowdStrike "Fusion SOAR Automated Response Quick Start Guide" (https://docs.crowdstrike.com/access?ft:originId=z8tzddxk)
skills: [authoring, deployment, execution]
capabilities: [workflow, ngsiem-detection, event-query, charlotte-ai, notification]
---

## When to Use

User wants a workflow that fires on a Next-Gen SIEM detection, pulls the full
detection context, applies conditional logic (for example, "only act when file
activity is outside business hours"), then uses Charlotte AI to summarize the
event and emails the summary to a team. This is the end-to-end
detect-and-respond pipeline: trigger -> filter -> hydrate -> extract -> gate ->
summarize -> notify.

This is grounded in the published CrowdStrike *Fusion SOAR Automated Response
Quick Start Guide*, which walks through exactly this pipeline for after-hours
file-activity detections. The mechanics below (hydration join key, Charlotte AI
decode path, HTML-email formatting) are cross-referenced to the reference docs
where each is covered in depth and verified live.

## Pattern

1. **Trigger on the NG-SIEM Detection.** Use a Signal trigger on the `Detection`
   category, `event: Investigatable/NGSIEM`. Mock the trigger with Custom JSON so
   you can iterate without waiting for a real detection. See
   `references/trigger-types.md`.
2. **Filter with a condition (fail closed).** Add a condition that continues only
   for the detections you care about (the guide checks `Vendors includes
   Anthropic`); the ELSE branch ends the workflow (or prints a "no action"
   message). A condition MUST have both a TRUE path and an ELSE/default, or
   release validation rejects it - see `references/yaml-schema.md`.
3. **Hydrate with an event query.** The Signal trigger carries common fields but
   not the full detection. Query Next-Gen SIEM to get the rest. **Match the
   trigger's composite `DetectionID` against `Ngsiem.alert.id`, NOT
   `Ngsiem.detection.id`** (a query keyed on `detection.id` silently returns zero
   rows). When the detection is built on a third-party data connector, query that
   connector's underlying events (e.g. `Ngsiem.alert.id = ?detectID` plus the
   vendor event/activity filters) rather than the detection record. Which
   detection types can be hydrated by Event Query versus need a Get Detection
   Details action is covered in `references/event-query-vs-api.md` - read it
   before authoring the query.
4. **Extract variables from the results.** Create variables (with explicit types)
   and populate them from the query results with CEL - e.g. parse a timestamp with
   `.substring(...)`, or filter-and-map an array of IPs with
   `.filter(...).map(...).distinct()`. Defensive CEL (null/size guards before
   indexing) is covered in `use-cases/event-queries.md` and
   `references/cel-expressions.md`.
5. **Gate on the extracted values.** Add a second condition on the extracted
   variables (the guide checks whether the event hour falls outside business
   hours). ELSE prints a "no unusual activity" message and ends.
6. **Summarize with Charlotte AI.** Add a Charlotte AI - LLM Completion action.
   Pass the query results as input, instruct the model to return raw JSON (no
   markdown, no code fences) and an HTML email body, and read decoded fields
   downstream with
   `cs.json.decode(data['<Node>.FaaS.nlpassistantapi.llminvocator_handler.completion']).<field>`.
   The decode path, the "no code fence" instruction, and HTML-email formatting are
   covered in `references/charlotte-ai-action.md` and
   `use-cases/http-actions.md` (LLM formatting section).
7. **Notify.** Send the decoded `subject` and `html_email` via a Send email action
   with `msg_type: html`. Prompt the user for the recipient - never hardcode one.
8. **Validate, then deploy.** Run `validate.py`, import, test with mock data, and
   release. See the deployment skill.

## Key Actions

| Action | Role | Notes |
|--------|------|-------|
| NG-SIEM Detection (Signal trigger) | Starts the workflow on a detection | `event: Investigatable/NGSIEM`; mock via Custom JSON |
| Event Query (`Inline.QueryEvent`) | Hydrates the detection context | Join on `Ngsiem.alert.id`; go schemaless |
| Create variable / Update variable | Extract and transform fields with CEL | Guard nulls/size before indexing |
| Charlotte AI - LLM Completion | Summarizes the event | Compound ID, `~0`; needs Charlotte AI credits; decode the completion |
| Send email | Delivers the summary | `msg_type: html`; ask the user for the recipient |

## Gotchas

- **Hydration join key is `Ngsiem.alert.id`, not `Ngsiem.detection.id`.** Match the
  composite `DetectionID` against `Ngsiem.alert.id` for all detection types. A
  correlation-rule detection hydrates the same way, but the query returns multiple
  records (the underlying events plus a correlation "meta-event" that only signals
  the rule fired), so drop the meta-event and keep the real events
  (`| xdr_type != correlation-rule-detection | report_name != *`) — an unfiltered
  `results[0]` is the most common silent-empty/wrong-row failure. Event Query is how
  you reach the event-level detail; a Get Detection Details action returns the
  detection object instead. See `references/event-query-vs-api.md`.
- **Charlotte AI needs credits**, and the org must opt in. A workflow that invokes
  it will not produce a summary in a tenant without them.
- **Business-hours logic:** the guide implements the time gate as hardcoded
  UTC-hour OR-branches (`EventHourPT == "07"` ... `"13"` for a PT location). That
  works but is brittle across time zones; prefer a numeric comparison on the
  extracted hour where practical.
- **Email recipient:** prompt for it. Send email only delivers to Falcon users and
  approved domains, so a placeholder like `user@example.com` fails at runtime.

## When to Route Elsewhere

Fetching a *population* of detections the workflow does not already hold ("all
high-severity detections today") is a Falcon platform API call (CrowdStrike HTTP
Request), not an Event Query - see `use-cases/http-actions.md` and
`references/event-query-vs-api.md`. Keep this use case for responding to a single
detection that fired the trigger.
