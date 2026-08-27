# Deduplicate & Rate Limit actions

Two families of built-in coordination actions that give workflows shared,
distributed state. **Deduplicate** answers "have I already handled this?"
(atomic once-only claim on a key). **Rate Limit** answers "am I going too fast?"
(pace a workflow against a shared budget). Both are inline actions — no Foundry
app, no `config_id`, no credential.

> **Availability.** These actions are enabled in all commercial CIDs, available
> in US-1, US-2, and EU-1 by default (other environments by request). As always,
> resolve the real action IDs and `version_constraint` values from your own tenant
> with `action_search.py --search "deduplicate"` / `"rate limit"` rather than
> hardcoding — the IDs in the worked example
> (`examples/tutorials/intro-deduplicate-third-party-detections.yaml`) were
> confirmed against a live tenant.

## The scope gotcha (read this first)

The console labels the two scopes **Workflow** and **CID**. The value the
platform stores in YAML is **not** `workflow` — it is `definition`:

```yaml
properties:
    scope: definition   # console label: "Workflow" (the default, per-definition)
    # scope: cid        # console label: "CID" (tenant-wide, shared across workflows)
```

Author `scope: definition` for per-workflow suppression (the default and almost
always what you want) and `scope: cid` only when several different workflows must
share one window. Writing `scope: workflow` from the console label does not match
what the platform accepts.

## Keys (both families)

The key is the identity of the thing you are coordinating on. Same rules for
Deduplicate and Rate Limit:

- Required, at most **150 characters**.
- Allowed characters: letters, digits, `_`, `-`. No dots, colons, spaces,
  slashes, or `@`.
- Both rules apply to the key's **final value after CEL expressions are
  evaluated**, not to what you type. A 200-char expression that resolves to a
  40-char string is fine.
- **Validation happens at execution, not save.** A key that resolves to something
  too long or with bad characters saves cleanly and fails at run time.

Because of the character rule, hash any key built from multiple fields or from
values you do not control:

```yaml
key: |-
    ${cs.hash.sha1(data['Trigger.Detection.ThirdParty.DetectionType'] +
    data['Trigger.Detection.ThirdParty.SourceIPs'].join(",") +
    data['Trigger.Detection.ThirdParty.DestinationIPs'].join(","))}
```

`cs.hash.sha1` turns any input into a fixed-length string that always satisfies
both rules. This is the recommended approach for composite keys.

---

## Deduplicate family

A "have I already handled this?" primitive. **Deduplicate** is atomic: if two
executions race on the same key, exactly one is told it is the original
(`duplicate: false`) and every other is told it is a duplicate (`duplicate:
true`). There is no window where both think they are first.

Six actions:

| Action | What it does |
|--------|--------------|
| **Deduplicate** | Claims a key for `period` seconds. Tells you whether it was already claimed. |
| **View Deduplicate Entry** | Checks whether a key is claimed, without claiming it. |
| **Delete Deduplicate Entry** | Releases a key early, before its period expires. |
| **View All Deduplicate Entries** | Lists every entry in a scope, plus quota usage. |
| **Set Deduplicate Entry Metadata** | Attaches a note (e.g. a case ID) to an existing entry. |
| **Wait for Deduplicate Entry Metadata** | Waits for another execution to attach that note. |

### Deduplicate

Attempts to claim `scope` + `key` for `period` seconds. If nothing held the key,
this execution claims it and `duplicate` is `false`. If the key was already held,
nothing changes (the original period is **not** extended) and `duplicate` is
`true`.

Inputs: `scope` (required, `definition`/`cid`, default `definition`), `key`
(required), `period` (required, seconds; `0` = never expires).

Outputs: `key` (evaluated value — reference this downstream instead of repeating
the expression), `duplicate`, `originating_execution_id`, `metadata` (only when
`duplicate: true` and metadata was set), `period_remaining`, `expires_at`.

**How to use it:** put Deduplicate right after the trigger, then branch on
`duplicate`:

- `duplicate == false` → do the real work (create the case, page the analyst).
- `duplicate == true` → end, or take a cheap path like commenting on the
  existing case.

The period is fixed at creation — repeated hits do not slide the window forward,
so the entry always expires a set time after the *first* event. Common periods:
`3600` (one per hour), `86400` (one per day), `0` (once forever — consumes quota
until deleted).

### View / Delete / View All

- **View Deduplicate Entry** — reads state without claiming. Returns `exists`,
  and when it exists `metadata` / `period_remaining` / `expires_at`. Viewing a
  missing entry is not an error (`exists: false`).
- **Delete Deduplicate Entry** — removes an entry early so the key can be claimed
  again. Returns `deleted`. Use to re-arm after a case is resolved, to undo on
  failure (so the next event retries instead of being suppressed), or to free
  quota held by `period: 0` entries.
- **View All Deduplicate Entries** — lists `{key, period_remaining, expires_at}`
  per entry plus `quota.limit`/`used`/`remaining`. Does **not** include metadata;
  use View for a specific key to read that.

> **View is not race-safe.** "View, then act" has a gap between the check and the
> work. To do something once, use **Deduplicate** and branch on `duplicate` —
> that is the atomic path. View is for reporting and diagnostics.

### Set / Wait for Metadata

The classic pattern: the original execution claims `detection_123`, creates case
`CS-4711`, and records `CS-4711` as the entry's metadata. Every suppressed
duplicate can then say *which* case already covers it.

- **Set Deduplicate Entry Metadata** — attaches a note (1–250 chars) to an
  existing entry. **It only updates; it never creates** and never changes expiry.
  Place it after a Deduplicate on the non-duplicate branch; if the entry expired
  or was never claimed, `updated` is `false` and the note is silently dropped.
