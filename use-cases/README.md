# Use Cases

Real-world patterns for building Falcon Fusion workflows. Some are extracted from
[CrowdStrike Tech Hub](https://www.crowdstrike.com/tech-hub/ng-siem/) articles;
others are grounded directly in the bundled example workflows under
`skills/authoring/examples/` and the community Workflow Wednesday series. Each file
captures an actionable pattern that Claude can apply when users describe similar
scenarios, and cites its `source:` so it can be traced back.

## How the Orchestrator Uses These

The `workflows` orchestrator skill globs `use-cases/*.md` and scans frontmatter
`description` fields to match a user request to a known pattern before delegating.
A use case names the sub-skills it needs in its `skills:` frontmatter, so the
orchestrator knows which phases (authoring, deployment, execution, lookup-files)
to coordinate.

## Available Use Cases

| Use case | Scenario |
|----------|----------|
| [detection-enrichment](detection-enrichment.md) | Enrich a detection's indicators with VirusTotal, then comment/tag the case or blocklist |
| [event-queries](event-queries.md) | Run a schemaless CQL/FQL query against the event store inside a workflow |
| [http-actions](http-actions.md) | Call an external REST API inline with a Cloud HTTP Request (no Foundry app) |
| [api-pagination](api-pagination.md) | Page through a large REST API result set inside a workflow |
| [lookup-enrichment](lookup-enrichment.md) | Enrich detections with third-party data via a Next-Gen SIEM lookup table |
| [custom-soar-actions](custom-soar-actions.md) | Drive a shared Foundry API action (list/deactivate users) from a workflow |
| [export-query-results-csv](export-query-results-csv.md) | Export Event Query results to CSV and write them to a lookup file |
| [human-in-the-loop-containment](human-in-the-loop-containment.md) | Gate device containment behind analyst approval on a high-severity detection |
| [detection-deduplication](detection-deduplication.md) | Find and close duplicate Next-Gen SIEM detections with an Event Query dedup |
| [case-management](case-management.md) | Query relevant events and attach them to a Next-Gen SIEM Case |
| [identity-detection-response](identity-detection-response.md) | Respond to an Identity Protection detection: get user context, then resolve or notify |
| [lookup-file-management](lookup-file-management.md) | Create/overwrite/append/update a lookup file from inside a workflow |
| [notifications](notifications.md) | Send a workflow notification to a chat channel (e.g. Slack) |
| [charlotte-agent-invocation](charlotte-agent-invocation.md) | Automatically invoke a published Charlotte AI (AgentWorks) agent when a detection fires |
| [ngsiem-detection-response](ngsiem-detection-response.md) | Respond to an NG-SIEM detection: filter, hydrate, extract, gate, summarize with Charlotte AI, and email |

## File Format

```markdown
---
name: use-case-name
description: One-line trigger for orchestrator pattern matching
source: https://www.crowdstrike.com/tech-hub/ng-siem/...  # public Tech Hub or Workflow Wednesday post
example: skills/authoring/examples/.../workflow.yaml  # bundled workflow this pattern is grounded in
skills: [authoring, deployment, execution]
---

## When to Use
What user request or scenario triggers this pattern.

## Pattern
Step-by-step solution using Fusion workflow actions, triggers, and conditions.

## Key Actions
The specific actions the pattern relies on (with their roles).

## Gotchas
Known issues, platform quirks, common mistakes.
```

`source:` names where the pattern comes from — a Tech Hub article, a Workflow
Wednesday post, or a CrowdStrike Content Library playbook (cited by name with a
cloud-agnostic deep link, `https://falcon.crowdstrike.com/login/?unilogin=true&next=/content-library/details/global:fusion_playbook:<id>`,
where Unified Login lets the user pick their cloud (US-1/US-2/EU-1) before landing on the playbook).
`example:` points at the bundled workflow the pattern is grounded in. Prefer
having both so the pattern can be independently verified.

## Adding New Use Cases

1. Create a new `.md` file in this directory following the format above
2. Keep files actionable patterns, not full blog summaries
3. Add a public `source:` (a Tech Hub or Workflow Wednesday URL) so the pattern can be independently verified; add `example:` pointing at the bundled workflow it's grounded in
4. List the relevant `skills:` in frontmatter so the orchestrator can plan phases
5. Ground the pattern in a real, verified example — every referenced action ID must exist in a bundled example or be discoverable via `action_search.py`
6. Cross-reference related use cases in the Pattern section

## Additional Resources

- [Tech Hub NG-SIEM Articles](https://www.crowdstrike.com/tech-hub/ng-siem/?cspage=0&lang=English&type=Article)
- Bundled example workflows: [`skills/authoring/examples/`](../skills/authoring/examples/README.md)
