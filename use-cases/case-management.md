---
name: case-management
description: Automate Next-Gen SIEM case work in a Falcon Fusion workflow by running an Event Query for the relevant events and attaching them to a Case, with an optional Charlotte AI summary written back into the case
source: https://www.reddit.com/r/crowdstrike/comments/1ugd5ec/20260626_workflow_wednesday_using_case_templates/
example: skills/authoring/examples/tutorials/intro-cases-add-event.yaml
skills: [authoring, deployment, execution]
capabilities: [workflow, case-management, event-query]
---

## When to Use

User wants a workflow that populates or curates a Next-Gen SIEM Case — pulling the events that
matter into the case record so an analyst has the full picture in one place. The grounding
example queries the event store for recent workflow events and attaches the matching event to a
case by ID. Use this whenever a workflow should enrich a case with evidence (triggering events,
related telemetry) rather than leaving the analyst to hunt for it.

This is grounded in the real Content Library playbook
`skills/authoring/examples/tutorials/intro-cases-add-event.yaml` — read it for the exact
structure: an On demand trigger → Event Query → "Add events to case".

## Pattern

1. **Choose a trigger.** The example uses an **On demand** trigger so the workflow can be run
   manually or called by another playbook with the case context. A real-world extension (the
   Workflow Wednesday "case templates" post) instead fires on a **Case Template Assigned**
   trigger so the automation runs the moment a case is created from a template.
2. **Query for the events.** Add an `Inline.QueryEvent` action (`class: Inline.QueryEvent`,
   `version_constraint: ~1`) with your CQL/LogScale query. The example queries `#repo = fusion`,
   selects the workflow definition name and `@id`, and renames `@id` to `eventID` so the next
   action can reference it. Keep `output_files_only: false` so the JSON result fields stay
   populated for downstream use.
3. **Add the events to the case.** Wire the query into the **Add events to case** action
   (`version_constraint: ~1`). It takes a `case_id` and an `events` list; the example passes
   `${data['ExampleEventQuery.results'][0].eventID}` — the event ID pulled from the query result.
   Supply the target `case_id` from the trigger context or a parameter; do not hardcode the long
   example value.
4. **(Optional) Summarize into the case.** As a real-world extension, the "case templates"
   Workflow Wednesday post adds a **Charlotte AI LLM Completion** action to summarize the
   attached events and write the summary back as a case comment. That action is not part of the
   grounding example, so discover its `id` with `action_search.py --search "Charlotte"` before
   using it — do not assume an ID here.
5. **Validate, then deploy.** Run `validate.py`, then import and release to the CID.

## Key Actions

The `id` and `version_constraint` values below are taken directly from the source example YAML.

| Action | `id` | version_constraint |
|--------|------|--------------------|
| example event query (`Inline.QueryEvent`) | `cdf5c3e0d69f156eaaf56c1f5d3f1b66` | `~1` |
| Add events to case | `91e6224248076bdea0e79b51f8b8b13a` | `~1` |

The Charlotte AI LLM Completion action referenced in step 4 is **not** in the example YAML —
discover its ID with `action_search.py` (Charlotte AI ships at a low semantic version, so expect
`version_constraint: ~0`, but verify). Never invent an ID.

## When to Route Elsewhere

Keep case population in the workflow when the query feeds the case attachment directly. Build a
Foundry function (route to foundry-skills) when case enrichment needs custom result
transformation in code, pagination across large result sets, or a UI/collection alongside it.
