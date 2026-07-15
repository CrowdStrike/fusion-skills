---
name: notifications
description: Send a workflow notification to a chat channel (Slack in the example) so teams get real-time alerts on a new detection, incident, or task update
source: https://www.reddit.com/r/crowdstrike/comments/1oq6xu9/cool_workflow_thursday_ngsiem_correlation_rule/
example: skills/authoring/examples/notifications/slack-send-message-to-channel.yaml
skills: [authoring, deployment]
capabilities: [workflow, notification]
---

## When to Use

User wants a workflow to post a message to a chat channel — notifying a team about a new alert,
incident, or task, or escalating during an active investigation. This is grounded in the Content
Library playbook `skills/authoring/examples/notifications/slack-send-message-to-channel.yaml`, which
takes a channel and message text on demand and posts to Slack. It's the simplest notification shape
and fills the otherwise-empty notifications category.

## Pattern

1. **Trigger.** The example uses an On demand trigger with a nested `json` object parameter holding
   `channel` (Slack channel ID or name, e.g. `#security-alerts`) and `text` (the message body); both
   are required. On demand lets a person run it or another workflow call it with the message details.
2. **Send the message.** Call `Slack v2 - Chat PostMessage`. Inputs map the trigger values through:
   `channel: ${data['json.channel']}` and `text: ${data['json.text']}` (the example leaves
   `markdown_text` empty).
3. **Reference upstream data (in a real workflow).** Instead of a manual `text` parameter, build the
   message from earlier steps — a detection field, an enrichment verdict — via `${...}` references.
4. **Validate, then deploy.** Run `validate.py`, then import and release to the CID.

## Key Actions

The Slack action ID and constraint are copied directly from the example YAML.

| Action | `id` | version_constraint |
|--------|------|--------------------|
| Slack v2 - Chat PostMessage | `184239ceb8a046cfb159228abd7cc67e` | `~0` |

The Slack action references a Slack connection/credential config created in the console and specific
to the CID. Discover it or ask the user; never invent one.

## Variation: chat platforms without a native action

When there's no native plugin action for the target chat platform, send the notification with a Cloud
HTTP Request (`Inline.HTTPRequest`) instead. The "password compromise" Workflow Wednesday post uses
this to post to **Google Chat** — a POST to a Google Chat webhook with a `cardsV2` JSON payload for a
formatted card. Same intent as the Slack example, delivered over HTTP; see the `http-actions`
use-case for the request/credential-config details.

## When to Route Elsewhere

Keep notifications in the workflow — this is a terminal action, not something that needs a Foundry app.
If the notification requires a custom UI or a reusable, multi-operation integration shared across many
workflows, that's the point to consider a Foundry API integration (route to foundry-skills).
