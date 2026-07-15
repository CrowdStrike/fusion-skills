# Fusion Workflow Trigger Types

All trigger types available for CrowdStrike Fusion workflows.
Sourced from the API Reference PDF, web docs, and our production workflows.

---

## On demand

Manually executed via the Falcon console or the Execute API endpoint.
Accepts user-defined input parameters via JSON Schema.

**FQL trigger.type value**: `On demand`

```yaml
trigger:
    next:
        - FirstAction
    name: On demand
    parameters:
        $schema: https://json-schema.org/draft-07/schema
        properties:
            device_id:
                type: string
                title: Device ID
                description: The CrowdStrike device/host ID.
        required:
            - device_id
        type: object
    type: On demand
```

**All 30 of our production workflows use this type.** It's the most common trigger for
automation that's called programmatically or from the Falcon UI.

### Parameter schema features

- Uses JSON Schema draft-07
- Supports `string`, `integer`, `boolean`, `array`, `object` types
- Arrays use `items` (can be simple types or nested objects)
- Objects use nested `properties`
- `enum` creates a dropdown in the Falcon UI
- `minItems` enforces minimum array length
- `title` and `description` appear as labels/help text in the UI
- `format` can hint at input formatting (e.g., `yyyy-MM-dd'T'HH:mm:ssZ`)
- `default` sets a default value

---

## Event (Signal)

Fires automatically when a CrowdStrike event occurs. Different event types provide
different trigger data payloads.

**A Signal (event) trigger's `trigger.type` is `Signal`, and it MUST carry an
`event:` field** that names the event source (e.g. `event: Investigatable/NGSIEM`).
This is the field the import API resolves; omitting it fails import with
`code 2003: "unknown trigger event named "`. The same `event:` requirement applies
to Scheduled triggers (`event: Schedule`) — see the Scheduled section below. The
`event:` value is the trigger's
**category** as returned by `trigger_search.py` / the `search_triggers` API. The
canonical shape:

```yaml
trigger:
    next:
        - FirstAction
    name: Detection > NG-SIEM Detection   # descriptive event-source label
    event: Investigatable/NGSIEM          # REQUIRED — the trigger category
    type: Signal                          # always "Signal" for event triggers
    version_constraint: ~1
```

A Signal trigger is identified by `event` + `name`, **not** by a hex `id`. Do NOT
add an `id:` to a Signal trigger — exported workflows carry no trigger id, and it
is not needed for import. (This corrects earlier guidance in this file that told
authors to omit `event:` and rely on an `id:` — that was backwards and does not
import.)

### Common Signal `event:` values (verified against `search_triggers`)

| Event source (`name`) | `event:` (category) | `version_constraint` |
|-----------------------|---------------------|----------------------|
| `Detection` (EPP) | `Investigatable` | `~1` |
| `Detection > NG-SIEM Detection` | `Investigatable/NGSIEM` | `~1` |
| `Detection > Identity Detection` | `Investigatable/IDP` | `~0` |
| `Phishing email > Microsoft O365` | `PhishingEmail/MicrosoftO365` | `~1` |
| `Receive email` | `MonitoredEmail` | `~1` |
| `Zero Trust Assessment > Host assessment change` | `ZeroTrust/HostScoreChange` | `~1` |
| `Case` | `Case` | `~1` |
| `Case > Case Created` | `Case/Created` | `~1` |
| `Audit event` | `FalconAudit` | `~1` |

For an event source not in this table, discover its `event:` (category) with
`trigger_search.py` rather than guessing.

### Detection (EPP)

```yaml
trigger:
    next:
        - FirstAction
    name: Detection > EPP Detection
    event: Investigatable/EPP
    type: Signal
    version_constraint: ~1
```

EPP detection payload fields live under the **`Trigger.Detection.EPP.*`** namespace
(release-verified). Discover the full set — 90+ paths — with
`trigger_search.py --fields Investigatable/EPP`; do NOT guess. Common ones:

