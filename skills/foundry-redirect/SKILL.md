---
name: foundry-redirect
description: >
  TRIGGER when the user asks to "build a Foundry app", "create a Foundry app",
  mentions manifest.yml, or needs a UI page/extension, serverless function,
  collection, or a custom API integration from a third-party API (Okta, ServiceNow,
  Jira, etc.) built. DO NOT TRIGGER for a standalone Fusion workflow that only wires
  together existing actions. This skill declines Foundry-app requests and points to
  the crowdstrike-falcon-foundry plugin, so the redirect works even without Claude
  Code hooks; it yields to the real Foundry plugin when that plugin is also installed.
version: 1.1.0
updated: 2026-08-19
tags: [fusion, foundry, redirect, routing]
author: CrowdStrike
license: MIT
compatibility: Claude Code >=1.0
metadata:
  category: routing
---

# Falcon Foundry Redirect

If this skill triggered, the request is a **Falcon Foundry app**, not a standalone
Falcon Fusion workflow. It belongs to the sibling Falcon Foundry plugin — the
`fusion-skills` plugin builds Fusion workflows only.

Why this skill exists: the `workflows` orchestrator declines Foundry-app requests too,
but its description matches *Fusion workflow* language, so a "build a Foundry app"
prompt never loads it. On Claude Code a hook covers that gap; on Codex, Copilot CLI,
Cursor, and the Agent SDK there are no hooks, so this skill — whose description matches
Foundry-app language directly — is what makes the redirect reachable.

## What to do

Do NOT author workflow YAML. Do NOT scaffold an app yourself. Respond with all three:

1. State plainly that this request needs a Falcon Foundry app, not a standalone Fusion workflow.
2. Name the plugin: **`crowdstrike-falcon-foundry`**.
3. How to install it: `/plugin install crowdstrike-falcon-foundry`, or clone https://github.com/CrowdStrike/foundry-skills.

## When both plugins are installed

If `crowdstrike-falcon-foundry` is present, its own `development-workflow` skill matches
Foundry-app requests directly and handles them — a stronger match than this one, so the
agent picks it and this redirect never fires. That is correct: this skill is the safety
net for when the Foundry plugin is absent, not a competitor with it when present.

## Foundry app vs. standalone workflow

| Signal in the request | Route |
|---|---|
| "Foundry app", `manifest.yml`, a UI page/extension, serverless function, collection, or custom third-party API integration | **Here** — redirect to foundry-skills |
| A trigger plus existing Fusion actions only (no UI, function, collection, or custom integration) | **`workflows`** — handle it as a standalone workflow |
| "a workflow inside a Foundry app" | **Here** — the app owns the workflow; Foundry scaffolds it |
| Fetch/summarize a population of alerts/detections the workflow doesn't already hold | **`workflows`** — a standalone CrowdStrike HTTP Request handles this without an app |
