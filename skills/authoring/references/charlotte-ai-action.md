# Charlotte AI — LLM Completion action

`Charlotte AI - LLM Completion` runs a prompt through an LLM inside a workflow
step — use it to summarize enrichment, classify, or extract structured fields.
Unlike the `Inline.*` actions, this is a **plugin/vendor action**: it has **no
`class:` field** and is referenced by `id:` only. This is exactly the case that
trips up authoring, so note the three gotchas: no `class`,
`version_constraint: ~0`, and the output must be decoded with `cs.json.decode()`.

**Discover the ID first** — it is a real, cross-cloud-stable value, but confirm
it in your CID rather than pasting blind:

```bash
python skills/authoring/scripts/action_search.py --search "llm"
```

```yaml
summarize:
    id: bdfecafafdb44919a458fcf51d6b93a7_98dec86072334d24b37dd798098cfd63
    version_constraint: ~0
    properties:
        user_prompt: "Summarize these enrichment results:\n${data['enrich.completion']}"
        model_name: "Claude Latest"
        temperature: 0.1
        # json_schema: '{...}'   # optional — forces structured JSON output
    next:
        - use_summary
```

- **Action ID:** `bdfecafafdb44919a458fcf51d6b93a7_98dec86072334d24b37dd798098cfd63`
  — a **compound** plugin ID (`<plugin-prefix>_<action>`). The first half is the
  shared Charlotte AI plugin prefix; the second identifies LLM Completion.
- **`version_constraint: ~0`.** This action's `semantic_version` is `0.0.100`,
  and the constraint is the tilde range for its major component — major 0 gives
  `~0`, not `~1`. Do not assume a sophisticated action means `~1`; the rule is
  `~<major of semantic_version>`, defaulting to `~0` when there is none. Charlotte
  AI is the textbook `~0` case.
- **No `class:` field.** Vendor/plugin actions are referenced by `id:` only
  (contrast the `Inline.*` actions, which set `class:`).
- **Inputs (`properties`):** `user_prompt` (the prompt; supports `${data[...]}`
  interpolation), `model_name` (e.g. `"Claude Latest"`), `temperature`
  (e.g. `0.1`), and optional `json_schema` (forces structured JSON output).
- **Output — decode it.** The `completion` output is a JSON **string**, so wrap
  it in `cs.json.decode()` to read fields:
  ```
  ${cs.json.decode(data['<NodeName>.FaaS.nlpassistantapi.llminvocator_handler.completion']).field_name}
  ```
  The namespace is `FaaS.nlpassistantapi.llminvocator_handler`; the output field
  is `completion`. Replace `<NodeName>` with the action's YAML key. **`FaaS` is
  PascalCase and case-sensitive** — release validation rejects `faas` with
  "field names are case-sensitive" (confirmed live).

> **Action IDs are stable across the commercial clouds (us-1/us-2/eu-1)**, so the
> ID above is a real starting point — but always confirm with
> `action_search.py --search "llm"`, especially for plugin/compound IDs, and note
> GovCloud may differ.
