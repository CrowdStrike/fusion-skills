# Fusion Workflow YAML Schema Reference

Complete field reference for CrowdStrike Fusion workflow YAML files.
Sourced from the CrowdStrike API Reference PDF, web docs, and our 30 production workflows.

---

## Top-Level Structure

```yaml
name: '<string>'                    # Unique per CID. Use quotes if it contains brackets.
description: <string>               # Human-readable description
trigger: { ... }                    # Required. How the workflow starts.
actions: { ... }                    # Action nodes (outside loops)
conditions: { ... }                 # Exclusive-gateway branch nodes
loops: { ... }                      # Loop definitions
output_fields: []                   # Fields surfaced to the caller
```

> **These are the ONLY top-level keys.** Every step is a node nested under
> `actions:` (or `conditions:`/`loops:`) — never a bare label at the root, and
> never an invented shape like `nodes:`/`edges:` or `steps:`/`outputs:`. A
> workflow with off-schema top-level keys imports as empty and fails at release;
> `validate.py` rejects any key outside the list above.

**Header comment**: Every exported workflow starts with `# This is an exported workflow...`.
Include a `#` comment line when authoring — some tooling expects it.

---

## trigger

```yaml
trigger:
    next:                           # First node(s) to execute
        - ActionLabel               # Reference to an action, loop, or condition
    name: On demand                 # Display name
    type: On demand                 # Trigger type (see trigger-types.md)
    parameters:                     # Input schema (On demand / API triggers)
        $schema: https://json-schema.org/draft-07/schema
        properties:
            my_param:
                type: string        # string | integer | boolean | array | object
                title: My Param     # Display label in Falcon UI
                description: ...    # Help text
        required:
            - my_param
        type: object
```

**Parameter types**: `string`, `integer`, `boolean`, `array` (with `items`), `object` (with nested `properties`).
Arrays can have `minItems`. Strings can have `enum` for dropdown values.

> **The trigger MUST have a `next:` edge, and every node must be reachable from it.**
> A trigger with a `type` and `event` but no `next:` severs the whole graph — the
> workflow imports fine, then fails at *release* with "disjoint node" / "At least
> one action or valid loop should be defined after the trigger". Likewise, any
> action, condition, or loop that nothing points at is a disjoint node. `validate.py`
> walks the graph from `trigger.next` and flags unreachable nodes before you present
> the YAML, so wire the trigger to its first action and connect every node via a
> `next:`/`else:` edge. **`next:` must always be a LIST** (a `- Target` item beneath
> it), never a scalar `next: Target` — the import API rejects a scalar with "import
> file must be a valid YAML file".

---

## actions

Each action is a named node with a unique label (PascalCase recommended).

```yaml
actions:
    ContainDevice:                  # Node label — referenced by next/conditions
        id: bec9fbeb...            # 32-char hex from the action catalog (global, not per-CID)
        name: Contain device        # Display label — defaults to the catalog name, but you can rename it freely (next:/conditions resolve by node key and id, not this label)
        next:                       # Next node(s) to execute
            - UpdateVariable
        properties:                 # Action-specific inputs
            device_id: ${data['device_id']}
            note: ${data['note']}
```

### Class-based actions (CreateVariable, UpdateVariable)

These require `class` and `version_constraint`:

```yaml
CreateVariable:
    id: 702d15788dbbffdf0b68d8e2f3599aa4    # Fixed ID for CreateVariable
    class: CreateVariable
    name: Create variable
    next:
        - NextAction
    properties:
        variable_schema:
            properties:
                my_field:
                    type: string
                my_flag:
                    type: boolean
            required:
                - my_field
            type: object
    version_constraint: ~1          # Nearly every action needs a version_constraint, not just class-based ones
```

```yaml
UpdateVariable:
    id: 6c6eab39063fa3b72d98c82af60deb8a    # Fixed ID for UpdateVariable
    class: UpdateVariable
    name: Update variable
    properties:
        WorkflowCustomVariable:     # Always this key
            my_field: ${data['some.path']}
            my_flag: true
    version_constraint: ~1
```

