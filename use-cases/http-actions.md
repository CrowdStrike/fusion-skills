---
name: http-actions
description: Build API integrations using Falcon Fusion HTTP Actions (Cloud, CrowdStrike, On-Premises) without a Foundry app wrapper
source: https://www.crowdstrike.com/tech-hub/ng-siem/build-api-integrations-with-falcon-fusion-soar-http-actions/
skills: [authoring, deployment]
capabilities: [workflow, http-action]
---

## When to Use

User wants to call an external REST API (VirusTotal, Slack, PagerDuty, ServiceNow) from a
Fusion workflow without building a Falcon Foundry app. HTTP Actions cover the vast
majority of API-integration needs: a single REST call with conditional handling of the
response, authored as workflow YAML and deployed directly to a CID.

**Use HTTP Actions when:**
- The integration is a simple REST call (GET/POST/PUT/DELETE) used by one workflow.
- Auth is an API key or OAuth 2.0 (configured in the console — before or after deploy).
- You want quick turnaround with no app scaffolding.

## Pattern

1. **Pick the HTTP Action type:**
   - **Cloud HTTP Request** — external internet APIs (VirusTotal, Slack, PagerDuty).
   - **CrowdStrike HTTP Request** — Falcon platform APIs (auto-authenticated via tenant context).
   - **On-Premises HTTP Request** — internal APIs behind a firewall, reached via a static host group.
2. **Author credential-less by default.** For a fresh integration you won't have a credential
   `definition_id` yet — omit it and leave authentication unset. The action imports with
   Authentication = "None"; the user attaches the API key in the console after deploy. Never invent
   a `definition_id` (a placeholder is a broken reference; `validate.py` flags it). Only set it to a
   real 32-char hex id when the user supplies one.
3. **Author the workflow.** Add an `Inline.HTTPRequest` action with the URL, method, headers,
   query params, and body. Inject runtime values with `${data['...']}` syntax (e.g. a trigger
   parameter `${data['ip']}`).
4. **Declare the response schema so downstream actions can read fields.** A Cloud HTTP Request
   whose response fields are referenced downstream MUST carry a `_cs_inline_output_schema` (a
   JSON-schema string) inside its `http_transaction`. Reference a field with the **direct JSON
   path** — `${data['<ActionLabel>.data.field']}` — NOT a `.HTTP.body.` prefix (that form is
   rejected at release as an unknown variable; verified live). The status code is
   `${data['<ActionLabel>.response_status_code']}`. Capture the real shape by attaching the
   credential (step 6), clicking **Test**, then **Schema builder** in the console — a hand-written
   schema that omits fields the API returns, or mistypes one, fails at runtime with a **406**
   ("script output does not validate against the output JSON schema") that no local check, import,
   or release catches. See `../skills/authoring/references/http-actions.md` "Reading the response".
5. **Branch on the response.** Add a CEL condition on `response_status_code` (200 vs 404 vs error).
6. **Validate, deploy, then configure the credential.** Run `validate.py`, import and release to the
   CID, then configure the API key in the console: open the action → Authentication → Create new →
   API key → secret key → location Header → header name (e.g. `x-apikey`) → Test → **Schema builder**
   → Save.

## Key Actions

| Action | Type | Purpose |
|--------|------|---------|
| Cloud HTTP Request | `Inline.HTTPRequest` | Calls an external REST API. `version_constraint: ~1` |
| CrowdStrike HTTP Request | `Inline.HTTPRequest` | Calls a Falcon platform API with tenant auth |
| On-Premises HTTP Request | `Inline.HTTPRequest` | Calls an internal API via a static host group |
| Condition | CEL gateway | Branches on `response_status_code` |

**Console-credential boundary:** the workflow YAML is authored outside the console, and the API
key/secret always lives in a console credential configuration — never in the YAML. You do NOT need
that config to exist before deploy: author the HTTP Action credential-less (no `definition_id`),
deploy, then attach the credential in the console. Only reference an existing `definition_id` (a
real 32-char hex id) when the user supplies one; never invent one.

## Formatting an LLM summary for email

A common HTTP-Action pattern is: call an API, summarize the response with a Charlotte AI LLM
Completion action, then email the summary. Charlotte AI returns **Markdown** by default, which
renders as literal `##` and `**` in an HTML email. Three tiers, cleanest last:

1. **Default (Markdown).** The email shows raw `##`/`**`. Fine for a plain-text email
   (`msg_type: text`); ugly in HTML.
2. **Prompt for HTML.** Append an instruction like `Respond with raw HTML only (headings, lists,
   bold); do not wrap it in markdown code fences.` to the LLM `user_prompt` and set the Send email
   action to `msg_type: html`. The summary then renders with real headings and lists instead of raw
   Markdown. Note the explicit "no code fences" part: `Format the response in HTML` alone often makes
   the model wrap its output in a ` ```html ` fence, which then shows up literally in the email
   (verified live). **Let the LLM completion BE the email body — don't rebuild a parallel template
   in the Send email action.** Since the model already emits the formatted HTML report, the `msg`
   should just wrap the completion: `msg: "<html><body>${data['<Node>.FaaS.nlpassistantapi.llminvocator_handler.completion']}</body></html>"`.
   Re-templating the enrichment fields inline (a second `<h3>` table of the same variables the LLM
   already summarized) is redundant and drifts from the model's output; forward the completion and
   let it own the layout. The shipped
   `../skills/authoring/examples/threat-intel/enrich-ip-virustotal-llm-email.yaml` example uses this
   approach (the enrichment path — VT call → variable → summary → email — is verified live
   end-to-end).
3. **Structured JSON (cleanest).** For a fully controlled layout, constrain the LLM to structured
   output with a `json_schema` on the Charlotte AI action, then read individual fields in the email
   with `cs.json.decode()`:

   ```yaml
   # Charlotte AI action: add a json_schema so completion is structured JSON
   # Email body reads decoded fields (FaaS is PascalCase, case-sensitive):
   ${cs.json.decode(data['Summarize.FaaS.nlpassistantapi.llminvocator_handler.completion']).risk_level}
   ```

   See `../skills/authoring/references/charlotte-ai-action.md` for the `json_schema` property and the
   exact `cs.json.decode(...)` output path. Write valid JSON for the schema — Fusion passes it to the
   LLM as a formatting hint rather than strictly validating it, so a malformed schema can appear to
   "work" while quietly being invalid; don't copy one without checking it parses.

## When to Route Elsewhere

Build a **Foundry API integration** (route to foundry-skills, `crowdstrike-falcon-foundry`)
when you need a *reusable* integration shared across many workflows, paired with a UI or
serverless function, or wrapping multiple tightly coupled API operations. A one-off REST call
from a single workflow stays here as an HTTP Action.
