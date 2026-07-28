# HTTP Action reference

HTTP Actions call REST APIs directly from a workflow — no Falcon Foundry app
required. They cover the majority of API integrations: threat-intel enrichment,
notifications, and data lookups. All three types share `class: Inline.HTTPRequest`
and `version_constraint: ~1`.

## The three types

| Type | Use for | Network | Auth |
|------|---------|---------|------|
| Cloud HTTP Request | External internet APIs (VirusTotal, Slack, Microsoft Graph) | Public internet from the Falcon platform | API key, Basic, OAuth 2.0 |
| CrowdStrike HTTP Request | Falcon platform APIs | CrowdStrike endpoints (absolute, region host) | OAuth credential required (`config_id`) |
| On-Premises HTTP Request | Internal APIs behind a firewall | Via a **static** host group (dynamic not supported; ≤20 hosts) | API key, Basic, OAuth |

All three serialize to `class: Inline.HTTPRequest`. The type is a console-side
distinction; discover the exact action ID with `action_search.py --search "HTTP"`.

## The `http_transaction` block

Every HTTP Action carries an `http_transaction` map under `properties`:

```yaml
MyRequest:
  id: <32-char hex from action_search.py>
  class: Inline.HTTPRequest
  name: Cloud HTTP Request - VirusTotal IP
  version_constraint: ~1
  next:
    - NextNode
  properties:
    # No credential here by default — configure it in the console after deploy.
    # See Authentication below.
    http_transaction:
      request_http_method: GET             # GET | POST | PUT | DELETE | ...
      request_url: https://www.virustotal.com/api/v3/ip_addresses/${data['WorkflowCustomVariable.ip']}
      request_content_type: NONE           # NONE | JSON
      request_headers: {}
      request_body: '{}'                   # present for POST/PUT; '{}' when none
```

- **`request_url`** supports `${...}` variable injection (trigger inputs,
  `${data['...']}` CEL refs, prior action outputs). Variables resolve at
  execution, not at test time.
- **`request_content_type`** is `NONE` for GET with no body, `JSON` when sending
  a body.
- **`request_query`** (optional) is the console "Query" tab: a UUID-keyed map of
  `{name, value}` pairs, encoded into the query string for you.

  ```yaml
      request_query:
        c373ed43-231d-4141-ba4e-c0214b9587bb:
          name: $select
          value: displayName,mail,jobTitle,department
  ```

## Authentication

**Secrets never appear in the workflow YAML.** The API key or client secret lives
in a credential configuration created in the Falcon console (CID-specific). The
YAML at most references that config by `definition_id`.

**Default: author credential-less, configure in the console after deploy.** For a
fresh integration (VirusTotal, DomainTools, etc.) you almost never have a
`definition_id` at authoring time, and you should NOT invent one. Author the HTTP
Action with **no** `definition_id` and **no** `authentication_option` — the
imported action simply shows Authentication = "None" in the console, which is a
valid, editable starting point. This is proven: a credential-less HTTP Action
imports cleanly, passes validation, renders in the console, and runs once you
attach a credential there. Then tell the user the console steps:

> In the workflow, open the Cloud HTTP Request action → **Authentication** →
> **Create new** → Configuration name: (e.g. `VirusTotal`) → Authentication type:
> **API key** → API secret key: `<your key>` → API key location: **Header** →
> Header name: `x-apikey` (per the API's docs) → **Test** → **Schema builder** →
> Save. (If a matching credential already exists, pick **Use existing** instead.)

**Never fabricate a `definition_id`.** A token like
`VIRUSTOTAL_CREDENTIAL_CONFIG_ID` or any non-hex placeholder is a broken
reference that dead-ends the user — worse than omitting it. `validate.py` flags a
placeholder `definition_id` on an HTTP Action. If you don't have a real 32-char
hex id, leave `definition_id` out entirely (credential-less, above).

**Reference an existing credential config only when the user supplies a real id.**
A real `definition_id` is 32 hex chars, CID-specific, and only exists once the
integration is configured in the console:

```yaml
    authentication_option: UseExisting
    definition_id: 7227ab386bd646c18b27716e8fff8d26   # real hex, user-supplied
```

**Attach-a-new-key shape** (what the console writes when you configure an API key
on the action) — `CreateNew` plus the header fields:

```yaml
    authentication_option: CreateNew
    api_key_header_label: x-apikey
    api_key_location: Header
    definition_id: 7227ab386bd646c18b27716e8fff8d26   # written by the console
```

**OAuth 2.0 (client credentials)** — e.g. Microsoft Graph, Okta:

```yaml
    authentication_option: CreateNew        # or UseExisting for a saved config
    definition_id: 662c4828b3804ad287acc7fc3cd9895b
    oauth_token_url: https://login.microsoftonline.com/{tenant-id}/oauth2/v2.0/token
    oauth_scopes:
      - https://graph.microsoft.com/.default
```

`definition_id` values are **CID-specific** — never invent one. Prefer authoring
credential-less and configuring in the console; only set `definition_id` to a real
hex id the user provides. See the Console-Credential Boundary section in `SKILL.md`.

## CrowdStrike HTTP Request

Calls Falcon platform APIs. Verified live end-to-end (built, published, executed
in the console); see the worked example
`examples/tutorials/crowdstrike-http-request-falcon-api.yaml`. Four things the
console enforces that are easy to get wrong:

- **`request_url` is ABSOLUTE and region-specific** — e.g.
  `https://api.us-2.crowdstrike.com/alerts/queries/alerts/v2` (US-1 =
  `api.crowdstrike.com`, EU-1 = `api.eu-1.crowdstrike.com`). A relative path
  (`/alerts/...`) is rejected with "Invalid URL format".
- **No query string in the URL.** Query params go in the `request_query`
  UUID-map (the console "Query" tab); a `?filter=...` in the URL is rejected with
  "URL should not contain query parameters".
- **Authentication is REQUIRED** (no credential-less option, unlike a Cloud HTTP
  Request). Reference a saved credential with `authentication_option: UseExisting`
  plus `config_id` (32-hex) and `config_name`, or create one inline
  (`CreateNew` with `definition_id` and `oauth_token_url`). The API client behind
  the credential needs the scope for the endpoint (e.g. **Alerts** read for
  `/alerts/...`). `config_id`/`definition_id` are CID-specific — never invent one.
- `class` is still `Inline.HTTPRequest` (same as a Cloud HTTP Request; the type is
  a console-side distinction).

Common endpoints: `/alerts/queries/alerts/v2`, `/detects/queries/detects/v1`,
`/incidents/queries/incidents/v1`, `/devices/combined/devices/v1`.

**Querying alerts/detections (the common case).** To fetch a population of
alerts, GET `/alerts/queries/alerts/v2` (then POST the returned IDs to
`/alerts/entities/alerts/v2` with a JSON body `{"ids": [...]}` for full records).
Filter with FQL on **`severity_name`** — e.g. `severity_name:'High'` (or
`'Critical'`) — NOT the numeric `severity` field, which is a different scale and
will not match what a user means by "high-severity." Combine conditions with `+`
(AND) and use relative times: `severity_name:'High'+created_timestamp:>'now-24h'`.
This is the standalone, tenant-authenticated way to query the alert population; see
`event-query-vs-api.md` for when to use this vs. an Event Query vs. a Foundry
function.

## Reading the response

Reference the response in later steps by the action's label, using the
**direct JSON path** — there is **no `.HTTP.body.` prefix** (that form is
rejected at release as an unknown variable; confirmed live and against the
shipped VirusTotal example):

- `${data['<ActionLabel>.<json.path>']}` — a field in the JSON body, e.g.
  `${data['EnrichIP.data.attributes.last_analysis_stats.malicious']}`.
- `${data['<ActionLabel>.response_status_code']}` — the numeric status code
  (used in a condition `expression:` as `<ActionLabel>.response_status_code:200`).

**`_cs_inline_output_schema` is REQUIRED to reference response fields.** A Cloud
HTTP Request whose response fields are read by any downstream action MUST declare
a `_cs_inline_output_schema` (a JSON-schema string describing the response shape)
inside its `http_transaction` block. Without it, release validation rejects every
body-field reference with "property contains unknown variable" — even though
import and `validate.py` pass. The shipped `enrich-url-virustotal-zscaler-blocklist.yaml`
example shows a full `_cs_inline_output_schema`.

**Declaring the schema is the mechanism for forwarding real enrichment data — it
is NOT an obstacle to route around.** The whole point of an enrichment workflow is
to carry the *actual* API verdict (VirusTotal's `malicious`/`suspicious` counts,
a reputation score, a threat list) into the summary and email. Do that by adding
the `_cs_inline_output_schema` and then referencing the real response fields
downstream. Do NOT dodge the schema requirement by storing a hand-written status
string — a workflow that stores `"VirusTotal enrichment completed successfully"`
instead of the real verdict passes validation and releases, but it is **hollow**:
it makes the API call, discards the result, and emails the same canned text
whether the indicator is malicious or clean. That is worse than a failing
workflow because it looks like it works. If a response field is read downstream,
add the schema; never substitute a literal for the data.

The same rule applies to any enrichment source, not just HTTP. The shipped
`examples/threat-intel/analyze-enrich-epp-detection-llm.yaml` (a real Content
Library playbook, exported unmodified) is the reference for doing it right with a
Charlotte AI LLM: it decodes the completion and forwards the model's **actual**
fields — `risk_level`, `verdict`, `confidence`, `conclusion`, `recommended_actions`
— into detection comments and tags via `WorkflowCustomVariable`, gated on
`cs.json.valid(...)`. It never stores a canned "analysis completed" string.

**Parallel branches: store outputs in a variable before converging.** When
several HTTP actions run in parallel and a shared downstream action (an LLM
summary, an email) reads their fields, the release validator cannot resolve those
references directly — even with `_cs_inline_output_schema` — because it can't
guarantee which branch ran. Each branch must write its results to a
`WorkflowCustomVariable` via an `UpdateVariable` action, and the convergence
action must reference only `WorkflowCustomVariable.*` fields (always in scope).
**The value stored by `UpdateVariable` MUST be the real HTTP response, referenced
as `${data['<HTTPAction>.<json.path>']}` — not a static status string.** The HTTP
action still needs its `_cs_inline_output_schema` for that reference to resolve at
release. For example, store
`${data['EnrichIP.data.attributes.last_analysis_stats.malicious']}` (or the whole
`${data['EnrichIP.data']}` object), NOT `"IP enrichment completed"`:

```yaml
# RIGHT — forwards the real VirusTotal verdict
UpdateVariableIP:
    class: UpdateVariable
    properties:
        WorkflowCustomVariable:
            ip_malicious: ${data['EnrichIP.data.attributes.last_analysis_stats.malicious']}
            ip_reputation: ${data['EnrichIP.data.attributes.reputation']}

# WRONG — hollow: discards the API result, emails canned text every time
UpdateVariableIP:
    properties:
        WorkflowCustomVariable:
            ip_enrichment: "VirusTotal IP enrichment completed successfully."
```

The shipped `examples/threat-intel/domain-enrichment-pulsedive.yaml` is the
canonical fan-out → converge pattern: parallel Pulsedive lookups each stash their
**actual response object** into a `WorkflowCustomVariable` via `UpdateVariable`
(e.g. `indicator_details_result` ←
`${data['PulsediveGetIndicatorDetails...body']}` and `explore_indicators_result`),
and every action after the join reads real fields like
`${data['WorkflowCustomVariable.indicator_details_result'].indicator}`.

Response body is capped at 10 MB and must be a JSON **object**, not an array.
Branch on `response_status_code` with a condition node (e.g. `== 200` vs `== 404`)
to handle success and error paths separately.

## Templates

130+ vendor APIs (VirusTotal, Slack, PagerDuty, ServiceNow, …) have console
templates that pre-fill URL, method, headers, and auth type — you still supply
credentials. Templates cover one API call, not a whole workflow. They are
unrelated to Falcon Foundry app templates.

See `examples/threat-intel/` and `examples/response-actions/` for complete
HTTP-Action workflows that import cleanly.
