# Locks in Fusion Workflows

Locks let your workflows coordinate with each other. Use them to make sure only one workflow (or one execution) does something at a time — preventing duplicate processing, protecting a shared resource, or implementing a "only run one at a time" guard.

There are four lock activities you can drop into a workflow:

| Activity | What it does |
|----------|--------------|
| **Acquire Lock** | Try to take ownership of a named lock |
| **Release Lock** | Give up a lock you hold |
| **View Lock** | Check the current state of one lock |
| **View All Locks** | List every lock in a scope and see quota usage |

---

## The one thing to understand first

> ### 🔑 Acquire Lock is a *try-lock*, not a *wait-lock*
>
> When you run **Acquire Lock** and someone else already holds the lock, you get back `acquired: false` **immediately** — the activity does **not** pause and wait for the lock to free up.
>
> This is different from most "locks" you may have seen. It's your job to decide what happens when `acquired` is `false`: skip the work, take a different path, or loop back and try again later using a Timer.

Every workflow that uses Acquire Lock **must** branch on the `acquired` result. If you ignore it and just proceed, you've defeated the purpose of the lock.

---

## Concepts

### Scopes — who else can see this lock

Every lock lives in a **scope**, which decides which executions are competing for it.

| Scope | Shared across | Auto-released? |
|-------|---------------|----------------|
| **Execution** | All activities within a single workflow run | ✅ Yes — when the execution finishes |
| **Definition** | All runs of the *same* workflow | ❌ No |
| **CID** | All runs of *every* workflow in your customer account | ❌ No |

**Which scope should I use?**

1. **Do you only need to coordinate steps inside a single run of one workflow?**
   → Use **Execution** scope. (It also cleans itself up automatically.)