**Every `WorkflowCustomVariable.<name>` you reference must be declared first** — by a
`CreateVariable` action's `variable_schema.properties` (its keys are the names) or set by an
`UpdateVariable` block. An undeclared name imports and validates fine, then fails at *release* with
`property "..." contains unknown variable "WorkflowCustomVariable.<name>"`. To feed a hydrated
indicator into an enrichment call, reference the producing action's output directly
(`${data['HydrateDetection.results'][0].URL}`) rather than inventing a custom variable you never create.

**Well-known fixed IDs**:
- CreateVariable: `702d15788dbbffdf0b68d8e2f3599aa4`
- UpdateVariable: `6c6eab39063fa3b72d98c82af60deb8a`
- Print data: `aadbf530e35fc452a032f5f8acaaac2a`

### Third-party / plugin actions

Actions from CrowdStrike Store plugins (Okta, Entra ID, Mimecast, etc.) use `config_id` and `params`:

```yaml
OktaRevokeSessions:
    id: 5092e629ba5f421abc057b72ea123c59
    name: Okta - Revoke Sessions
    properties:
        config_id: bb72f1a93d89473b8c0bd1a3317fb1a9  # Plugin instance ID (per-CID)
        params:
            path:
                Okta User ID: ${data['GetUserIdentityContext.UserOktaObjectID']}
            query:
                oauthTokens: true
```

**Warning**: `config_id` values are CID-specific. Workflows using them cannot be imported into a different CID without updating these IDs.

---

## loops

```yaml
loops:
    Loop:
        display: For each Device IDs; Sequentially
        name: For each Device IDs; Sequentially
        for:
            input: device_ids       # Parameter name containing the array
            continue_on_partial_execution: false
            sequential: true        # true = one at a time; false = parallel
        trigger:
            next:
                - CreateVariable    # First node inside the loop
        actions:
            CreateVariable: { ... }
            SomeAction: { ... }
            UpdateVariable: { ... }
        conditions:                 # Optional conditions inside the loop
            my_condition: { ... }
        output_fields:              # Fields collected per iteration
            - WorkflowCustomVariable.field_name
```

**Loop limits**: 100,000 iterations max. 7-day max execution window.

### Nested loops

Loops can contain sub-loops under their `actions` key using a `loops` sub-key:

```yaml
actions:
    SomeAction:
        ...
        loops:
            InnerLoop:
                for:
                    input: SomeAction.results
                ...
```

---

## conditions

Two expression syntaxes are available. Prefer `cel_expression` (CEL) for new workflows; `expression` (FQL-style) is legacy.

### CEL expressions (`cel_expression`)

For data comparisons, type checks, string matching:

```yaml
conditions:
    is_ip:
        next:
            - ProcessIP
        cel_expression: data['iocs.#.ioc_type'] == 'ip'
        display:
            - IOC type is IP
```

### FQL-style expressions (`expression`, legacy)

For membership/inclusion checks (e.g., group membership):

```yaml
conditions:
    not_in_skip_group:
        next:
            - ProcessUser
        expression: GetUserIdentityContext.Groups:!['SkipCrowdStrikeWorkflows']
        display:
            - User groups does not include SkipCrowdStrikeWorkflows
        else:
            - SkipAction
```

### `else` and `else_if` branches

Both `cel_expression` and `expression` support `else`. CEL also supports `else_if`, which chains to another condition node to build if / else-if / else:

```yaml
conditions:
    is_bar:                          # IF
        cel_expression: data['foo'] == "bar"
        next:
            - PrintBar
        else_if: is_tea
    is_tea:                          # ELSE IF
        cel_expression: data['foo'] == "tea"
        next:
            - PrintTea
        else:                        # ELSE (default fallthrough)
            - PrintDefault
```

In the workflow JSON this is an exclusive gateway whose `else` branch is the gateway's `default` flow. The YAML is a conversion of that JSON; the backend processes the JSON.

### Parallel fan-out (run branches concurrently)

To run several independent branches at the same time — for example, enriching a
domain, an IP, and a file hash simultaneously rather than one after another — a
node's `next:` lists **the branch targets directly**. Each target is a real
downstream node (an action, or a gate condition that guards one branch). Listing
more than one target is what makes the console draw side-by-side parallel
branches.

