---
name: charlotte-agent-invocation
description: Automatically invoke a published Charlotte AI (AgentWorks) agent from a Falcon Fusion workflow when a detection fires, passing the detection ID so the agent can triage, correlate, and update cases
source: CrowdStrike Workflow Wednesday "Building an AI Agent with Charlotte AI AgentWorks" (https://www.reddit.com/r/crowdstrike/comments/1uxkatu/20260715_workflow_wednesday_building_an_ai_agent/)
skills: [authoring, deployment]
capabilities: [workflow, charlotte-ai, detection-trigger]
---

## When to Use

User has built and published a Charlotte AI agent in AgentWorks (for example, a
case-triage agent that groups related detections, checks for an existing open case,
and adds an analysis comment) and wants that agent to run automatically whenever a
detection fires, instead of invoking it by hand from the Charlotte AI console.

This use case is the Fusion half of the pattern only. Building, testing, and
publishing the agent happens in Charlotte AI > AgentWorks, not in a workflow. Once
the agent is published, AgentWorks offers a "generate a workflow using this agent"
option that produces the workflow described below; this use case documents how to
review and scope that workflow, and how to author an equivalent one from scratch.

## Pattern

1. **Trigger on the Detection type.** Use a Signal trigger on the `Detection`
   category, which fires for every detection. The generated workflow defaults to
   this broad trigger, so scoping it down is the important step (next).
2. **Scope with a condition on `Product`.** A Detection trigger fires for all
   detection products, so add a condition node whose parameter is `Product` and
   narrow to the detection type(s) the agent is meant to handle, e.g.
   `EPP Detection`, `NG-SIEM Detection`. Without this the agent runs (and spends
   Charlotte AI credits) on every detection in the tenant.
3. **Invoke the agent.** Add the published agent's Agent action, pass the
   detection ID as its `input`, and pin `version_constraint: ~1`:

   ```yaml
   invoke_agent:
       id: <agent action ID, discovered in this CID>
       properties:
           input: ${data['Trigger.Detection.DetectionID']}
           credit_limit: 10          # optional Charlotte AI credit ceiling
       version_constraint: ~1
       next:
           - print_reply
   ```

   Three details each broke a real deploy or run (verified end to end on US-2):

   - **The input key is `input`, not `prompt`.** The action takes `input` (string
     — the prompt or data for the agent) and an optional `credit_limit` (int — the
     Charlotte AI credit consumption limit, so the credit guard has a name).
   - **`version_constraint` is `~1`, not `~0`.** The published agent action is
     versioned (`version: 1`), so `~0` fails the deploy with `(2018) Action was
     not found, please select a new action.` even when the ID is exact. This is
     the same `~<major of semantic_version>` rule used everywhere; do not carry
     the LLM Completion action's `~0` over to an agent action. An agent
     republished at a new major version needs its constraint raised to match,
     since agent major versions do not update automatically in workflows.
   - **Read the reply as `${data['<action key>.response']}`.** The output is a
     plain string in `response` (there is also a `_reference_links` array of
     `{display, url}`). There is no `.output` segment and no `cs.json.decode()`
     for a text agent — a later step consumes `response` directly, e.g. a Print
     data action:

   ```yaml
   print_reply:
       id: aadbf530e35fc452a032f5f8acaaac2a
       properties:
           text_data: ${data['invoke_agent.response']}
       version_constraint: ~1
   ```

   `Trigger.Detection.DetectionID` is the release-verified path for a Detection
   trigger (see `skills/authoring/references/trigger-types.md`); do not use the
   bare `${Trigger.X.Y}` form or a `Trigger.Category.Investigatable.*` path, both
   of which release validation rejects.
4. **Save, publish, and enable.** The agent receives the detection ID, performs its
   analysis, checks for related cases, and either recommends or takes approved
   action per its own instructions.
5. **Validate, then deploy.** Run `validate.py`, then import and release to the CID.

## Key Actions

The Agent action is generated per published AgentWorks agent and is **CID-specific**:
its action ID has the form `<agent UUID without dashes>_<CID>`, so it exists only in
the tenant where the agent was published and will not resolve in another CID.
Discover it with `action_search.py` in the target CID (or
`foundry workflows actions view --name "<agent name>"`), or ask the user which
published agent to invoke. Never fabricate or hardcode an agent action ID, exactly
as with plugin `config_id` values.

| Action | Role | Notes |
|--------|------|-------|
| Agent (published AgentWorks agent) | Invokes the agent with the detection ID | CID-specific ID; discover per tenant or ask the user |

## Gotchas

- **Human-in-the-loop vs. autonomous.** If the agent keeps the "Ask for clarification"
  tool enabled, it pauses for approval before making changes; those approvals surface
  under Charlotte AI > Action requests rather than completing automatically. Decide
  with the user whether the agent should be fully autonomous or gated on approval.
- **Credit consumption.** Each invocation spends Charlotte AI credits. The `Product`
  condition is what keeps an all-detections trigger from draining credits; set a
  per-action credit limit as a second guard.
- **Eligibility.** AgentWorks and Charlotte AI credits require a qualifying Falcon
  license and the Charlotte AI credits opt-in; a workflow that invokes an agent will
  not run usefully in a tenant without them.

## When to Route Elsewhere

Building the agent itself (prompt, tools, guardrails, testing) is Charlotte AI >
AgentWorks console work, not a Fusion workflow. Keep the workflow to the invoke
step. If the agent needs to reach an external system through an on-demand workflow,
that is a separate workflow the agent calls, not part of this trigger-and-invoke
pattern.

If you want the agent and the workflow packaged together and installable across
CIDs, that is a Falcon Foundry app rather than a standalone workflow. An app-scoped
workflow calls its own agent by the portable alias `id: ai_agents.<agent name>`,
which the Foundry API resolves per CID at deploy (see the `crowdstrike-falcon-foundry`
plugin). A standalone Fusion workflow has no app to resolve that alias against, so
it must use the CID-specific agent action ID discovered above — do not use the
`ai_agents.` alias in a standalone workflow.