- `${data['Trigger.Detection.EPP.Process.SHA256']}` — offending process hash
  (the enrichment target; also `.MD5`). Parent/grandparent hashes live at
  `Trigger.Detection.EPP.ParentProcess.SHA256` and `.GrandParentProcess.SHA256`.
- `${data['Trigger.Detection.EPP.Behavior.IOCValue']}` + `.IOCType` — the IOC and its type.
- `${data['Trigger.Detection.EPP.Sensor.Hostname']}` / `.SensorID` / `.ExternalIP` / `.LocalIP`.
- `${data['Trigger.Detection.DetectionID']}`, `${data['Trigger.Detection.Name']}`,
  `${data['Trigger.Detection.SeverityDisplayName']}`.

**These are NOT under `Trigger.Category.Investigatable.*`** — that namespace is
rejected at release as "unknown variable" for EPP triggers. Enrich indicators
straight from the trigger payload (e.g. send `Process.SHA256` to VirusTotal);
you do not need an Event Query to hydrate the detection.

**Severity is an integer (1-5) at `Trigger.Detection.Severity`, not a string.** Use numeric comparison in CEL conditions:

| Value | Display Name |
|-------|-------------|
| 1 | Informational |
| 2 | Low |
| 3 | Medium |
| 4 | High |
| 5 | Critical |

```yaml
# Correct: numeric comparison on the release-verified path
cel_expression: "data['Trigger.Detection.Severity'] != null && data['Trigger.Detection.Severity'] >= 4"

# Wrong: string comparison (field is numeric, not a string)
cel_expression: "data['Trigger.Detection.Severity'] == 'Critical'"
```

#### NG-SIEM detection (`event: Investigatable/NGSIEM`)

**NG-SIEM detections use a DIFFERENT variable namespace than EPP**, even though
both are `Investigatable`-category Signal triggers. Do NOT reuse the
`Trigger.Category.Investigatable.*` paths above — release validation rejects them
as "variable not found". The **release-verified** NG-SIEM trigger fields are the
detection's own metadata, all under `Trigger.Detection.*`:

| Field | EPP path | NG-SIEM path (release-verified) |
|-------|----------|--------------------------------|
| Detection ID | `Trigger.Category.Investigatable.InvestigatableID` | `Trigger.Detection.DetectionID` |
| Severity (display) | `Trigger.Category.Investigatable.Severity` (int 1-5) | `Trigger.Detection.SeverityDisplayName` (string) |
| Name | — | `Trigger.Detection.Name` |
| Source URL | — | `Trigger.SourceEventURL` (verified in shipped `ngsiem/close-duplicate-detections.yaml`) |

**Bare `${Trigger.X.Y}` vs `${data['Trigger.X.Y']}`.** Use the **`data['...']`
form inside string interpolation** — email `subject`/`body`, Charlotte AI
`user_prompt`, comment text, any property whose value is a longer string:
`${data['Trigger.Detection.DetectionID']}`. The **bare `${Trigger.X.Y}`** form is
for **dedicated ID property fields** where the whole value is that one variable
(e.g. `investigatable_id: ${Trigger.Detection.DetectionID}`). Release rejects the
bare form used inside interpolated strings as "unknown variable". The shipped
`close-duplicate-detections.yaml` uses both: bare for `investigatable_id:`,
`data['...']`-wrapped for interpolated `detection_id:`.

**Some NG-SIEM indicators ARE on the trigger — the ones `trigger_search.py
--fields Investigatable/NGSIEM` lists. Read those from the trigger directly; use
an Event Query only for fields not in that list.** The rule is exactly the
`--fields` output: a path it lists resolves at release; a path it does not list
is rejected. Confirmed live with two release probes of identical shape (only the
field name changed):

- `Trigger.Detection.NGSIEM.SourceIPs` (in the `--fields` list) — **released.**
- `Trigger.Detection.NGSIEM.RemoteIP` (NOT in the list) — **release rejected it**:
  `property "HTTP Transaction" contains unknown variable
  "Trigger.Detection.NGSIEM.RemoteIP"`.

So do NOT guess indicator field names (the singular `RemoteIP` is a natural
guess and it fails). Run `--fields` and use exactly what it returns.

