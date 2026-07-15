# Workflow Examples

Production-grade workflow examples from the CrowdStrike Content Library, converted
to YAML for developer readability. Each file is a real Falcon Fusion playbook
demonstrating a common automation pattern — IOC enrichment, identity response,
network containment, notifications, and more.

## Categories

| Category | Count | Description |
|----------|-------|-------------|
| `threat-intel/` | 6 | IOC enrichment with VirusTotal, AbuseIPDB, Pulsedive, and Zscaler blocklisting. |
| `identity-response/` | 2 | Identity detection response and phishing remediation with Identity Threat Protection. |
| `notifications/` | 2 | Alert routing, Slack notifications, and human-approved endpoint containment. |
| `ngsiem/` | 1 | Falcon Next-Gen SIEM duplicate-detection management. |
| `response-actions/` | 6 | Palo Alto Networks NGFW integration for network-level response (DAG tags, EDLs). |
| `tutorials/` | 8 | "Introduction to..." playbooks teaching specific Fusion concepts. |

## Workflows

### threat-intel

- [Domain Enrichment VirusTotal](threat-intel/domain-enrichment-virustotal.yaml)
- [IP Address Enrichment AbuseIPDB](threat-intel/ip-address-enrichment-abuseipdb.yaml) — parallel fan-out (multi-target `next:`) and input gating (`cs.ip.valid`)
- [Enrich URL on-demand with VirusTotal and add to Zscaler blocklist](threat-intel/enrich-url-virustotal-zscaler-blocklist.yaml)
- [Domain Enrichment Pulsedive](threat-intel/domain-enrichment-pulsedive.yaml) — fan-out → converge: parallel Pulsedive lookups store their real response objects in `WorkflowCustomVariable`, read by downstream comment/tag actions
- [Enrich an IP with VirusTotal and email an AI summary](threat-intel/enrich-ip-virustotal-llm-email.yaml) — on-demand `ip` enrichment → stores real VT fields in a variable → Charlotte AI HTML summary → email; shows both response-schema blocks (`_cs_inline_output_schema` for release-time refs, `inline_configuration.output_schema` for runtime 406 avoidance)
- [Analyze and enrich an EPP detection with a Charlotte AI LLM](threat-intel/analyze-enrich-epp-detection-llm.yaml) — a real Content Library playbook (exported unmodified) that forwards the model's actual `risk_level`/`verdict`/`confidence`/`conclusion` fields into detection comments and tags, gated on `cs.json.valid(...)`

### identity-response

- [Email phishing playbook with Identity Threat Protection actions](identity-response/email-phishing-playbook-itp.yaml)
- [Identity Detection Auto-Resolution - Recent Password Change](identity-response/identity-detection-auto-resolution.yaml)

### notifications

- [Network Contain Endpoint on Detection](notifications/network-contain-endpoint-on-detection.yaml)
- [Slack - Send Message to Channel](notifications/slack-send-message-to-channel.yaml)

### ngsiem

- [Close Duplicate Next-Gen SIEM Detections Automatically](ngsiem/close-duplicate-detections.yaml)

### response-actions

- [PAN NGFW - Allowlist - Add to EDL Exception List](response-actions/pan-ngfw-allowlist-edl-exception.yaml)
- [PAN NGFW - Blocklist to EDL and Force Refresh](response-actions/pan-ngfw-blocklist-edl-force-refresh.yaml)
- [PAN NGFW - Get All EDLs](response-actions/pan-ngfw-get-all-edls.yaml)
- [PAN NGFW - Monitor Dynamic Address Group Members](response-actions/pan-ngfw-monitor-dag-members.yaml)
- [PAN NGFW - Register IP to Tag (DAG)](response-actions/pan-ngfw-register-ip-tag-dag.yaml)
- [PAN NGFW - Unregister IP from Tag (DAG)](response-actions/pan-ngfw-unregister-ip-from-tag-dag.yaml)

### tutorials

- [Introduction to Cases: How to add an event to a case](tutorials/intro-cases-add-event.yaml)
- [Introduction to data transforms: How to use a ternary operator](tutorials/intro-data-transforms-ternary.yaml)
- [Introduction to error handling](tutorials/intro-error-handling.yaml)
- [Introduction to lookup file actions](tutorials/intro-lookup-file-actions.yaml)
- [Introduction to the Python script action: build a lookup file from an external feed](tutorials/intro-python-sslbl-lookup.yaml) — an `Inline.Python` action fetches and reshapes the abuse.ch SSL blocklist, and its `output_stdout` feeds a lookup file
- [Introduction to Receive Email trigger: How to create a lookup file from an email attachment](tutorials/intro-receive-email-trigger.yaml)
- [Introduction to variables: How to append to an array](tutorials/intro-variables-append-array.yaml)
- [CrowdStrike HTTP Request: query the Falcon Alerts API and email the result](tutorials/crowdstrike-http-request-falcon-api.yaml) — the canonical **CrowdStrike HTTP Request** shape (Falcon platform API, distinct from a Cloud HTTP Request): absolute region host, query params in `request_query` (not the URL), required OAuth credential (`UseExisting` + `config_id`). Built and executed live end-to-end.

## How to use these examples

These examples demonstrate real workflow patterns. Copy and adapt them to fit your
own automations. The structure follows the documented Fusion workflow YAML schema:
a `trigger` that starts the workflow, `actions` (and `loops` / `conditions`) that do
the work, and `output_fields` that surface results to the caller.

**All action IDs are real values from the CrowdStrike platform.** The 32-character
hex IDs (such as `702d15788dbbffdf0b68d8e2f3599aa4` for Create variable) are global
and work across clouds. CrowdStrike-native actions import directly; third-party
actions (Slack, Zscaler, PAN NGFW) reference plugin instances that are specific to
your CID and need to be configured before the workflow will run.

CEL expressions, FQL conditions, trigger configurations, and `version_constraint`
values are preserved exactly as they appear in the source playbooks.

## A note on the source format

These workflows are real Falcon Fusion playbooks from the CrowdStrike Content
Library, in the Falcon console's own **import/export** format: a flat `trigger`
plus an `actions` map with `next` edges, `conditions`, and `loops`. Every example
is verified against the live import API (`import_definition` with `validate_only`).

Examples come from one of two sources, noted in each file's header:

- **Console export (preferred, definitive).** Installed from the Content Library
  and exported unmodified from the console. This is ground truth — it round-trips
  through Fusion's own serializer, so it imports cleanly by construction. Newer
  examples (e.g. the VirusTotal, AbuseIPDB, phishing-ITP, and LLM-enrichment
  playbooks) use this path.
- **Deterministic conversion.** Some examples were produced by running
  `bin/convert_catalog_to_yaml.py` on a Content Library catalog record (JSON).
  The converter resolves the Signal `event:` from the trigger catalog
  automatically. Prefer a console export when one is available; the catalog record
  is not the import format and conversion is a best-effort reproduction of it.

**Authoring guardrail — always ground new examples in a real workflow.** Never
hand-translate a catalog record: an early version of these examples dropped the
Signal trigger's required `event:` field and invented a trigger `id`, which broke
import. To add or fix an example, either export a working workflow from the console
(`export_definition`) — the preferred path — or run
`bin/convert_catalog_to_yaml.py` on a catalog record. A Signal trigger must carry
`event:` (the trigger category, e.g. `Investigatable/NGSIEM`); discover values with
`trigger_search.py --events`.
