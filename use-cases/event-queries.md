---
name: event-queries
description: Use Falcon Fusion Event Query actions for schemaless queries against the event store without predefined schemas
source: https://www.crowdstrike.com/tech-hub/ng-siem/falcon-fusion-soar-event-queries-when-and-how-to-go-schemaless/
skills: [authoring, execution]
capabilities: [workflow, event-query]
---

## When to Use

User wants a workflow to query the NG-SIEM event store — ingested logs, custom telemetry,
LogScale search results, or enriching a detection the workflow already holds (e.g.
`Ngsiem.alert.id = ?detectID` — for first-party/third-party detections the trigger's composite
`DetectionID` is stored as `Ngsiem.alert.id`, not `Ngsiem.detection.id`; correlation-rule
detections can be hydrated the same way, but the query returns multiple records — filter it, see
below) — without defining a
schema up front. The Event Query action
runs a CQL/FQL query inline and
returns matching events, which subsequent actions process and branch on. This is the
"schemaless" path: you query whatever the event store holds and shape the results downstream,
rather than pinning a fixed schema before you know what is there.

## Pattern

1. **Choose a trigger.** A Scheduled trigger for periodic sweeps, or On demand for ad-hoc and
   API-driven runs.
2. **Author the Event Query.** Add an `Inline.QueryEvent` action with the query string. Go
   schemaless when the shape of the data is unknown, exploratory, or varies between runs.
3. **Process the results.** Event Query output is an **array** at `results` —
   reference an element as `${data['<QueryName>.results'][0].FieldName}` (NOT
   `.events.N`, which release validation rejects). Loop over multiple results
   when the query returns a list.
4. **Branch on query output.** Add a CEL condition on a returned field (a count, a severity, a
   matched value) to route the workflow — e.g. notify only when the query returns results.
5. **Validate, then deploy.** Run `validate.py`, then import and release to the CID.

## Key Actions

| Action | Type | Purpose |
|--------|------|---------|
| Query event | `Inline.QueryEvent` | Runs a schemaless CQL/FQL query against the event store. `version_constraint: ~1` |
| Condition | CEL gateway | Branches on a returned field (count, value, severity) |
| Loop | Iterator | Processes each event when the query returns multiple results |

**Query syntax:** the Event Query runs CQL/FQL against the event store. Keep queries narrow
(time-bounded, field-selective) so the workflow stays fast and within execution limits.

## Turning Schema Validation Off

By default the Event Query action **generates a JSON schema from your first test result and
validates every future run against it** — the console checkbox is *"Automatically generate schema
and enforce schema validation."* That is fine for predictable queries, but detection-enrichment
queries return different fields for different detection types: one run has fields another lacks, and
the workflow fails with `Schema validation failed: unexpected field ...` (or silently drops fields
not in the original schema).

**Uncheck that box to go schemaless** when the response shape varies — dynamic aggregations
(grouping by a runtime field), conditional fields that depend on event type, or detection
enrichment where each detection type carries a different structure. Schemaless trades the safety
guardrail for flexibility: no schema is generated, and the query accepts whatever it returns. Keep
validation **on** for predictable queries — it catches type errors early.

With no schema, only the top-level `results` array is recognized; you reference into it with plain
CEL (`results[0].Field`). When the shape is too variable to reference field-by-field, feed the
**whole result blob** to a Charlotte AI action with `${data['<QueryName>.raw_results']}` — the LLM
extracts what it needs regardless of which fields are present.

## Handling Variable Results (defensive CEL)

Schemaless means *you* own the null- and shape-handling in workflow logic. Four patterns keep a
variable response from crashing the workflow:

1. **Check array length before indexing.** Never assume the query returned a row. Gate on
   `size(data['<QueryName>.results']) > 0` before touching `[0]`.
2. **Null-check a field before using it**, with a default:
   `${size(data['<QueryName>.results']) > 0 && data['<QueryName>.results'][0].Field != null ? data['<QueryName>.results'][0].Field : "N/A"}`
3. **CEL has no `??` null-coalescing operator.** Use a ternary — `condition ? valueIfTrue :
   valueIfFalse` — not `a ?? b`.
4. **Validate with CEL extensions** when the data may be malformed: `cs.json.valid()` /
   `cs.json.decode()` for JSON strings, `cs.ip.valid()` for IPs.

**Hybrid pattern (recommended for typed downstream actions):** run the query schemaless, then
**normalize** into a fixed shape in a `CreateVariable` step (each field a null-checked ternary),
and pass that stable variable to downstream actions that expect specific fields. Flexibility at the
source, type safety where it matters.

> **Array-typed trigger fields are a different thing.** The `.size() > 0` rule above is about the
> **results array length**. Separately, NG-SIEM *trigger* fields like `SourceIPs`/`UserNames` are
> themselves `list(string)`; gate those with `.size() > 0` too (not `!= ''`, a release-time CEL
> type error) and index with `[0]`. See
> [trigger-types.md](../skills/authoring/references/trigger-types.md).

## When to Route Elsewhere

Use an **Event Query action** for query logic that lives inside a single workflow and feeds the
next steps directly. Build a **Foundry function with FQL** (route to foundry-skills) when the
query logic is complex, reused across workflows, needs custom result transformation in code, or
is paired with a UI or collection. Schemaless-in-workflow stays here; schema-backed,
code-driven querying belongs in a function.

> **⚠️ Querying the alert/detection *population* you don't already hold?** For "summarize all
> high-severity alerts", "list open detections across products", or similar fleet-wide questions,
> do NOT use an Event Query. It runs against NG-SIEM/LogScale repos, whose alert/detection contents
> depend on the customer's ingestion connectors, so it can silently return nothing. Hit the Falcon
> platform API instead. **Default:** a **CrowdStrike HTTP Request** to the API
> (`/alerts/queries/alerts/v2`) — tenant-auth, no app, the right tool for most API integrations.
> **Mention** the alternative when the workflow must be shared: a **Foundry app + FalconPy
> `Alerts`/`Detects` function** (route to **foundry-skills**) — distributable/certifiable, prompts
> for creds on install. This does NOT apply to *enriching a detection
> you already hold* — when the workflow was triggered on a **first-party or third-party** detection
> and has its ID, an Event Query like `Ngsiem.alert.id = ?detectID` to pull more fields is the right
> tool (match the composite `DetectionID` against `Ngsiem.alert.id`, not `Ngsiem.detection.id`).
> **Correlation-rule detections can also be hydrated** with `Ngsiem.alert.id = ?detectID`, but the
> query returns multiple records (the underlying events plus a correlation "meta-event" that only
> signals the rule fired), so `results[0]` is non-deterministic without a filter. Drop the meta-event
> and keep the real events with `| xdr_type != correlation-rule-detection | report_name != *`, or
> project named columns with `table([...])`. Event Query is how you reach the event-level detail that
> composed the detection; a **Get Detection Details** action returns the detection object instead —
> use it when the object's summary fields are all you need. See
> [event-query-vs-api.md](../skills/authoring/references/event-query-vs-api.md).
