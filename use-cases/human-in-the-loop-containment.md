---
name: human-in-the-loop-containment
description: Gate Falcon device containment behind explicit human approval on a high-severity detection, using a "Request human input - Send email" action and an approval condition before the Contain device action runs, so automated response never isolates a host without an analyst's sign-off
source: https://www.reddit.com/r/crowdstrike/comments/1tpa4k3/20260527_workflow_wednesday_human_in_the_loop/
example: skills/authoring/examples/notifications/network-contain-endpoint-on-detection.yaml
skills: [authoring, deployment, execution]
capabilities: [workflow, response, human-in-the-loop, containment]
---

## When to Use

User wants a detection-triggered workflow that contains an endpoint, but with a human approval
gate in front of the containment action rather than acting automatically. The analyst gets an
email (or Slack) with the detection context, approves or declines, and the host is contained only
on approval. This is the "middle ground" pattern from the Workflow Wednesday "Human in the Loop
Automation" post: automated enrichment and orchestration up front, human judgment before the
response action.

This is grounded in the real Content Library playbook
`skills/authoring/examples/notifications/network-contain-endpoint-on-detection.yaml`, which
contains a severity gate, a per-product branch, the "Request human input - Send email" action with
Approve/Decline responses, an approval condition, and the "Contain device" action. Read that file
for the full structure; it handles EPP, QuickScanPro, Data Protection, and NG-SIEM detection
products, each with its own approval-then-contain branch.

## Pattern

1. **Trigger on the detection (Signal).** The workflow uses a `Signal` trigger
   (`event: Investigatable`, `version_constraint: ~1`) that fires on new detections.
2. **Gate on severity.** The first condition is an FQL expression
   `Trigger.Detection.Severity:>=5` ("Severity is greater than or equal to Critical"). Detection
   severity is numeric (4 = High, 5 = Critical), so change the threshold to `>=4` if you want High
   and above. Only detections meeting the threshold continue.
3. **Branch by product and confirm a host is present.** A per-product condition set routes EPP,
   QuickScanPro, Data Protection, and NG-SIEM detections to their own paths (the NG-SIEM path first
   runs a Device Query to resolve hostnames to sensors). Each branch confirms an agent/sensor ID is
   present before asking for approval.
4. **Request human input.** Each branch calls "Request human input - Send email"
   (`d6731c10b24834e2e0f4bd9d390a29c8`) with `allowed_responses: [Approve, Decline]`, an HTML
   message carrying host, detection name, severity, and tactic, and a `user_input_timeout` of
   `90m`. This example is email-only. If your team prefers Slack, Fusion also ships an equivalent
   "Request human input - Send Slack message" action (id `1ecc2f19f3deb1c607c07a2d755eb538`) that is
   not used in this workflow; discover and confirm it in your CID with
   `action_search.py --search "request human input"` before swapping it in.
5. **Condition on the response.** An FQL condition
   `RequestHumanInputSendEmail.RequestHumanInput.SendEmail.result.user_response:'Approve'` creates
   the true branch. A companion condition matches `Decline` or `Timed out` and routes to a comment
   explaining no approval was obtained.
6. **Contain only on approval.** The approve branch calls "Contain device"
   (`bec9fbeb4999d207937854fd56088107`) with the branch's device/sensor ID and a note recording who
   approved (`...SendEmail.result.responder`). The NG-SIEM path contains each resolved host in a
   `For each ... Sequentially` loop.
7. **Comment the outcome back onto the detection.** "Add comment to detection"
   (`7b77cb5d5ff2651cc51c7c4c610d54d1`) records the approval/decline result on the detection either
   way, for the audit trail.
8. **Validate, then deploy.** Run `validate.py`, then import and release to the CID. The playbook
   ships in dry-run mode (`enable_preventive_actions` variable defaults to false); it only contains
   when that variable is set to true, so test before enabling preventive mode.

## Key Actions

Every id below is taken directly from the source YAML. Where the source file does **not** set a
`version_constraint` on an action, the example omits it but the correct explicit value has been
confirmed against the live action catalog (the constraint is `~<major>` of the action's
`semantic_version`, defaulting to `~0` when none). Author with these explicit values; a vetted
value still wins if an import ever rejects a derived one.

| Action | `id` | version_constraint |
|--------|------|--------------------|
| Request human input - Send email | `d6731c10b24834e2e0f4bd9d390a29c8` | `~1` (example omits it; `~1` per the action catalog via `action_search.py --details`) |
| Contain device | `bec9fbeb4999d207937854fd56088107` | `~0` (example omits it; `~0` per the action catalog via `action_search.py --details`, no semantic_version) |
| Device Query | `68ffa99af40c84b36462daa076f535d0` | `~1` (example omits it; `~1` per the action catalog via `action_search.py --details`) |
| Add comment to detection | `7b77cb5d5ff2651cc51c7c4c610d54d1` | `~0` |
| Create variable | `702d15788dbbffdf0b68d8e2f3599aa4` | `~1` (class `CreateVariable`) |
| Update variable | `6c6eab39063fa3b72d98c82af60deb8a` | `~1` (class `UpdateVariable`) |
| Write to log repo | `04c59ceb6dff9e6cd89e5f5cf13121ab` | `~1` (example omits it; `~1` per the action catalog via `action_search.py --details`) |

Approval and containment are both wired through conditions, not `else` branches on the request
action: a dedicated "Human response is equal to Approve" condition feeds the Contain device action,
and a separate Decline/Timed out condition feeds the "no approval obtained" comment.

## When to Route Elsewhere

Keep the approval gate in the workflow. If the request needs a custom approval UI, a durable
approval record in a collection, or serverless logic to decide who to route the approval to, that
is a Foundry app concern — route to foundry-skills.
