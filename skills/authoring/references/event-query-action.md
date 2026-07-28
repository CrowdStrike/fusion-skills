# Event Query Action (`Inline.QueryEvent`)

> **⚠️ FIRST, CHECK: are you querying a _population_ of Falcon alerts, detections,
> or incidents the workflow does NOT already hold?** ("summarize all high-severity
> alerts", "list open detections", "fetch alerts from the last 24h".) If so, **do
> NOT use this action.** An Event Query runs against NG-SIEM/LogScale repos, whose
> alert/detection contents depend on the customer's ingestion connectors — a tenant
> with no connectors returns audit-log events, not alerts, so the query silently
> returns nothing. **Default to a CrowdStrike HTTP Request** to
> `/alerts/queries/alerts/v2` (tenant-authenticated, no app; FQL
> `severity_name:'High'+created_timestamp:>'now-24h'`). Only reach for a Foundry-app
> FalconPy `Alerts`/`Detects` function (route to `foundry-skills`) when the workflow
> must be distributed/certified. See `event-query-vs-api.md`. Event Query is correct
> ONLY for data that genuinely lives in NG-SIEM (ingested logs, custom parsers) or
> for **enriching a detection the workflow already holds** (it was triggered on the
> detection and has its ID, e.g. `Ngsiem.alert.id = ?detectID` — the trigger's
> composite `DetectionID` is stored as `Ngsiem.alert.id`, NOT `Ngsiem.detection.id`;
> see `event-query-vs-api.md`).

Fusion's native event-query action runs a CQL/FQL query against the event store
directly inside a workflow step (vendor: CrowdStrike, namespace `faas`, like the
other inline actions). Use it for simple, inline lookups against data that
genuinely lives in NG-SIEM/LogScale — ingested third-party logs, custom parsers,
LogScale search results, or enriching a detection the workflow already holds (e.g.
`Ngsiem.alert.id = ?detectID` — match the composite `DetectionID` against
`Ngsiem.alert.id`, not `Ngsiem.detection.id`) — including schemaless queries where no
predefined schema exists. (For an alert/detection _population_, see the warning
above — use a CrowdStrike HTTP Request, not this action.)
> triggered on a detection and has its ID, an Event Query is the right tool. See
> [event-query-vs-api.md](event-query-vs-api.md).

```yaml
query_ngsiem_logs:
    id: <inline-queryevent-action-id>   # discover via action_search.py
    class: Inline.QueryEvent
    version_constraint: ~1
    properties:
        # Top-level properties (required at release): the query args bound to
        # trigger data, plus the search window and export flags.
        detection_id: ${data['Trigger.Detection.DetectionID']}
        logscale_search_start_time: 1 day
        output_files_only: false
        workflow_csv_header_fields: []
        workflow_export_event_query_results_to_csv: false
    inline_configuration:
        config:
            description: ''
            end: now                     # NOT "time_range"
            repo_or_view: search-all      # NOT "repo"
            search_name: Hydrate detection
            search_query: "#repo=xdr_indicatorsrepo Ngsiem.alert.id=?detection_id"
            search_query_args:
                detection_id: '*'         # placeholder default; real value bound above
            start: 24h                    # NOT "time_range"
            tags: []
        input_schema:
            $schema: https://json-schema.org/draft-07/schema
            type: object
            description: Generated request schema
            required:
                - detection_id
            properties:
                detection_id:
                    type: string
                    title: Detection ID
                    default: '*'
    next:
        - handle_results
```

- **Config field names matter at release.** Use `search_query` (not `query`),
  `repo_or_view` (not `repo`), and `start`/`end` (not `time_range`), plus the
  top-level `logscale_search_start_time`. The minimal `query`/`time_range`/`repo`
  shape passes structural + API validation but release rejects it with "Missing
  repo or view; Missing search start time; Missing search end time". Match the
  full shape from the shipped `ngsiem/close-duplicate-detections.yaml` export.
  **`start` and `end` both live INSIDE `inline_configuration.config` and are both
  required** — the top-level `logscale_search_start_time` does NOT substitute for
  them. Omitting `start:` (even with `logscale_search_start_time` set) fails
  release with "Missing search start time". `validate.py` now flags a config that
  has `search_query`/`repo_or_view` but is missing `start` or `end`.
- **Outputs:** results are an **array** at `${data['<ActionLabel>.results']}` —
  NOT `.events.0.`, which release validation rejects. Access an element with
  `${data['<ActionLabel>.results'][0].FieldName}`, count with
  `${data['<ActionLabel>.results'].size()}`, or filter with CEL list ops (the
  shipped `close-duplicate-detections.yaml` uses
  `data['...results'].filter(e, e.alerted_before == true)[0].previous_alert_id`).
- **Read results directly — do NOT route them through an inline Python
  extractor.** It is tempting to add an `Inline.Python` step that JSON-parses the
  query results and re-emits indicators, then read them back with
  `${cs.json.decode(data['<Python>.output_stdout']).field}`. **That form does not
  resolve at release** ("invalid or missing variable definitions") and the Python
  hop is unnecessary. Reference the query fields straight from
  `${data['<ActionLabel>.results'][0].FieldName}` — this is the pattern that
  releases cleanly (confirmed live: a workflow reading `results[0]` directly
  released; the same workflow rebuilt with a Python extractor failed release
  four times).
- **`version_constraint: ~1`.**

## Binding a trigger value into a query argument (two parts, both required)

When the query filters on a runtime value — the detection's IP, the detection ID,
a user — the value flows through a **named query argument** (`?arg` in the query),
and wiring it takes **two** coordinated pieces. Real console exports always have
both; a workflow that has only the first silently scans everything.

1. Declare the arg in `search_query_args` with a **placeholder default of `'*'`**
   (this is the console default, not the runtime value).
2. Bind the real trigger value in a **top-level `properties.<arg>`** field with a
   `${data['...']}` reference. This is what actually substitutes at runtime.

```yaml
IPBlocklistQuery:
    id: <inline-queryevent-action-id>   # discover via action_search.py
    class: Inline.QueryEvent
    version_constraint: ~1
    properties:
        remote_ip: ${data['Trigger.Detection.NGSIEM.RemoteIP']}   # (2) BINDS the value
    inline_configuration:
        config:
            search_query: "#event_simpleName=* RemoteIP=?remote_ip | match(file=\"ip-blocklist.csv\", column=ip, field=RemoteIP, strict=true)"
            search_query_args:
                remote_ip: '*'                                    # (1) default only
```

**Omitting part (2) is a real bug that passes validation.** `?remote_ip` falls
back to its `'*'` default, so the query matches every event (`RemoteIP=*`) instead
of the detection's IP — a full scan, and the report can disagree with what was
actually queried. The YAML is syntactically valid, so `validate.py` and the import
API both accept it; only runtime behavior is wrong. Every `?arg` referenced in the
query needs a matching `properties.<arg>: ${data['Trigger...']}` binding.


**Event Query vs. a Foundry function with FQL:** use an Event Query action for
simple inline queries inside a workflow step (no app, no code). Reach for a
Foundry function when you need complex processing, pagination, or transformation
logic — that path belongs to the `foundry-skills` plugin.

See [use-cases/event-queries.md](../../use-cases/event-queries.md) for the full
pattern.