- **Wait for Deduplicate Entry Metadata** — pauses the **duplicate** branch until
  the original attaches metadata, then continues with it. Place it after a
  condition on `duplicate` (not directly wired to Deduplicate — see validation
  below). Waits up to **30 minutes**, and ends early if the entry expires first.
  Branch on whether `metadata` is present to tell success from every other
  outcome; `existed_during_wait` distinguishes "expired while waiting" from "never
  existed" (usually a key mismatch).

---

## Rate Limit family

Pace a workflow against a shared, named budget so it never exceeds what a
downstream system accepts. The distinguishing feature: **a Rate Limit action can
pause the workflow.** By default it waits until a slot is free, then continues.

Four actions:

| Action | What it does |
|--------|--------------|
| **Rate Limit** | Requests one slot. Proceeds now, waits if a slot frees within `max_wait`, or sheds. |
| **View Rate Limit** | Reports config, current headroom, and time to next slot. Consumes nothing. |
| **Delete Rate Limit** | Removes a limiter, resetting its state. |
| **View All Rate Limits** | Lists every limiter in a scope, plus quota usage. |

### Rate Limit

Inputs: `scope` (required, `definition`/`cid`), `key` (required), `limit`
(required, positive integer), `every` (required window, default `1m`),
`tolerance` (`smooth` default / `bursty`), `max_wait` ("Wait Timeout", required).
`every` and `max_wait` accept `1s`–`24h`.

`limit` + `every` define the rate (`100` every `1m` = 100/min). Three outcomes:

1. **Admitted immediately** — a slot is free. `waited: 0`,
   `exceeded_wait_timeout: false`.
2. **Admitted after a wait** — a slot opens within `max_wait`; the workflow
   suspends and resumes. `waited` reports the block, `exceeded_wait_timeout:
   false`.
3. **Shed** — no slot within `max_wait`. Returns **immediately and successfully**
   with `exceeded_wait_timeout: true` and `retry_after` set to how long the wait
   *would* have been.

> **Shedding is a success, not an error.** If you do not branch on
> `exceeded_wait_timeout`, the workflow carries on and calls the downstream system
> anyway — defeating the rate limit. Always add a condition after a Rate Limit
> action.

**Tolerance:** `smooth` (default) paces evenly — a limit of N per window admits
one request every `window / N`, spreading bursts. `bursty` is a token bucket of
depth `limit` — up to `limit` go back-to-back, then one refills every `window /
N`.

**Scopes add up.** Three `definition`-scoped limiters of 10/min against the same
vendor permit 30/min in aggregate — three independent budgets. Only a `cid`-scoped
limiter keeps their combined rate under one budget. Scope the limiter the way the
thing you are protecting is shared: a per-tenant vendor quota wants `cid`; pacing
one workflow's own side effects wants `definition`.

### View / Delete / View All

- **View Rate Limit** — inspects one limiter without consuming a slot. Returns
  `exists`, and when it exists `limit` / `remaining` / `retry_after`. Not
  race-safe for "check then call"; use for diagnostics.
- **Delete Rate Limit** — removes a limiter and frees its quota slot. Resets
  pacing (grants a fresh burst), so use for housekeeping, not to skip the queue.
- **View All Rate Limits** — lists `{key, limit, remaining, retry_after}` per
  limiter plus `quota` fields.

---

## Save-time validation

The builder checks these when you save (keys compared as text, `definition` scope
only — CID entries are usually claimed by another workflow, and two expressions
that happen to resolve to the same key are not caught):

- **Deduplicate** — warns if two Deduplicate actions share scope + key but
  disagree on `period` (whichever runs second has its period ignored); errors if a
  metadata action has no Deduplicate upstream; warns if a Deduplicate is wired
  straight into Wait (with no `duplicate` condition between, the wait also runs on
  the non-duplicate branch and waits on itself for the full timeout).
- **Rate Limit** — warns if two Rate Limit actions share scope + key but disagree
  on `limit`, `every`, or `tolerance`. `max_wait` may legitimately differ and is
  excluded.

## Quotas & cleanup

Each CID may hold a bounded number of active entries/limiters across all scopes
(default **10,000** each). At the quota, calls that would create a *new*
entry/limiter fail; calls against existing ones keep working.

- Deduplicate entries expire when `period` elapses (`0` = never — delete
  explicitly). Deleting a workflow definition removes its entries.
- Rate limiters are self-cleaning: a limiter goes idle and expires ~5 minutes
  after its last reservation. Deleting a definition removes its limiters.
- Avoid unbounded key spaces (timestamps, random IDs) — they create a new
  entry/limiter every time and never coordinate anything.

## Worked example

`examples/tutorials/intro-deduplicate-third-party-detections.yaml` — an NG-SIEM
third-party (Palo Alto) detection is deduplicated on a sha1 key over a 24h window:
new detections create a case and record its ID as metadata; duplicates wait for
that metadata and comment on the original case.

This is the same pattern the CrowdStrike "Workflow Wednesday — Taming Noisy Alerts
with Deduplication" post walks through (25 phishing detections collapsed into one
case): a `cs.hash.sha1` key over Detection Name + Sender + Subject, Workflow scope,
an 86400-second (24h) period, branch on `duplicate`, `Set Deduplicate Entry
Metadata` to store the Case ID, and `Wait for Deduplicate Entry Metadata` on the
duplicate path to handle the create-vs-read race.
See <https://www.reddit.com/r/crowdstrike/comments/1vn01u6/20260812_workflow_wednesday_taming_noisy_alerts/>.
