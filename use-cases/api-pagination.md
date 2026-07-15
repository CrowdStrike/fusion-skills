---
name: api-pagination
description: Paginate through large or unknown-size external API results from a Falcon Fusion workflow using a loop with a pagination token
source: https://www.crowdstrike.com/tech-hub/ng-siem/api-pagination-strategies-for-falcon-foundry-functions-and-workflows/
skills: [authoring, deployment]
capabilities: [workflow, http-action]
---

## When to Use

A workflow must fetch paginated data from an external API (threat intel feeds, CMDB,
ITSM) where the result set is large or of unknown size. Workflow-based pagination uses a loop
that carries a pagination token between iterations, so the workflow keeps fetching pages until
the API signals there are no more. Use this over a one-shot HTTP Action whenever the data could
exceed a single response or the run could take a long time (the workflow execution window is
far longer than any single action).

## Pattern

1. **Initialize a workflow variable** for the pagination token with `CreateVariable` (start it
   empty/null).
2. **Make the first call** — an `Inline.HTTPRequest` (or an existing platform function action)
   that fetches one page and returns the `next` token.
3. **Update the variable** with the returned `next` token via `UpdateVariable`.
4. **Loop** while the token is present. The loop body calls the fetch action with
   `${WorkflowCustomVariable.next}`, then updates the variable again.
5. **Stop correctly.** The loop `condition` must check BOTH null and `"0"` —
   `next:!null+next:!'0'` — because the engine maps an omitted `next` field to the string `"0"`,
   not null. Checking only null loops forever.
6. **Validate, then deploy.** Run `validate.py`, then import and release to the CID.

## Key Code

**Workflow YAML pagination loop:**
```yaml
loops:
  Loop:
    for:
      condition: WorkflowCustomVariable.next:!null+WorkflowCustomVariable.next:!'0'
      max_iteration_count: 500
      max_execution_seconds: 7200
      sequential: true
    trigger:
      next: [FetchPage]
    actions:
      FetchPage:
        next: [UpdateNext]
        # An Inline.HTTPRequest action, or a platform function action discovered
        # via action_search.py — never a placeholder ID.
        properties:
          limit: 1000
          next: ${data['WorkflowCustomVariable.next']}
      UpdateNext:
        class: UpdateVariable
        properties:
          WorkflowCustomVariable:
            next: ${data['FetchPage...next']}
```

## Key Actions

| Action | Type | Purpose |
|--------|------|---------|
| Create variable | `CreateVariable` | Initializes the pagination token |
| Fetch page | `Inline.HTTPRequest` | Fetches one page, returns the `next` token. `version_constraint: ~1` |
| Update variable | `UpdateVariable` | Stores the returned token for the next iteration |
| Loop | Iterator | Repeats the fetch while the token is present |

## Gotchas

- **The "0" gotcha:** when the API/action omits `next`, the engine maps it to the string `"0"`,
  not null. The loop condition MUST check both: `next:!null+next:!'0'`. Otherwise it never ends.
- **Workflow limits:** up to 100,000 iterations and a 7-day execution window — generous, but
  bound the loop with `max_iteration_count` and `max_execution_seconds` anyway.
- **Identify the pagination pattern first** (offset/limit, cursor, page-number, Link header,
  search-after, timestamp) before authoring — the token field and stop condition differ per API.

## When to Route Elsewhere

This is the **workflow** path. If pagination needs custom code (complex state,
result transformation, retry/backoff logic) or the dataset is small enough to drain inside a
single 15-minute function run, build a **Foundry function** instead — route to foundry-skills
(`crowdstrike-falcon-foundry`). A workflow loop is right when the work is large,
long-running, or must survive across many pages without custom code.
