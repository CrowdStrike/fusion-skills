# Event Query vs. a source-of-truth API

Two ways to get data in a workflow, and they answer different questions. Picking
the wrong one produces a workflow that looks right but returns incomplete or
empty results.

## The mental model

- **Event Query (`Inline.QueryEvent`) = ask the historian.** It searches
  NG-SIEM/LogScale for *what happened* — historical patterns, aggregations,
  trends, counts over time, debugging what occurred. It reads logs, not live
  object state.
- **A source-of-truth API = ask the system of record.** It answers *does X
  exist* and *what is the current state of X*, and it performs create / update /
  delete on a known object (cases, hosts, users, incidents, detections, alerts).

**Governing rule: if the object you care about has an API, call the API.** Event
Query is the escape hatch for data that has no dedicated object API (raw ingested
logs, custom parsers) or when the API you need does not exist yet.

## Which to use

| Use an Event Query when… | Use a source-of-truth API when… |
|--------------------------|----------------------------------|
| "What happened over time?" — patterns, trends | "Does X exist right now?" — existence check |
| Aggregations, counts by field, dedup across events | Get / read the current state of a known object |
| Enriching a detection you already hold (`Ngsiem.alert.id = ?detectID`) | Fetching the alert/detection *population* you don't have |
| Searching ingested logs with no object API | Create / update / delete a known object |
| Debugging why something did (or didn't) fire | Acting on whether an object exists |

## Reach for a source-of-truth API, in this order

When the question is about current state or existence, do NOT infer it from logs.
Prefer, in order:

1. **A native platform action**, if one exists — e.g. *CrowdStrike Cases → Search
   Cases* (`filter: detection_ids:'<id>'`), or the Hosts / Incidents actions.
   Discover these with `action_search.py`. No app, no code.
2. **A CrowdStrike HTTP Request** to the Falcon platform API when no native action
   covers it — e.g. `/alerts/queries/alerts/v2`, `/detects/queries/detects/v1`.
   Tenant-authenticated, standalone, no app. See `http-actions.md`.
3. **A Foundry function calling FalconPy** — same API, more ceremony, but the app
   is distributable/certifiable and prompts for credentials on install. Belongs to
   the `foundry-skills` plugin (e.g. `Alerts.query_alerts_v2`). Choose this when the
   workflow must be shared across CIDs.

## Why "infer from logs" goes wrong

A worked example: *"Has a case been created for this detection?"*

- **Log-inference (fragile):** query workflow execution logs for the "create case"
  workflow and join against detections. This answers a *different* question — "did
  this specific workflow run?" — and misses cases created manually, via the API, or
  by any other workflow. It is indirect, multi-repo, and brittle.
- **Source of truth (correct):** *CrowdStrike Cases → Search Cases* with
  `filter: detection_ids:'<detection_id>'`. Results > 0 means a case exists,
  regardless of how it was created.

## Falcon platform alerts, detections, and incidents

These are the most common trap. Their data in NG-SIEM/LogScale depends entirely on
the customer's **ingestion connectors** — a tenant with no connectors returns
audit-log events, not alerts, so an Event Query for "high-severity alerts" can
silently return zero. The split is about whether you already hold the object:

- **Enriching a detection you already hold** → depends on the detection type.
  When a workflow is triggered on a detection and has its ID, how you pull more
  fields depends on what kind of detection fired:
  - **First-party and third-party detections** → Event Query. Go schemaless
    (detection field shapes vary) and **match the detection's composite ID
    against `Ngsiem.alert.id`, NOT `Ngsiem.detection.id`.** The Signal trigger's
    `Trigger.Detection.DetectionID` is the *composite* ID (`cid:...:cid:id`), and
    in the NG-SIEM event store that value lives in `Ngsiem.alert.id`;
    `Ngsiem.detection.id` holds a different, short ID, so a query keyed on it
    silently returns **zero rows** (verified live). So:

    ```
    # RIGHT — composite DetectionID matches Ngsiem.alert.id
    Ngsiem.alert.id = ?detectID
    # WRONG — returns 0 rows for a composite DetectionID
    Ngsiem.detection.id = ?detectID
    ```

  - **Correlation-rule detections** → hydrate with the same
    `Ngsiem.alert.id = ?detectID` query. It works, but returns **multiple records**
    (the underlying events plus a correlation "meta-event" that only signals the
    rule fired), so `results[0]` is non-deterministic across runs. Drop the
    meta-event and keep the real events:

    ```
    Ngsiem.alert.id = ?detectID
    | xdr_type != correlation-rule-detection
    | report_name != *
    ```

    or project named columns with `table([field1, field2, ...])`. Restrict the
    action's output schema to only the fields you read, so runs that omit some
    fields don't fail schema validation. Event Query is how you reach the
    **event-level detail** (per-event source IP, country, and so on) that made up
    the detection. A **Get Detection Details** action returns the detection
    *object* instead — reach for it when the object's summary fields are all you
    need, or call `/alerts/entities/alerts/v2` with the composite `DetectionID` as
    `composite_id` for that same object.

  **Treat detection IDs as opaque.** Per the detections team, `composite_id` (what
  the trigger hands you) is the primary key used to retrieve a detection, but its
  tuple format may change — never split or parse it; match the whole string. If a
  hydration query returns nothing, confirm the join field by running the query in
  NG-SIEM → Advanced event search or via the NG-SIEM search API before assuming the
  detection has no data. **If the detection type carries indicators directly in the
  trigger payload (e.g. EPP: `Trigger.Detection.EPP.Process.SHA256`), prefer reading
  them straight from the payload over any hydration step — no join, no empty-result
  risk.** Discover payload fields with `trigger_search.py --fields <category>`.
- **Fetching the alert/detection *population* you don't have** — "summarize all
  high-severity alerts", "list open detections across products" → the Falcon
  platform API, NOT an Event Query (whether that population is in NG-SIEM is
  connector-dependent, so an Event Query can silently miss it). **Both options below
  call the same API; default to the first, mention the second for distribution:**
  - **Default — a CrowdStrike HTTP Request** to the API endpoint directly
    (`/alerts/queries/alerts/v2`, `/detects/queries/detects/v1`): tenant-authenticated,
    no Foundry app. Per CrowdStrike guidance an HTTP Action is the right tool for the
    vast majority of API integrations. FQL `severity_name:'High'+created_timestamp:>'now-24h'`
    (use `severity_name`, not the numeric `severity` field). Downside: the workflow
    isn't distributable — export/import only, no credentials travel with it.
  - **Foundry app + FalconPy `Alerts`/`Detects` function** (`query_alerts_v2`) — the
    same API via the FalconPy SDK; route to `foundry-skills`. More setup, but
    distributable/certifiable to other CIDs and prompts for credentials on install.
    Mention/suggest this only when the workflow needs to be shared or published.
- **Historical / aggregate analysis of alert telemetry that genuinely lives in
  NG-SIEM** (patterns over time, counts by vendor) → an Event Query is
  appropriate, subject to the same connector caveat.
