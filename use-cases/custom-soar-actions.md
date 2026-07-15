---
name: custom-soar-actions
description: Use a custom or shared Falcon Foundry action inside a Falcon Fusion workflow to call a third-party API (e.g. list and deactivate Okta users), loop over the results, and write output to a log repository
source: https://www.crowdstrike.com/tech-hub/ng-siem/create-custom-actions-for-soar-with-falcon-foundry/
skills: [authoring, deployment, execution]
capabilities: [workflow, custom-action, api-integration]
---

## When to Use

User wants a Fusion workflow that drives a custom action exposed from a Falcon Foundry API
integration — for example, an Okta integration whose operations (list users, deactivate a user)
have been shared with SOAR. Use this for scheduled data ingestion into a log repository or an
on-demand response action that calls the external API.

## Pattern

The API integration and its shared operations are built in Foundry (create app, import the
OpenAPI spec or add operations manually, configure auth, mark each operation workflow-shareable,
then deploy, release, and install with credentials). That is Foundry work — route to
foundry-skills for the app side. Once an operation is shared and installed, build the workflow:

1. **Choose a trigger.** Scheduled (e.g. hourly) for periodic ingestion, or On demand for a
   manual response action.
2. **Add the shared action.** Search the workflow builder for the operation by name (e.g.
   `listUsers`) and select the credential profile (the installed API key config).
3. **Loop over the response.** Add a "For each" loop over the response body; set processing to
   sequential.
4. **Act per item.** Inside the loop, call the next operation (e.g. `deactivateUser`) —
   autocomplete resolves display fields like email to the API's user ID — or add a "Write to log
   repo" action with a custom JSON payload:

   ```json
   {
     "event": { "kind": "UserState", "provider": "Okta" },
     "user": { "email": "${Email instance}", "first_name": "${FirstName instance}" }
   }
   ```

5. **Validate, then deploy.** Run `validate.py`, then import and release to the CID. Verify
   ingestion in Next-Gen SIEM: `#repo = fusion | event.provider = Okta`.

## Key Actions

| Action | Type | Purpose |
|--------|------|---------|
| Custom API operation | Shared Foundry action | The integration operation (`listUsers`, `deactivateUser`); referenced by `id` with a `config_id` |
| For each | Iterator | Iterates the response body; sequential processing |
| Write to log repo | Built-in action | Persists a custom JSON event to a LogScale repository |

A custom action references a `config_id` (the installed credential profile) created in the
console and specific to the CID. Discover it or ask the user; never invent one.

## Common Pitfalls

- **Action not visible in the workflow builder:** the app must be released AND installed. Use
  Preview mode (the `</>` icon) during development to see pre-release actions.
- **Scheduled runs hit rate limits:** turn off or delete a scheduled workflow when done testing —
  hourly schedules trigger provider rate-limit warnings within days.

## When to Route Elsewhere

Building the API integration, sharing operations, and deploying the app is Foundry work — route
to foundry-skills. Stay here for the Fusion workflow that consumes an already-shared action.
