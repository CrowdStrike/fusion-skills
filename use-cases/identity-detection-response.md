---
name: identity-detection-response
description: Respond to a Falcon Identity Protection detection in a Falcon Fusion workflow by getting the user's identity context, evaluating a condition (e.g. a recent password change), then auto-resolving the detection or notifying — reducing false-positive noise on identity alerts
source: https://www.reddit.com/r/crowdstrike/comments/1oj9rve/cool_workflow_wednesday_password_compromise/
example: skills/authoring/examples/identity-response/identity-detection-auto-resolution.yaml
skills: [authoring, deployment, execution]
capabilities: [workflow, identity, response]
---

## When to Use

User wants a workflow that triages Identity Protection detections automatically — pulling the
involved user's identity context, deciding whether the alert is a likely false positive, and
either closing it or leaving it for an analyst. The grounding example auto-resolves "Password
Brute Force attack" detections when the user changed their password within 30 minutes of the
detection (recent password change → likely legitimate activity). This is corroborated by the
Workflow Wednesday "password compromise" post, which builds the same identity-response shape.

This is grounded in the real Content Library playbook
`skills/authoring/examples/identity-response/identity-detection-auto-resolution.yaml` — read it
for the exact structure: a Signal trigger → detection-name gate → get identity context →
time-window condition → set status → comment.

## Pattern

1. **Trigger on the identity detection.** The example uses a **Signal** trigger named
   `Detection > Identity Detection` with `event: Investigatable/IDP` and
   `version_constraint: ~0`. This fires the workflow on each Identity Protection detection.
2. **Gate on detection name (optional but recommended).** The example's first condition is an FQL
   `expression` on `Trigger.Category.Investigatable.Product.IDP.DetectName`, matching only
   `Password Brute Force attack (Active Directory)` and `...(web-based)` so the automation scopes
   to the detections it understands.
3. **Get the user's identity context.** Call **Get user identity context**
   (`version_constraint: ~1`), passing the user from the trigger —
   `entity_sid: ${Trigger.Category.Investigatable.Product.IDP.SourceAccountObjectSid}` and
   `username: ${Trigger.Category.Investigatable.Product.IDP.SourceAccountName}`. Set
   `continue_if_entity_not_found: false` so the workflow stops if the user can't be resolved.
4. **Evaluate a condition.** The example uses a CEL condition comparing the detection end time to
   the user's last password change:
   `cs.timestamp.parse(data['Trigger.Category.Investigatable.Product.IDP.EndTime'], 'RFC3339') -
   cs.timestamp.parse(data['get_user_identity_context_....PasswordChange'], 'RFC3339') <
   duration('30m')`. When true, the detection is treated as a likely false positive.
5. **Act on the verdict.**
   - *Auto-resolve:* call **Set detection status** with
     `investigatable_id: ${Trigger.Category.Investigatable.InvestigatableID}` and
     `status: closed`.
   - *Annotate:* call **Add comment to detection** with the same `investigatable_id`, recording
     why it was resolved (e.g. "user changed password shortly before detection triggered"). Swap
     the close for a notification action if you want a human to review instead of auto-closing.
6. **Validate, then deploy.** Run `validate.py`, then import and release to the CID.

## Key Actions

The `id` and `version_constraint` values below are taken directly from the source example YAML.

| Action | `id` | version_constraint |
|--------|------|--------------------|
| Get user identity context | `19c7e2af0a24f468be7797fe180c8329` | `~1` |
| Set detection status | `beb56cc40d334583671ca91e6e390056` | `~0` |
| Add comment to detection | `7b77cb5d5ff2651cc51c7c4c610d54d1` | `~0` |

**Note:** the "Set detection status" action omits `version_constraint` in the example YAML (the
platform defaults it), but the correct explicit value is `~0` per the action catalog via
`action_search.py --details`.

The trigger's Signal event value (`Investigatable/IDP`) and all `${Trigger.Category.Investigatable...}`
references are taken from the example; keep them exact, since they bind to the Identity Detection
signal's data shape.

## When to Route Elsewhere

Keep identity triage in the workflow when each step feeds the next directly (context → condition
→ status). Build a Foundry function (route to foundry-skills) when the decision logic is complex,
reused across many workflows, needs custom result transformation in code, or is paired with a UI
or collection.