```yaml
actions:
    QueryDetection:
        id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
        name: Query event - Hydrate detection
        version_constraint: ~1
        next:                                   # fan out to N branches directly
            - EnrichDomain
            - EnrichIP
            - EnrichHash
        properties:
            query: ...
    EnrichDomain: { id: ..., next: [SummarizeEnrichment], properties: {...} }
    EnrichIP:     { id: ..., next: [SummarizeEnrichment], properties: {...} }
    EnrichHash:   { id: ..., next: [SummarizeEnrichment], properties: {...} }
```

Do **not** invent pass-through condition nodes (`default_parallel_*` with
`default: true`) to model the fan-out. Release rejects those synthetic nodes
(`exclusive gateway ... has no condition set and is not marked as default`,
confirmed live — the release API does not honor a node-level `default: true`),
and the visual editor also crashes on import. List the real targets in `next:`
instead.

Do **not** model parallel work as a serial chain
(`EnrichDomain → EnrichIP → EnrichHash`). A serial chain runs each call only
after the previous one finishes, and it is not what "in parallel" means — the
graph the console draws will be a single stacked column, not parallel branches.

### Converge parallel branches (do NOT use a `default: true` join node)

When several parallel branches need to rejoin before a shared downstream action
(an LLM summary, an email), **have every branch point its `next:` directly at the
convergence target**, and have each gate's `else:` clause point at the **same
target**. Do NOT insert a synthetic join node — a condition with only
`default: true` and a `next:`, used purely to funnel branches together. Release
rejects it: `exclusive gateway "<join>" outgoing flow ... has no condition set
and is not marked as default` (confirmed live).

```yaml
conditions:
    ip_present:
        next: [EnrichIP]
        cel_expression: "data['QueryDetection.results'][0].RemoteIP != null && data['QueryDetection.results'][0].RemoteIP != ''"
        else: [SummarizeEnrichment]        # else converges straight on the target
    domain_present:
        next: [EnrichDomain]
        cel_expression: "data['QueryDetection.results'][0].DomainName != null && data['QueryDetection.results'][0].DomainName != ''"
        else: [SummarizeEnrichment]
actions:
    EnrichIP:     { id: ..., next: [SummarizeEnrichment], properties: {...} }
    EnrichDomain: { id: ..., next: [SummarizeEnrichment], properties: {...} }
    SummarizeEnrichment: { id: ..., next: [SendEmail], properties: {...} }
```

