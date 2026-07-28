# Fusion execution-log queries (CQL / LogScale)

Query Fusion SOAR workflow **execution logs** with Next-Gen SIEM's advanced event
search (LogScale/CQL) for fleet-wide debugging, success-rate analysis, and
monitoring. This complements the `execution` skill's per-execution API scripts
(`get_execution_results.py`, `monitor_execution.py`): those inspect one known
execution; these queries analyze the whole population.

> **Verified** against a live `fusion` export (2,134 execution-log records) and a
> live US-2 query. Field names, log types/subtypes, `status` values, and the
> error-code saved function below are confirmed real. `Trigger.Category` *values*
> vary by tenant — treat the examples as illustrative and confirm against your own
> data.

## Where to run these

Next-Gen SIEM > Log management > **Advanced event search**.

**Via the API**, query the `search-all` repository and filter to Fusion data with
`#repo=fusion` in the query string. Passing `repository=fusion` to the search API
returns **403**; `repository=search-all` with the `#repo=fusion` filter works.

## Field reference

All Fusion execution data carries these top-level fields:

| Field | Description |
|-------|-------------|
| `cid` | Customer ID |
| `definition_id` / `definition_name` / `definition_version` | Workflow identity |
| `execution_id` | Execution ID |
| `execution_log_type` | `summary` (one per execution) or `details` (one per action) |
| `execution_log_subtype` | `start`, `end`, `loop_start`, or `loop_end` |
| `status` | `Succeeded` or `Failed` (also transient `In progress`, `Action required`) |
| `start_timestamp` / `end_timestamp` | Execution start/end (ISO 8601) |
| `parent_execution_id` / `root_execution_id` | Set on loop iterations and sub-workflows |
| `action.name` / `action.vendor` / `action.error_message` / `action.error_code` | Per-action details (`details` records) |

Trigger context is flattened under `trigger.data.Trigger.*`, e.g.
`trigger.data.Trigger.Category`, `trigger.data.Trigger.SourceEventID`,
`trigger.data.Trigger.ObservedTime`, and detection fields like
`trigger.data.Trigger.Detection.DetectionID` / `.Product`.

**`trigger.data.Trigger.Category` uses a hierarchical `Parent/Child` format**, not
flat names. Real values seen in one tenant: `Schedule`, `Investigatable/NGSIEM`
(this is how an NG-SIEM detection appears — there is no bare `Detection`),
`CloudSecurityAssessment/Configuration`, `CustomIOAMonitor/BasicProcess`,
`OnDemand`. A filter like `Category = "Detection"` matches nothing; confirm the
exact strings in your tenant with the distinct-values query below.

## Exclude loop iterations

Loops and sub-workflows create nested records. To count only top-level
executions, drop rows that carry a parent or root execution ID:

```cql
| not parent_execution_id = * AND not root_execution_id = *
```

## Sample queries

### Distinct values (use these to confirm fields in your tenant)

```cql
#repo=fusion | groupBy([execution_log_type], function=count())
#repo=fusion | groupBy([execution_log_subtype], function=count())
#repo=fusion | groupBy([status], function=count())
#repo=fusion | execution_log_type=summary | groupBy([trigger.data.Trigger.Category], function=count(), limit=max)
```

### Failed executions (top-level only)

```cql
#repo=fusion
| execution_log_type = summary AND execution_log_subtype = "end"
| status = "Failed"
| not parent_execution_id = * AND not root_execution_id = *
| select([definition_name, execution_id, start_timestamp, end_timestamp])
| sort(start_timestamp, order=desc, limit=100)
```

### Overall success rate

```cql
#repo=fusion
| execution_log_type = summary AND execution_log_subtype = "end"
| not parent_execution_id = * AND not root_execution_id = *
| succeeded := if(status == "Succeeded", then=1, else=0)
| failed := if(status == "Failed", then=1, else=0)
| [sum(succeeded, as=succeeded_count), sum(failed, as=failed_count)]
| rate := if((succeeded_count + failed_count) > 0, then=(succeeded_count * 100.0 / (succeeded_count + failed_count)), else=100.0)
| format("%.1f%%", field=rate, as="Overall success rate")
```

### Execution summary and its action details

Combine the summary record and its per-action detail records for one execution.
Both log types are matched with `OR` (a field cannot equal two values at once):

```cql
#repo=fusion
| execution_log_type = "summary" OR execution_log_type = "details"
| (execution_id = ?execID OR root_execution_id = ?execID)
| sort(start_timestamp, order=asc)
```

### Find executions that touched a value

```cql
#repo=fusion
| /<user-email>/i
| groupBy([execution_id], function=[selectLast([definition_name, definition_id, status, @timestamp])], limit=max)
| sort(@timestamp, order=desc, limit=100)
```

### Errors by code

Two options for error-code analysis:

- **Raw field** — `details` records carry `action.error_code` directly:

  ```cql
  #repo=fusion
  | execution_log_type = details
  | action.error_code >= 500
  | groupBy([action.error_code, action.vendor, action.name], function=count(as="Count"))
  | sort("Count", order=desc, limit=max)
  ```

- **Saved function** — the tenant-managed function
  `$"falcon/ngsiem:Fusion SOAR workflow error code"()` extracts the code and is
  what the built-in failure dashboards use:

  ```cql
  #repo=fusion
  | execution_log_type = details
  | $"falcon/ngsiem:Fusion SOAR workflow error code"()
  | "Error Code" < 500
  | groupBy(["Error Code", action.vendor, action.name], function=count(as="Count"))
  ```

## Pre-built dashboards

Two built-in dashboards live under Next-Gen SIEM > Log management > Dashboards:
**Fusion SOAR Execution** (trends, trigger analysis, action stats) and **Fusion
SOAR Workflow Execution Failures** (success rate, 4xx/5xx breakdown, throttling).

## Error-code reference

`4xx` codes are client-side (bad input, auth, permissions) and usually fixable in
the workflow; `5xx` are server-side and often transient. `429` is rate limiting. A
`Throttled` status is not a failure — the engine slowed the action to protect a
downstream service.
