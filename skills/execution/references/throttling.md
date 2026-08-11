# Throttled workflow executions

When a workflow looks stuck — an action sitting "in progress" far longer than it
should — the cause is often **throttling**, not a failure. Falcon Fusion SOAR
deliberately paces an action when its execution volume exceeds a limit within a
time window: rather than failing, the action is queued and retried automatically
until it can proceed. This reference explains what you're seeing and when it
warrants action.

> Sourced from the CrowdStrike Fusion product team's throttling documentation.
> The console-visibility details (node state, metadata panel) describe the
> execution-detail UI; confirm the exact styling against your console, since UI
> can change.

## What throttling is

Throttling is **volume-based pacing**, applied per action:

- Each action has a limit on how many executions it can perform within a time
  window. Executions under the limit are never throttled; executions beyond it
  are queued and retried.
- **It retries automatically — no manual step.** A throttled activity is queued
  and retried after 30 seconds to 3 minutes. If throttled again on retry, it is
  re-queued. This repeats until the activity succeeds or **6 hours** have passed
  since the first attempt.
- **After 6 hours it times out.** An activity still being throttled 6 hours after
  its first attempt is timed out instead of retried forever. A timed-out activity
  **does** require a manual retry.
- **Scope is per action, across the CID** — shared across all workflows in the
  tenant, not isolated to one workflow. Available volume refreshes on a rolling
  basis (currently ~every 30 seconds), so instances of the same action are not
  all throttled at once; some always proceed within each window.

Throttling is **not an error** and does not mean a workflow is broken. It is the
platform staying within a volume limit.

## Recognizing it in the console

Throttling is surfaced on the **execution detail** view (open an execution from
the execution log), not the execution list:

- **On the execution graph**, a throttled action's node is visually distinct from
  a normal "in progress" node: an amber/yellow outline, a clock icon in place of
  the usual status icon, and a **"Throttled"** label.
- **In the action detail panel** (left side, always open), the action's status
  reads **"Throttled"** with a clock icon, and a throttle metadata block appears:

  | Field | Meaning |
  |-------|---------|
  | Throttled start time | When this action first began being throttled |
  | Rate limit | The volume limit being applied (e.g. `4/min`) |
  | Throttled count | How many times it has been queued/retried so far |
  | Last throttled time | Most recent time it was throttled |

  *Throttled start time* and *Rate limit* appear as soon as throttling begins.
  *Throttled count* and *Last throttled time* may read **"— updates on
  completion"** while the action is still running — they finalize once it stops
  retrying, and are not expected to update live. The metadata block stays visible
  after the action completes, as a record of why the run took longer.

"Throttled" is a **temporary, non-terminal state** like "in progress" — the
action is still active and keeps retrying. Only the 6-hour timeout makes it stop.

### Execution-level status

If any action in an execution is currently throttled, the **overall execution
status** reflects that, so a throttled run is identifiable from the execution log
rather than looking merely slow. When an execution mixes outcomes, the
execution-level status is chosen by priority: **failure > throttle > in
progress** — a failure needs attention, and a throttle explains a delay.

## When to act

- **Infrequent throttling is expected** and not a concern — a normal spike in
  execution volume. No action needed; it clears itself.
- **Sustained, continuous throttling** over a long period is more likely a
  workflow-design signal: the workflow is generating more volume than intended —
  a trigger firing too fast, or a loop without pacing. Consider:
  - spreading out execution timing,
  - batching calls,
  - using a slower trigger,
  - splitting the workflow,
  - or, where you control the pacing deliberately, a **Rate Limit** action
    (see `../authoring/references/deduplicate-ratelimit.md`) to smooth volume at
    the source.

## Debugging checklist

1. A workflow "stuck" in progress? Open the execution detail and check whether any
   node shows the **Throttled** state before assuming a hang or failure.
2. Read the throttle metadata block (rate limit, throttled count) to see how hard
   the action is being paced.
3. One-off throttling → let it retry; it resolves automatically.
4. Repeated throttling across many runs → treat it as a design issue and pace the
   source (see "When to act").
5. An activity that hit the **6-hour timeout** needs a **manual retry** — it will
   not resume on its own.
