# Console verification & render-testing

How to verify a deployed workflow in the Falcon console — used to confirm a
workflow both imports cleanly (API) *and* renders in the visual editor (canvas).
Import-clean is a weaker bar than render-clean: a workflow can pass
`import_definition` yet crash the console's graph renderer. This is how you catch
that, and how to navigate the console reliably to do it.

## Navigating the console

- **Use the menu, not guessed URLs.** Direct navigation to an authenticated page
  (e.g. pasting a workflow-editor URL) often bounces to `/login` because of a
  redirect/session-timing race. Land on an already-authenticated page first,
  open the main **Menu**, then click through.
- **The CID workflow list is Fusion SOAR > Workflows**, which lives at
  `/workflow/fusion` (singular `workflow`). This lists the workflows deployed in
  your CID.
- **Open a workflow's editor by clicking it from that list** — not by
  constructing a `/workflows/definitions/editor/<id>` URL, which 404s. The
  detail/editor view renders the workflow graph on a canvas.

## Render-testing a workflow

1. Deploy it via `import_workflows.py` (capture the definition ID from the
   `Imported — ID: <hex>` line).
2. From Fusion SOAR > Workflows, click the workflow to open its editor.
3. Watch the browser console while the canvas draws. A render failure shows up as
   an error like:

   ```text
   Can not create edge X with nonexistant source Y
   ```

   followed by a blank/partial canvas (0 nodes drawn, Test/Save/Publish locked).
   A clean render draws the full graph with **zero** console errors.
4. Delete the test workflow afterward with `delete_workflow.py --id <hex>` (see
   the deploy skill) so it doesn't linger in the CID.

The crash above is caused by referencing a node the canvas can't build — most
often a synthetic gateway pass-through. See `workflows/references/yaml-schema.md`
("Parallel fan-out") for the render-safe shape: fan out by listing targets
directly in `next:`, never via `default_parallel_*`/`default: true`
pass-throughs.

## What needs the browser vs. what the API does

`verify-workflows.sh` Phase 2 uses the browser only for the two things the API
cannot do, and uses the API for everything deterministic:

| Task | Browser or API | Why |
|------|----------------|-----|
| Render-test (Signal/Scheduled/SubModel) | **Browser** | No API reports "did the canvas draw" — open the editor and watch the console |
| Configure an HTTP-action credential | **Browser** | Fusion has no API to create an HTTP-action credential |
| Execute an On-demand workflow | **API** | `trigger_workflow.py --wait` triggers it |
| Determine success | **API** | `get_execution_results.py` checks `status == succeeded` — no UI eyeballing |

So a credential-gated On-demand workflow is handled in two steps: the browser
configures the VirusTotal credential and publishes it, then the harness executes
it over the API and checks the result. Credential-free On-demand workflows skip
the browser entirely — the API runs them directly. Signal-type workflows only
render-test; they fire on real events and cannot be triggered here.

## The Content Library catalog API is console-only

The workflow examples are grounded in real CrowdStrike Content Library playbooks.
Those catalog records (the graph model the converter consumes) come from a
console-only reverse-proxy route, **not** the public API:

- Query IDs: `GET /api2/content-library/queries/content/v1?filter=content_type:'fusion_playbook'`
- Fetch records: `GET /api2/content-library/entities/content/v1?ids=<id>&ids=<id>`

Both require two headers the console sets on its own requests:

- `x-csrf-token` — reuse the value from any in-page request (capture it from the
  network panel or a prior fetch)
- `x-cs-app-name: falcon-content-library`

The **public API host** (`api.<cloud>.crowdstrike.com`) returns **404** for
`content-library` — Foundry CLI / FalconPy OAuth credentials do not reach it.
Fetching catalog records requires an authenticated browser session against the
console gateway (`falcon.<cloud>.crowdstrike.com/api2/...`).