Both the enrichment action (`next`) and the skip path (`else`) land on
`SummarizeEnrichment` directly. No join node — the convergence is expressed by
many edges pointing at one target, which is what released cleanly. (Note: if the
Event Query returns no rows, every gate's `else` fires and all branches skip —
the workflow still terminates on the convergence target but produces empty
enrichment. Expect this when testing with mock data that doesn't populate the
query's repo.)

### Gate an enrichment on the indicator being present

An enrichment should run only when the indicator it needs actually exists on the
detection. Otherwise the workflow fires calls like `.../domains/${empty}` on
every run. Gate each branch with a null check so a missing field skips that
branch instead of calling the API with an empty value:

```yaml
conditions:
    domain_present:                             # CEL null check
        next:
            - EnrichDomain
        cel_expression: data['QueryDetection.results'][0].DomainName != null
        display:
            - Domain is present
    ip_present:                                 # FQL-style equivalent: field:!null
        next:
            - EnrichIP
        expression: QueryDetection.results[0].RemoteIP:!null
        display:
            - IP is present
```

Combine the two patterns for the common "enrich every indicator that's present,
in parallel" shape: fan out from the query node by listing each indicator's gate
condition directly in `next:`, and have each gate's `next:` run its enrichment
call.

> **Every condition node needs either an `else:` branch or a match expression —
> never a bare `next:` alone.** A condition with only a `next:` and no
> `cel_expression` / `expression` imports and passes API validation, then **fails
> at release** with `exclusive gateway '<name>' ... has no condition set and is
> not marked as default`. A gated branch needs a `cel_expression` (or the
> FQL-style `expression`); its no-match fallthrough goes in `else:`. `validate.py`
> flags a condition that has neither an expression nor an `else:`. Never use a
> standalone `default: true` pass-through to fan out — that is the synthetic shape
> that crashes the canvas.

Here is the combined shape in full — fan out to each indicator's gate directly,
and gate each enrichment on its indicator being present:

```yaml
actions:
    QueryDetection:
        id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
        name: Query event - Hydrate detection
        version_constraint: ~1
        next:                                   # fan out to each gate directly
            - domain_present
            - ip_present
        properties:
            query: ...
    EnrichDomain: { id: ..., next: [SummarizeEnrichment], properties: {...} }
    EnrichIP:     { id: ..., next: [SummarizeEnrichment], properties: {...} }

conditions:
    domain_present:                            # gate (needs an expression)
        next:
            - EnrichDomain
        cel_expression: data['QueryDetection.results'][0].DomainName != null
    ip_present:
        next:
            - EnrichIP
        cel_expression: data['QueryDetection.results'][0].RemoteIP != null
```

For a complete, real example of both patterns, see the Content Library playbooks
`examples/threat-intel/ip-address-enrichment-abuseipdb.yaml` (parallel fan-out
plus a `cs.ip.valid` input gate) and `examples/threat-intel/domain-enrichment-virustotal.yaml`.

---

## Data references (`${data['...']}`)

### Trigger parameters
- `${data['param_name']}` — top-level trigger input
- `${data['param.nested_field']}` — nested object field

### Loop iteration
- `${data['array_param.#']}` — current item (simple array)
- `${data['array_param.#.field']}` — field of current object item

### Action output
- `${data['ActionLabel.FieldName']}` — output from a prior action
- `${data['ActionLabel.Nested.Path']}` — nested output field

### Custom variable
- `${data['WorkflowCustomVariable.field']}` — read current custom variable value

### Event trigger and system variables
Trigger fields and workflow/system fields (CID, execution ID, definition name,
etc.) live in the `data` namespace like every other field. Inside string
interpolation, reference them with the `${data['...']}` form — the system-level
fields are **not** an exception:
- `${data['Trigger.Category.Investigatable.Product.EPP.Sensor.SensorID']}`
- `${data['Trigger.Category.Incident.Name']}`
- `${data['Trigger.CID']}`
- `${data['Workflow.Execution.ID']}`
- `${data['Workflow.Execution.Time']}`
- `${data['Workflow.Definition.Name']}`

(The bare `${Trigger.X}` form is only for dedicated ID property fields — see the
interpolation-vs-dedicated-field note in `trigger-types.md`.)

---

## output_fields

Declares which fields are returned to the caller.

```yaml
# Inside a loop — collect per-iteration results
output_fields:
    - WorkflowCustomVariable.device_id
    - WorkflowCustomVariable.contained

# Top level — surface loop output
output_fields:
    - Loop.output
```

For non-loop workflows, output fields reference action results directly:
```yaml
output_fields:
    - ActionLabel.FieldName
```

---

## version_constraint

Required for class-based actions (CreateVariable, UpdateVariable), and wanted on
every other action too. The value is the tilde range for the major component of the
action's `semantic_version` (`~0` when it declares none), so read it from
`action_search.py --details` rather than assuming `~1`:

```yaml
version_constraint: ~1   # semantic_version 1.x.y; use ~0 for 0.x.y, ~2 for 2.x.y
```

Omitting this on a class-based action causes import validation failures. On a
non-class action, import and release both succeed without it, but the action's
output paths keep their older, longer form — see below.

### It also decides the shape of the action's output paths

Pinning a version is not only a compatibility guard. It changes how you reference
that action's output, and the two forms are mutually exclusive:

| `version_constraint` | Reference form |
|---|---|
| omitted | `${data['DeviceQuery.Device.query.devices']}` — carries the action's namespace |
| `~1` | `${data['DeviceQuery.devices']}` — node label plus field |

That middle segment is the action's `Namespace` as reported by
`action_search.py --details` (`device.query` for Device Query,
`device.get_details` for Get device details, `logscale.query_event` for Event
Query). Pin `~1`, leave the long path in place, and **release fails**:

```
property "Script" contains unknown variable "DeviceQuery.Device.query.devices"
```

So when you add a `version_constraint` to an existing workflow, shorten every
`${data['...']}` reference to that action in the same edit. Adding the pin alone
produces YAML that imports and then fails at release.

Confirmed against a live tenant for Device Query, Get device details, and Event
Query: with `~1` the short form resolves and the long form is rejected at
release; without it, the long form resolves.
