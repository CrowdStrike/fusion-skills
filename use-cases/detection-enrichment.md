---
name: detection-enrichment
description: Enrich a detection's indicators (IP, domain, URL, file hash) with VirusTotal threat intelligence in a Falcon Fusion workflow, then write the results back as case/alert comments and tags or push malicious indicators to a blocklist
source: CrowdStrike Content Library playbooks "Domain Enrichment VirusTotal" (https://falcon.crowdstrike.com/login/?unilogin=true&next=/content-library/details/global:fusion_playbook:3452ba7ac9334fef873e029cd77f3a5d) and "Enrich URL on-demand with VirusTotal and add to Zscaler blocklist" (https://falcon.crowdstrike.com/login/?unilogin=true&next=/content-library/details/global:fusion_playbook:869720e0f8464b388d6aa25724b451c7)
example: skills/authoring/examples/threat-intel/domain-enrichment-virustotal.yaml, skills/authoring/examples/threat-intel/enrich-url-virustotal-zscaler-blocklist.yaml
skills: [authoring, deployment, execution]
capabilities: [workflow, enrichment, http-action]
---

## When to Use

User wants a workflow that takes an indicator from a detection or case (a domain, URL, IP, or
file hash), looks it up against VirusTotal, and records the verdict — either annotating the
Next-Gen SIEM case/alert or pushing a malicious indicator to a blocklist. This is grounded in
two real Content Library playbooks:

- **Primary reference** — `skills/authoring/examples/threat-intel/domain-enrichment-virustotal.yaml`
  (compact and complete: trigger, VirusTotal enrichment, variable/loop handling, and case
  comment/tag write-back). Read this one for the overall structure.
- **Blocklist variation** — `skills/authoring/examples/threat-intel/enrich-url-virustotal-zscaler-blocklist.yaml`
  shows the enrich-then-block path (gateway on malicious count → Zscaler). It is large and mostly
  an auto-generated VirusTotal response schema; you do not need to read it in full. Consult its
  header structure map and jump to the gateway/Zscaler section near the end only if you need the
  blocklist pattern.

## Pattern

1. **Trigger on demand.** Both examples use an On demand trigger with a parameter schema — the
   domain playbook takes `case_id` (`ngsiemCaseID`), `detection_id` (`investigatableID`), and
   `domain` (`domain` format); the URL playbook takes a required `URL_to_scan` (`url` format).
   On demand lets the workflow run from a Case or be called by another playbook with the entity.
2. **Enrich with VirusTotal.** Call VirusTotal for the indicator. The URL playbook uses a Cloud
   HTTP Request (`Inline.HTTPRequest`, `1ba474f407d9228fc8fa02cdce8ae8ef`) against the VirusTotal
   API with a defined output schema; the domain playbook uses VirusTotal plugin actions.
3. **Shape the results.** Both playbooks build up comment and tag strings with
   `CreateVariable` / `UpdateVariable` actions, looping over nested results (e.g. the domain
   playbook iterates DNS A-record resolution IDs with a "For each … Sequentially" loop) and
   chunking long comments.
4. **Act on the verdict.**
   - *Annotate:* the domain playbook writes back with "Add comment to alert", "Add Comment to
     Case", and "Add tags to case", keyed off `${detection_id}` and `${case_id}`.
   - *Block:* the URL playbook branches on how many VirusTotal sources flagged the URL and, when
     malicious, calls "ZScaler - Add URL or IP to Blacklist" (a plugin action needing a
     `config_id`).
5. **Validate, then deploy.** Run `validate.py`, then import and release to the CID.

## Key Actions

The comment, case, tag, and variable actions and their constraints are taken directly from the
domain-enrichment example. The Cloud HTTP Request constraint is the value verified in the
authoring skill's Common Action IDs table (the url-zscaler example omits `version_constraint`, so
confirm it with `action_search.py --details` if an import rejects it).

| Action | `id` | version_constraint |
|--------|------|--------------------|
| Cloud HTTP Request (`Inline.HTTPRequest`) | `1ba474f407d9228fc8fa02cdce8ae8ef` | `~1` |
| Add comment to alert | `7b77cb5d5ff2651cc51c7c4c610d54d1` | `~0` |
| Add Comment to Case | `a16f4fdd1b244b0bfeecd47e25dbe0e0` | `~1` |
| Add tags to case | `696f57b7cdcd475e5c56e6196836ee39` | `~1` |
| Create variable | `702d15788dbbffdf0b68d8e2f3599aa4` | `~1` |

VirusTotal and Zscaler plugin actions reference a `config_id` created in the console and specific
to the CID. Discover it or ask the user; never invent one.

## When to Route Elsewhere

Keep enrichment in the workflow when each lookup feeds the next step directly. Build a Foundry
function (route to foundry-skills) when enrichment needs complex result transformation in code,
pagination across large result sets, or is paired with a UI or collection.