**The NG-SIEM indicator fields are multivalued (arrays).** `SourceIPs`,
`DestinationIPs`, `SourceHosts`, `DestinationHosts`, `HostNames`, `UserNames`,
`SourceProducts`, `SourceVendors` are all lists — `--fields` marks them
`(list?)`. Gate presence with `.size() > 0` (NOT `!= ''`, which release rejects
with `found no matching overload for '_!=_' applied to '(list(string), string)'`)
and index the first element with `[0]` when a scalar is needed (a URL segment, a
variable value):

```yaml
# CORRECT — plural indicator field is on the trigger and is an array
cel_expression: "data['Trigger.Detection.NGSIEM.SourceIPs'] != null && data['Trigger.Detection.NGSIEM.SourceIPs'].size() > 0"
request_url: "https://www.virustotal.com/api/v3/ip_addresses/${data['Trigger.Detection.NGSIEM.SourceIPs'][0]}"

# WRONG — comparing a list to a string; release rejects the CEL type
cel_expression: "data['Trigger.Detection.NGSIEM.SourceIPs'] != ''"

# WRONG — singular RemoteIP is not a trigger field; release rejects it
request_url: "https://www.virustotal.com/api/v3/ip_addresses/${data['Trigger.Detection.NGSIEM.RemoteIP']}"
```

> **Why the array flag isn't obvious:** the public `search_triggers` API returns
> only `name`/`display`/`type` for a trigger field — no array marker (the
> action-side `ActivityExtField` schema has a `multiple` boolean, but
> `TriggerExtField` omits it). `--fields` therefore infers "list" from the plural
> field name, shown as `(list?)`. Trust it: the plural NG-SIEM fields are arrays.

**For indicators genuinely NOT on the trigger** (anything `--fields` doesn't
list), hydrate with an Event Query and read from its `results` array:
`${data['HydrateDetection.results'][0].<Field>}`. See
`references/event-query-action.md` for that pattern. **Do NOT route query results
through an inline Python extractor** (`cs.json.decode(data['<Python>.output_stdout'])`)
— that form does not resolve at release. Read `results[0].<Field>` directly.

**Event Query hydration only works for first-party and third-party detections**
(match the composite `DetectionID` against `Ngsiem.alert.id`, not
`Ngsiem.detection.id`). **Correlation-rule detections cannot be hydrated by an
Event Query** — their `Ngsiem.detection.id` is not exposed on the trigger and no
Event Query field matches it. For those, add a **Get Detection Details** action
(or an HTTP Request to `/alerts/entities/alerts/v2` passing the composite
`DetectionID` as `composite_id`). See `references/event-query-vs-api.md`.

**Not every `Trigger.Detection.*` field resolves on the NG-SIEM trigger.**
`${Trigger.Detection.Product}` and `${Trigger.Detection.Description}` are available
on the **base `event: Investigatable`** trigger (the multi-product Detection trigger
— see `notifications/network-contain-endpoint-on-detection.yaml`, which branches on
`data['Trigger.Detection.Product'] == 'NGSIEM'`), but **release rejects them on the
dedicated `event: Investigatable/NGSIEM` trigger** with `property "…" contains
unknown variable "Trigger.Detection.Product"`. On the NG-SIEM trigger the product is
fixed (`NGSIEM`), so use a static string instead of the variable. Confirm exact
fields for your detection with a `trigger_search.py --events` lookup or a real
console export before relying on them.

### Case

Fires on case lifecycle events. **Cases are the current model for grouping and
tracking detections** — endpoint detection handling moved from incidents to cases,
so prefer a `Case` trigger over a legacy incident trigger for new workflows. The
`Case` family covers creation, status changes, and updates:

```yaml
trigger:
    next:
        - FirstAction
    name: Case > Case Created
    event: Case/Created
    type: Signal
    version_constraint: ~1
```

Other common case events (discover the full list with `trigger_search.py`):
`Case` (`Case`), `Case > Case Status Changed` (`Case/StatusChanged`),
`Case > Case Updated` (`Case/Updated`).