2. **Do you need to stop multiple runs of *the same* workflow from clashing?** (e.g. don't process the same record twice)
   → Use **Definition** scope. **This is the right default for most cases.** A key named `daily-sync` in one workflow won't collide with `daily-sync` in another.
3. **Do you need a single guard shared across *different* workflows in your account?** (e.g. one global maintenance job)
   → Use **CID** scope.

> ⚠️ Don't reach for CID scope by default. It's the broadest, and it means unrelated workflows sharing a key name will contend with each other — often not what you want.

#### Execution scope spans sub-workflows

Execution scope covers the *entire* execution tree. If your workflow calls another workflow as an activity (or runs a loop/submodel), the parent and child **share the same execution-scoped locks**. That's useful for passing a coordination signal from a parent workflow into a child — but be aware a child's "local" lock can collide with the parent's if they use the same key.

### Keys — which resource you're locking

The **key** names the specific thing being locked, within its scope.

- 1–150 characters
- Letters, numbers, underscores, and hyphens only (`a-z`, `A-Z`, `0-9`, `_`, `-`)
- Examples: `daily-report`, `process_record_42`, `isolate-host-abc123`

Include the identifier of what you're protecting in the key — e.g. `process-{{host_id}}` so each host gets its own lock.

### Value — proof of ownership

The **value** you store when acquiring a lock is an **ownership token**. When you go to release the lock, the value you pass must match — this stops one execution from accidentally releasing a lock that another execution holds. (See [Release Lock](#release-lock) for how the match works and how `force` bypasses it.)

- 1–1024 characters
- A good default is the execution ID, since it's unique and always available.

### TTL — the safety net

TTL (time-to-live) is how many seconds the lock lives before it automatically expires. Here's the mental model:

- **TTL is a safety net, not your primary release mechanism.** Its main job is to make sure a crashed or hung workflow doesn't hold a lock forever. **Always add a Release Lock step** when your work is done rather than relying on the TTL.
- **Expiry does not wait for your work to finish.** If you set `ttl: 300` but your protected work takes 400 seconds, the lock expires mid-way and another execution can grab it — silently breaking the mutual exclusion you thought you had.
- **There's no renewal/heartbeat.** You can't extend a lock you already hold. So set the TTL comfortably longer than your worst-case run time.
- **`ttl: 0` means "never expires"** for CID and Definition scopes. For Execution scope, `0` (and the maximum) is capped at 91 days — but execution locks auto-release when the run ends anyway.

### Quota — how many locks you can hold

Your account has a cap on the total number of active locks across all scopes (10,000 by default). If you hit it, Acquire Lock fails with a quota error. Use **View All Locks** to check `quota.remaining`.

---

## Results vs. errors

It's important to distinguish two different outcomes, because they flow down different paths in your workflow:

- **A negative result is still success.** `acquired: false` (lock was held) and `released: false` (nothing to release) are *normal results*. The activity succeeded and continues down the **normal path** — you branch on the boolean yourself.
- **An error is a failure.** Bad inputs (invalid scope, illegal key characters, TTL over the max) and infrastructure problems (quota exceeded, connectivity) cause the activity to **fail** and take the **error path**.
  - Input validation errors are permanent — retrying won't help. Fix the configuration.
  - Quota and connectivity errors are transient — the platform may retry them automatically.

Design your workflow so the `acquired`/`released` branch handles the "held by someone else" case, and the error path handles genuine failures.

---

## Activities

### Acquire Lock

Tries to take a lock. Returns immediately whether or not it succeeded (see the [try-lock note](#the-one-thing-to-understand-first)).

**Inputs**

| Field | Required | Description |
|-------|----------|-------------|
| `scope` | ✅ | `execution`, `definition`, or `cid` |
| `key` | ✅ | Lock name (1–150 chars; letters, numbers, `_`, `-`) |
| `value` | ✅ | Ownership token (1–1024 chars); the execution ID is a good default |
| `ttl` | ✅ | Seconds until auto-expiry. `0` = never expires (capped at 91 days for execution scope) |

**Output**

| Field | Description |
|-------|-------------|
| `acquired` | `true` if you got the lock; `false` if someone else already holds it |

---

### Release Lock

Gives up a lock. By default the `value` must match the current holder, so you only release your own lock.

**Inputs**

| Field | Required | Description |
|-------|----------|-------------|
| `scope` | ✅ | Must match the scope used to acquire |
| `key` | ✅ | The lock to release |
| `value` | ✅ | Must match the value the lock was acquired with |
| `force` | ❌ | If `true`, release regardless of value. Default `false` |

**Output**

| Field | Description |
|-------|-------------|
| `released` | `true` if the lock was released |
| `current_value` | Only present when release fails on a value mismatch — shows who actually holds it |

- If the value doesn't match: `released: false` and `current_value` tells you the real holder.
- If the lock doesn't exist: `released: false` (not an error).
- `force: true` deletes the lock no matter who holds it — use with care.

---

### View Lock

Inspects a single lock without changing it.

**Inputs**

| Field | Required | Description |
|-------|----------|-------------|
| `scope` | ✅ | The scope to look in |
| `key` | ✅ | The lock to inspect |

**Output**

| Field | Description |
|-------|-------------|
| `exists` | Whether the lock currently exists |
| `value` | Its current value (only when it exists) |
| `ttl_seconds` | Seconds left before expiry; `0` = never expires (only when it exists) |
| `expires_at` | Estimated expiry timestamp, RFC 3339 (only when it exists) |

---

### View All Locks

Lists every lock in a scope and reports quota usage.

**Inputs**

| Field | Required | Description |
|-------|----------|-------------|
| `scope` | ✅ | Which scope's locks to list |

**Output**

| Field | Description |
|-------|-------------|
| `locks[]` | Each lock's `key`, `ttl_seconds`, and `expires_at` |
| `meta.total` | How many locks are in this scope |
| `quota.limit` | Your account-wide lock limit |
| `quota.used` | Active locks across **all** scopes for your account |
| `quota.remaining` | How many more you can acquire |

> Note: `quota.used` counts locks across *every* scope for your account, not just the one you're listing — because the quota is enforced account-wide.

---

## Common patterns

Each pattern below shows what to do on **both** branches of the `acquired` result — because you always have to handle the "already held" case.

### Prevent duplicate processing (Definition scope)

Stop two runs of the same workflow from handling the same item at once:

```
Acquire Lock  scope=definition  key="process-{{item_id}}"  value="{{execution_id}}"  ttl=300
├─ acquired = true  →  do the work  →  Release Lock (same scope/key/value)
└─ acquired = false →  another run has it — skip, or end the workflow
```

### Global singleton (CID scope)

Ensure only one workflow across your whole account runs a periodic task:

```
Acquire Lock  scope=cid  key="nightly-report"  value="{{execution_id}}"  ttl=3600
├─ acquired = true  →  generate the report  →  Release Lock
└─ acquired = false →  someone else is already running it — exit
```

### Wait and retry

If you'd rather wait for a lock instead of skipping, loop with a Timer:

```
Acquire Lock
├─ acquired = true  →  proceed
└─ acquired = false →  Timer (wait N seconds)  →  loop back to Acquire Lock
```

Cap the number of retries so you don't loop forever.

### Check capacity before a bulk job

```
View All Locks  scope=cid
└─ if quota.remaining is too low  →  alert / back off
   otherwise                      →  proceed
```

> **Passing data between steps?** Locks are for *coordination*, not for carrying data. Don't stash business data in a lock's `value` to read it elsewhere — there's no wait/notify and you'd fight the ownership-match rule on release. Use workflow variables (or a datastore activity) for moving data between steps.

---

## Limits at a glance

| Constraint | Value |
|------------|-------|
| Key length | 1–150 characters |
| Key characters | letters, numbers, `_`, `-` |
| Value length | 1–1024 characters |
| Max TTL (execution scope) | 91 days |
| Max TTL (definition / CID scope) | none (`0` = never expires) |
| Locks per account | 10,000 (default) |
| Acquire behavior | non-blocking — returns immediately |

---

## Cleanup

- **Execution-scoped locks** are released automatically when the run finishes.
- **Definition- and CID-scoped locks** stay until you release them or their TTL expires — always add a Release Lock step.
- Deleting a workflow removes all of that workflow's definition-scoped locks.
- All locks are cleared if an account is offboarded.

---

## Discovering the action IDs

The four lock activities are native CrowdStrike actions. Resolve each one's
32-character hex `id` and `version_constraint` with `action_search.py` before
writing YAML — do not hardcode an ID from memory:

```bash
action_search.py --search "lock" --details
```

Match on the activity names above (Acquire Lock, Release Lock, View Lock, View
All Locks) and copy the real `id` plus `~<major>` of the `semantic_version` each
returns.
