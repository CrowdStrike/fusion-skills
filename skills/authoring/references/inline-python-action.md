# Python Script Action (`Inline.Python`)

Fusion has a native Python script action that runs user-written Python directly
in a workflow step (vendor: CrowdStrike, namespace `faas`, no `config_id`).

```yaml
run_script:
    id: <inline-python-action-id>     # via action_search.py
    class: Inline.Python
    version_constraint: ~1
    properties:
        runtime: py0313general
        script: |
            print("hello from Fusion")
    next:
        - print_result
```

- **Inputs:** `runtime` (REQUIRED — `py0313general`) and `script`; the script
  must `print(...)` its result to stdout.
- **Outputs:** `output_stdout`, `output_stderr`, `exit_code`, `error`. Read
  stdout downstream as `${data['run_script.output_stdout']}` — never `$(...)`.
  **Read stdout as a plain string; do NOT wrap it in `cs.json.decode(...)` and
  dot-index the result** (`${cs.json.decode(data['run_script.output_stdout']).field}`).
  That form does not resolve at release ("invalid or missing variable
  definitions"). If a downstream step needs structured fields, prefer reading the
  source directly (e.g. an Event Query's `results[0].Field`) over parsing Python
  stdout — see `event-query-action.md`.
- **`version_constraint: ~1`.**
- **Limits:** 60s execution, 256 MB memory, 50,000-char script, input+output
  ≤ 1024 KB, pre-installed packages only.
- **Regions:** US-1, US-2, EU-1 only (not US-GOV).

**Good fit — CEL++.** Reach for `Inline.Python` when you need transformation
logic CEL can't express: fetching and parsing an external feed, string
manipulation, or reshaping a payload. A representative example fetches the
abuse.ch SSL blocklist and cleans it into CSV for a lookup file (public data,
`requests` is pre-installed, no credentials):

```python
import csv, io, requests
r = requests.get('https://sslbl.abuse.ch/blacklist/sslblacklist.csv', verify=True)
clean_lines = []
for line in r.text.splitlines():
    if line.strip().startswith('# Listingdate'):
        clean_lines.append(line.lstrip('#').strip())   # promote header row
    elif line.strip().startswith('#'):
        continue                                        # drop comment lines
    else:
        clean_lines.append(line)
reader = csv.reader(io.StringIO("\n".join(clean_lines)))
for row in reader:
    print(",".join(row))                                # stdout -> downstream lookup file
```

Chain it to a lookup-file action to build a table you can `match()` against
(create-if-absent / overwrite by name). The complete, console-verified workflow is
`examples/tutorials/intro-python-sslbl-lookup.yaml` — `Create Python script` →
`Get lookup file metadata` → condition on whether the file exists → create or
overwrite it, reading `${data['CreatePythonScript.output_stdout']}` directly.

`Inline.Python` is a Fusion-workflow-only capability (not supported in Foundry).