**Legacy incident triggers.** There is no plain first-class "Incident" detection
trigger. What remains is specialized: `CrowdScore incident` (`Incident`),
`Detection > NG-SIEM Incident` (`Investigatable/XDR`), and audit-only
`Audit event > Incident` (`FalconAudit/Incident`). Use `Case` for new work unless
you specifically need one of these.

### Zero Trust score change

```yaml
trigger:
    next:
        - FirstAction
    name: Zero Trust Assessment > Host assessment change
    event: ZeroTrust/HostScoreChange
    type: Signal
    version_constraint: ~1
```

Available field: `${Trigger.Category.ZeroTrust.EventType.HostScoreChange.OverallScore}`

### Audit event

Fires when a user performs actions in the Falcon console (incident updates,
detection changes, policy modifications).

```yaml
trigger:
    next:
        - FirstAction
    name: Audit event
    event: FalconAudit
    type: Signal
    version_constraint: ~1
```

---

## Scheduled

Runs on a cron-like schedule.

**FQL trigger.type value**: `Scheduled`

Like a Signal trigger, a Scheduled trigger MUST carry an `event:` field naming its
category, which is `Schedule`. Omitting it fails import with
`code 2003: "unknown trigger event named "`. The schedule itself goes in a
`schedule:` block whose fields are **`time_cycle`** (the cron expression) and
**`tz`** (the timezone) — NOT `cron:`/`timezone:`. Those wrong names import but
fail at release with "missing timer_event_definition or schedule parameters for
trigger". Include `start_date`/`end_date` (empty strings unless bounding the
window) and `skip_concurrent`.

```yaml
trigger:
    next:
        - FirstAction
    event: Schedule            # REQUIRED — the trigger category
    type: Scheduled
    schedule:
        time_cycle: "0 */6 * * *"   # cron expression — the field is time_cycle
        start_date: ""              # empty unless bounding the start
        end_date: ""                # empty unless bounding the end
        tz: Etc/UTC                 # timezone — the field is tz, not timezone
        skip_concurrent: true       # skip a run if the previous one is still going
```

A `schedule:` block is required only for a self-scheduled recurring workflow. An
`event: Schedule` trigger with **no** `schedule:` block is also valid — that is a
caller-scheduled job template, where the run cadence is supplied at execution time
rather than baked into the definition (several Foundry sample workflows ship this
shape).

**Warning**: Disable scheduled workflows after testing to avoid rate limiting.

---

## API invocation (uses the On demand type)

A workflow triggered exclusively via the CrowdStrike Workflow Execution API.
"API" is an **execution method**, not a distinct trigger type — the workflow
still declares `type: On demand`. It behaves like On demand but is typically not
surfaced in the Falcon console trigger dropdown.

**FQL trigger.type value**: `On demand`

```yaml
trigger:
    next:
        - FirstAction
    name: On demand
    parameters:
        $schema: https://json-schema.org/draft-07/schema
        properties:
            input_data:
                type: string
        required:
            - input_data
        type: object
    type: On demand
```

---

## Workflow execution / chaining (SubModel)

Fires when triggered by another workflow. Used to build modular, composable
automations. The trigger.type value is `SubModel`.

**FQL trigger.type value**: `SubModel`

```yaml
trigger:
    next:
        - FirstAction
    name: SubModel
    type: SubModel
```

The parent workflow calls this child using an "Execute workflow" action,
passing parameters that become available as trigger data.

---

## Execution notes

- **On demand** workflows can be executed via:
  - Falcon console → Workflow → Run
  - `POST /workflows/entities/execute/v1` with `definition_id` or `name`
  - Another workflow's "Execute workflow" action

- **Event triggers** fire automatically — no manual execution needed.
  They cannot be tested with the execute endpoint; use mock executions instead.

- **Deduplication**: The `key` parameter on the execute endpoint prevents
  duplicate executions. If omitted, every call gets a unique UUID.

- **`${Workflow.Execution.Time}`** is available in all trigger types.
