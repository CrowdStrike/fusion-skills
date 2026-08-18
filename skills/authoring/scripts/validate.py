"""
Validate CrowdStrike Fusion workflow YAML files.

Performs three levels of validation:
  1. Pre-flight: checks header comment, required top-level keys, PLACEHOLDER markers
  2. Structural: parses YAML and validates schema rules (action IDs, trigger types, etc.)
  3. API: dry-run import via POST /workflows/entities/definitions/import/v1?validate_only=true

Usage:
    python validate.py workflow.yaml                    # Validate one file
    python validate.py *.yaml                           # Validate multiple files
    python validate.py --preflight-only workflow.yaml   # Skip API call (runs pre-flight + structural)
"""

import argparse
import re
import sys
import os

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", "common", "scripts"))
import _bootstrap  # pylint: disable=wrong-import-position
_bootstrap.ensure_deps(__file__)  # re-exec via managed venv if deps are missing
from auth import get_client  # pylint: disable=wrong-import-position

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REQUIRED_KEYS = {"name", "trigger"}
PLACEHOLDER_PATTERN = re.compile(r"PLACEHOLDER_[A-Z_]+")
ACTION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}([_~][0-9a-f]{32})?$")
# A real credential config id (HTTP action definition_id) is exactly 32 hex
# chars, e.g. 7227ab386bd646c18b27716e8fff8d26. Anything else — an
# ALL_CAPS_UNDERSCORE token or a PLACEHOLDER_* — is a placeholder that imports
# as a broken reference.
CREDENTIAL_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
DATA_REF_PATTERN = re.compile(r"\$\{data\[")
VALID_TRIGGER_TYPES = {"On demand", "Signal", "Scheduled", "SubModel"}

# Wrong ways to reference runtime data that a weaker model reaches for. The
# correct forms are ${data['<node>.<field>']} and the null-safe
# ${data[?'<node>.<field>'].orValue(...)} (both documented in the skill). These
# patterns catch the clearly-wrong mistakes so they fail authoring instead of at
# release as an unresolved-variable error:
#   $trigger.x / $GetUser.out / $loop.item  -> bare $token (no ${data[...]})
#   $(data['x'])                            -> shell-style $(...) instead of ${...}
#   $action.output.body                     -> dotted $token.output.* literal
# NOTE: ${data[?'...']} (CEL optional chaining) is VALID and intentionally not
# flagged. Matches a leading '$' NOT followed by '{' (double-brace interpolation
# is the only valid shape). Reported as WARNING with the correct form.
BAD_DATA_REF_PATTERNS = [
    (re.compile(r"\$\(\s*data\["), "$(data['...']) uses shell-style $(...) — use ${data['...']}"),
    (re.compile(r"\$(?!\{)[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z0-9_.\[\]#'\"]+"),
     "a bare $token.field reference — wrap runtime data as ${data['<node>.<field>']}"),
]

# Release-time reference forms that pass import + local structural checks but the
# release validator rejects as "unknown variable" (confirmed live). Each matches
# inside a ${data['...']} reference. Keyed to a specific finding so the message
# names the correct replacement.
RELEASE_BAD_REF_PATTERNS = [
    (re.compile(r"data\['[^']*\.HTTP\.body\.[^']*'\]"),
     "an HTTP response reference with a '.HTTP.body.' prefix. Release rejects it "
     "as an unknown variable — use the direct path ${data['<Action>.<json.path>']} "
     "(no '.HTTP.body.'). See references/http-actions.md."),
    (re.compile(r"data\['[^']*\.events\.\d+\.[^']*'\]"),
     "an Event Query output using '.events.<n>.'. Release rejects it — the output "
     "is an array at .results, referenced as ${data['<Action>.results'][0].Field}. "
     "See references/event-query-action.md."),
    (re.compile(r"data\['[^']*\.faas\.nlpassistant[^']*'\]"),
     "a Charlotte AI reference with lowercase '.faas.'. Field names are "
     "case-sensitive; release rejects it — use '.FaaS.nlpassistantapi..."
     "'. See references/charlotte-ai-action.md."),
    (re.compile(r"cs\.json\.decode\(\s*data\['[^']*\.output_stdout'\]\s*\)"),
     "a cs.json.decode() wrapper around an Inline.Python 'output_stdout' "
     "reference. Release rejects this as 'invalid or missing variable "
     "definitions'. Read the source directly instead — e.g. an Event Query's "
     "${data['<Action>.results'][0].Field} — rather than parsing Python stdout. "
     "See references/inline-python-action.md and event-query-action.md."),
    (re.compile(r"data\['[^']*\.\d+\.[^']*'\]"),
     "a numeric array index inside the data['...'] quotes (e.g. "
     "'.results.0.field'). Release rejects it as 'not found' — the index goes "
     "OUTSIDE the brackets: ${data['<Action>.results'][0].field}, not "
     "'<Action>.results.0.field'. See references/event-query-action.md and "
     "http-actions.md."),
]

# Output namespaces that appear only in the LONG-form data reference
# ${data['<node>.<namespace>.<field>']}. When the producing action is PINNED
# (version_constraint set), the platform addresses the collapsed
# ${data['<node>.<field>']} output and release rejects the namespaced path as an
# unknown variable (confirmed live, PR #34). An UNPINNED action legitimately
# keeps the long form, so the guard fires only when the referenced node is
# pinned. Maps the namespace prefix to the collapsed replacement hint.
PINNED_COLLAPSE_NAMESPACES = {
    "device.query": "devices",
    "device.get_details": "<field>",
    "logscale.query_event": "results",
}

# Trigger fields that resolve on the base `event: Investigatable` (multi-product
# Detection) trigger but are REJECTED at release on the dedicated
# `event: Investigatable/NGSIEM` trigger with `unknown variable "..."` (confirmed
# live). On the NG-SIEM trigger the product is fixed, so use a static string.
NGSIEM_EVENT = "Investigatable/NGSIEM"
NGSIEM_REJECTED_TRIGGER_FIELDS = (
    "Trigger.Detection.Product",
    "Trigger.Detection.Description",
)

# MITRE ATT&CK fields that trigger discovery (search_triggers, surfaced by
# trigger_search.py --fields) advertises on the NG-SIEM Signal trigger, yet the
# release validator rejects them as `unknown variable "..."` (confirmed live).
# MITRE tactics/techniques are not on the NG-SIEM trigger payload at release
# time; source them from the hydrated detection instead.
NGSIEM_REJECTED_MITRE_FIELDS = (
    "Trigger.Detection.MitreAttack.Tactic",
    "Trigger.Detection.MitreAttack.Technique",
)

# The EPP-detection payload namespace. NG-SIEM detections (event:
# Investigatable/NGSIEM) do NOT expose Trigger.Category.Investigatable.* — that
# path resolves only on the base EPP trigger (event: Investigatable). Referencing
# it on the NG-SIEM trigger imports cleanly but release rejects every reference as
# an "unknown variable" (confirmed live). NG-SIEM uses Trigger.Detection.* instead.
NGSIEM_REJECTED_NAMESPACE = "Trigger.Category.Investigatable"

# NG-SIEM detection trigger fields that are multivalued (list(string)), confirmed
# against the search_triggers payload schema. Comparing one to a string literal
# (`!= ''` / `== ''`) is a CEL type error the release validator rejects with
# "found no matching overload for '_!=_' applied to '(list(string), string)'".
# Presence must be tested with `.size() > 0`; index an element with `[0]`. Import
# and structural checks pass, so this only surfaces at release without a guard.
NGSIEM_ARRAY_FIELDS = (
    "SourceIPs",
    "DestinationIPs",
    "SourceHosts",
    "DestinationHosts",
    "HostNames",
    "UserNames",
    "SourceProducts",
    "SourceVendors",
)

# Matches a CEL comparison of an NG-SIEM array field against a string literal:
#   data['...SourceIPs'] != ''   /   data['...UserNames'] == ""
# The field-name alternation is filled in at import time from NGSIEM_ARRAY_FIELDS.
_NGSIEM_ARRAY_STRING_CMP_RE = re.compile(
    r"data\[\s*['\"]Trigger\.Detection\.NGSIEM\.(?:"
    + "|".join(NGSIEM_ARRAY_FIELDS)
    + r")['\"]\s*\]\s*[=!]=\s*['\"]\s*['\"]"
)

# The Charlotte AI - LLM Completion action is a plugin action referenced by
# compound id only (no `class:` field), so it can't be recognised by class.
# Its id always begins with this Charlotte AI plugin prefix; the segment after
# the underscore identifies the LLM Completion operation.
CHARLOTTE_LLM_ID_PREFIX = "bdfecafafdb44919a458fcf51d6b93a7_"

# The built-in "Send email" action. It has no class and is referenced by id, so
# match on the id. Its recipient list property is `to`.
SEND_EMAIL_ID = "07413ef9ba7c47bf5a242799f59902cc"

# Fake recipient domains a model invents instead of asking for a real address.
# `yourcompany`/`yourorg`/`example`/`company` etc. look plausible but dead-end at
# runtime (Send email only delivers to Falcon users and CID-approved domains).
FAKE_EMAIL_DOMAIN_PATTERN = re.compile(
    r"@(?:your|my|the)?(?:company|companyname|org|organization|domain|email)"
    r"|@example\.(?:com|org|net)"
    r"|@(?:acme|contoso|foo|bar|test|sample|placeholder)\.",
    re.IGNORECASE,
)

# The only keys a Fusion workflow may have at the top level. `disconnected_nodes`
# and `output_fields` appear in real console exports; the rest are the documented
# schema. Any other top-level key means the model invented an off-schema shape
# (e.g. `nodes`/`edges`/`steps`, or bare action labels dumped at the root instead
# of nested under `actions:`). Such files import as an empty workflow and then
# fail release, yet slip past every check keyed on `data.get("actions")`.
ALLOWED_TOP_LEVEL_KEYS = {
    "name", "description", "trigger", "actions",
    "conditions", "loops", "output_fields", "disconnected_nodes",
}


def preflight_check(file_path):
    """
    Local checks before hitting the API. Returns list of warning/error strings.
    Empty list means all pre-flight checks passed.
    """
    issues = []

    if not os.path.isfile(file_path):
        return [f"File not found: {file_path}"]

    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()

    # Check header comment
    if not lines or not lines[0].startswith("#"):
        issues.append("WARNING: Missing header comment (first line should start with #)")

    # Check for required top-level keys (simple text scan — not a full YAML parser)
    for key in REQUIRED_KEYS:
        # Match key at start of line (top-level) followed by colon
        if not re.search(rf"^{key}\s*:", content, re.MULTILINE):
            issues.append(f"ERROR: Missing required top-level key '{key}'")

    # Check for PLACEHOLDER markers
    placeholders = PLACEHOLDER_PATTERN.findall(content)
    if placeholders:
        unique = sorted(set(placeholders))
        issues.append(f"ERROR: Found PLACEHOLDER markers that must be replaced: {', '.join(unique)}")

    # Flag an invalid trigger 'type' at pre-flight too (structural catches it, but
    # surfacing it in the first gate is cheaper feedback). A near-miss like
    # 'On_demand' (underscore) for 'On demand' is the common mistake. Parse the
    # YAML in a guarded block so a nested schema `type:` (e.g. type: string inside
    # trigger.parameters) is never mistaken for the trigger's own type; if the file
    # doesn't parse, skip silently and let structural_check report the parse error.
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError:
        parsed = None
    if isinstance(parsed, dict):
        trig = parsed.get("trigger")
        if isinstance(trig, dict):
            ttype = trig.get("type")
            if ttype and ttype not in VALID_TRIGGER_TYPES:
                issues.append(
                    f"ERROR: Invalid trigger type '{ttype}'. Must be one of: "
                    f"{', '.join(sorted(VALID_TRIGGER_TYPES))} (note the space in "
                    f"'On demand', not 'On_demand')."
                )

    return issues


def _collect_node_labels(data):
    """Collect all defined node labels from actions, loops, and conditions.

    Recurses through nested loops so a label defined inside a loop-within-a-loop
    is still counted — real Content Library playbooks nest sub-models several
    levels deep, and a shallow scan would flag their inner references as dangling.
    """
    labels = set()
    for key in ("actions", "conditions"):
        section = data.get(key, {})
        if isinstance(section, dict):
            labels.update(section.keys())
    loops = data.get("loops", {})
    if isinstance(loops, dict):
        for loop_name, loop_def in loops.items():
            labels.add(loop_name)
            if isinstance(loop_def, dict):
                labels.update(_collect_node_labels(loop_def))
    return labels


def _validate_action(label, action, issues):
    """Validate a single action dict."""
    if not isinstance(action, dict):
        return
    if "id" not in action:
        issues.append(f"ERROR: Action '{label}' missing required 'id' field")
    elif not ACTION_ID_PATTERN.match(str(action["id"])):
        issues.append(
            f"ERROR: Action '{label}' has invalid id '{action['id']}' "
            f"(must be 32-char hex, or a compound plugin id '<hex>_<hex>' / '<hex>~<hex>')"
        )
    elif len(set(str(action["id"]))) == 1:
        issues.append(
            f"ERROR: Action '{label}' has a fake id '{action['id']}' "
            f"(all-same-character). Run action_search.py to get the real ID."
        )
    if "name" not in action:
        issues.append(f"ERROR: Action '{label}' missing required 'name' field")
    if "class" in action and "version_constraint" not in action:
        issues.append(
            f"ERROR: Action '{label}' has 'class' but missing "
            f"'version_constraint' (add: version_constraint: ~1)"
        )
    _validate_action_properties(label, action, issues)


def _validate_action_properties(label, action, issues):
    """Check per-class required properties that the release validator enforces.

    These properties import cleanly but fail at release with errors like
    "HTTP Request actions missing required fields request_http_method and
    request_content_type". Catching them here turns a release-time failure —
    which forces an edit/re-import cycle — into a pre-deploy validation error.
    """
    properties = action.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    # Inline.HTTPRequest: the http_transaction block must set the method and
    # content type. Without them the release validator rejects the workflow.
    if action.get("class") == "Inline.HTTPRequest":
        transaction = properties.get("http_transaction")
        if not isinstance(transaction, dict):
            issues.append(
                f"ERROR: HTTP Request action '{label}' is missing its "
                f"'http_transaction' block under 'properties' (needs "
                f"request_http_method and request_content_type). See "
                f"references/http-actions.md."
            )
        else:
            for field in ("request_http_method", "request_content_type"):
                if not transaction.get(field):
                    issues.append(
                        f"ERROR: HTTP Request action '{label}' is missing "
                        f"required '{field}' in its http_transaction. Release "
                        f"validation rejects HTTP actions without it."
                    )

        # definition_id references a console credential config by its 32-char
        # hex id. It is OPTIONAL: the recommended shape is to author the HTTP
        # action WITHOUT it (credential-less) and configure auth in the console
        # after deploy. But when present it must be a real config id, not a
        # placeholder like VIRUSTOTAL_CREDENTIAL_CONFIG_ID — that placeholder
        # imports as a broken reference and dead-ends the user, and server-side
        # validation does not catch it.
        definition_id = properties.get("definition_id")
        if definition_id is not None and not CREDENTIAL_ID_PATTERN.match(
            str(definition_id)
        ):
            issues.append(
                f"ERROR: HTTP action '{label}' has a placeholder definition_id "
                f"'{definition_id}'. Author the HTTP action WITHOUT a "
                f"definition_id (leave authentication unset) and configure the "
                f"credential in the Falcon console after deploy; only set "
                f"definition_id to a real 32-char hex config id the user "
                f"provides."
            )

    # Charlotte AI - LLM Completion (compound id, no class): requires a prompt
    # and a model name. Referenced by id only, so match on the plugin prefix.
    if str(action.get("id", "")).startswith(CHARLOTTE_LLM_ID_PREFIX):
        for field in ("user_prompt", "model_name"):
            if not properties.get(field):
                issues.append(
                    f"ERROR: Charlotte AI LLM action '{label}' is missing "
                    f"required '{field}' property. Release validation rejects "
                    f"the LLM Completion action without it. See "
                    f"references/charlotte-ai-action.md."
                )

    # Send email: the recipient list must be non-empty and use the correct
    # field name `to`. An empty/missing `to:` — or the recipient placed under a
    # wrong key like `recipients:` — imports and passes server-side validation,
    # but the email reaches no one at runtime (the API ignores unknown keys), a
    # silent dead workflow. `to` is the only recipient field in the action's
    # catalog schema. Resolve the recipient (ask the user; org domain in CI).
    if str(action.get("id", "")) == SEND_EMAIL_ID:
        _validate_send_email(label, properties, issues)

    if action.get("class") == "Inline.QueryEvent":
        _validate_event_query_config(label, action, properties, issues)


def _validate_event_query_config(label, action, properties, issues):
    """Event Query release-config checks: correct field names in inline_configuration.

    The minimal shape `query`/`time_range`/`repo` passes import + structural
    checks but release rejects it ("Missing repo or view; Missing search start
    time; Missing search end time"). The release validator wants the full
    console-export config: `search_query`, `repo_or_view`, `start`/`end`. Confirmed
    live; matches the shipped close-duplicate-detections.yaml export.
    """
    config = action.get("inline_configuration", {})
    config = config.get("config", {}) if isinstance(config, dict) else {}
    # The wrong minimal shape puts query/time_range/repo directly under properties.
    if properties.get("time_range") or properties.get("repo") or properties.get("query"):
        issues.append(
            f"ERROR: Event Query action '{label}' uses the minimal "
            f"query/time_range/repo shape. Release rejects it — use the full "
            f"inline_configuration.config with search_query, repo_or_view, and "
            f"start/end, plus a top-level logscale_search_start_time. See "
            f"references/event-query-action.md."
        )
        return
    if isinstance(config, dict) and config:
        if config.get("repo") and not config.get("repo_or_view"):
            issues.append(
                f"ERROR: Event Query action '{label}' config uses 'repo:' — "
                f"release requires 'repo_or_view:'. See "
                f"references/event-query-action.md."
            )
        if config.get("time_range") and not (config.get("start") or config.get("end")):
            issues.append(
                f"ERROR: Event Query action '{label}' config uses 'time_range:' — "
                f"release requires 'start:' and 'end:'. See "
                f"references/event-query-action.md."
            )
        # A real Event Query config (has search_query or repo_or_view) must carry
        # BOTH start and end inside the config block. Setting only the top-level
        # logscale_search_start_time is not enough — release fails with "Missing
        # search start time" / "Missing search end time". Confirmed live.
        if config.get("search_query") or config.get("repo_or_view"):
            missing = [k for k in ("start", "end") if not config.get(k)]
            if missing:
                fields = " and ".join(f"'{m}:'" for m in missing)
                issues.append(
                    f"ERROR: Event Query action '{label}' config is missing "
                    f"{fields} inside inline_configuration.config. Release fails "
                    f"with \"Missing search start time\"/\"Missing search end "
                    f"time\" — the top-level logscale_search_start_time does not "
                    f"substitute for them. Add 'start:' (e.g. 7d) and 'end:' "
                    f"(e.g. now). See references/event-query-action.md."
                )
        _check_detection_hydration_join(label, config, issues)
        _check_alert_population_query(label, config, issues)


# Matches the detection-hydration anti-pattern: filtering the wrong event-store
# field 'Ngsiem.detection.id' against a query argument ('= ?arg'). The trigger
# hands a *composite* DetectionID that is stored in 'Ngsiem.alert.id'; querying
# 'Ngsiem.detection.id' (a different, short ID the trigger never provides)
# returns zero rows, so hydration silently yields nothing and every downstream
# enrichment gate falls through. Confirmed live (0 rows vs 3 rows). Optional
# whitespace around '=' and after '?'; the correct 'Ngsiem.alert.id' form does
# not match.
_DETECTION_ID_JOIN_RE = re.compile(r"Ngsiem\.detection\.id\s*=\s*\?\s*\w")


def _check_detection_hydration_join(label, config, issues):
    """Flag a hydration query joining on 'Ngsiem.detection.id' instead of 'alert.id'.

    A Signal trigger's composite ``DetectionID`` lives in the event store as
    ``Ngsiem.alert.id``. Matching it against ``Ngsiem.detection.id`` — the
    intuitive-but-wrong field — returns zero rows at runtime while passing
    import, structural, and release validation, producing a workflow that
    deploys cleanly and enriches nothing. Fires only on the ``= ?arg`` join
    form so the labelled ``# WRONG`` doc example and the correct ``alert.id``
    form are never flagged. See references/event-query-vs-api.md.
    """
    query = config.get("search_query")
    if isinstance(query, str) and _DETECTION_ID_JOIN_RE.search(query):
        issues.append(
            f"ERROR: Event Query action '{label}' hydrates a detection by "
            f"matching 'Ngsiem.detection.id = ?...'. The trigger never provides "
            f"that field. The trigger's composite DetectionID is stored in "
            f"'Ngsiem.alert.id' for all detection types — change the join field to "
            f"'Ngsiem.alert.id'. For correlation-rule detections that query returns "
            f"multiple records (the underlying events plus a correlation meta-event), "
            f"so filter it (e.g. '| xdr_type != correlation-rule-detection | "
            f"report_name != *') to the real events. As "
            f"written this query returns zero rows at runtime and the workflow "
            f"enriches nothing, despite releasing cleanly. See "
            f"references/event-query-vs-api.md."
        )


# Matches an Event Query that fetches a *population* of Falcon alerts/detections
# the workflow does not already hold — the connector-dependent trap. Two signals
# must BOTH be present for the query to look like a population fetch:
#   1. it targets an alert/detection repo or event type, and
#   2. it filters on severity (the "high-severity alerts" ask).
# A query that instead joins a held detection by argument
# (`Ngsiem.alert.id=?detectID`) is legitimate enrichment and is excluded, as are
# queries against ordinary NG-SIEM telemetry (failed logins, custom parsers) that
# name no alert repo.
_ALERT_POPULATION_REPO_RE = re.compile(
    r"xdr_indicatorsrepo|#repo\s*=\s*[\"']?(?:detections|alerts)|DetectionSummaryEvent",
    re.IGNORECASE,
)
_SEVERITY_FILTER_RE = re.compile(r"severity", re.IGNORECASE)
# A held-detection hydration join: `<field>=?arg` (the query already has the ID).
_HELD_DETECTION_JOIN_RE = re.compile(r"\.(alert|detection)\.id\s*=\s*\?", re.IGNORECASE)


def _check_alert_population_query(label, config, issues):
    """Flag an Event Query used to fetch an alert/detection *population*.

    "Fetch all high-severity alerts from the last 24h" asks for a population of
    Falcon platform objects the workflow does not hold. An Event Query runs
    against NG-SIEM/LogScale repos, whose alert contents are connector-dependent
    and silently return nothing on many tenants — so the correct tool is a
    CrowdStrike HTTP Request to ``/alerts/queries/alerts/v2``. This fires only
    when the query targets an alert/detection repo AND filters on severity, and
    never when it joins a held detection by argument (legitimate enrichment).
    See references/event-query-vs-api.md.
    """
    query = config.get("search_query")
    if not isinstance(query, str):
        return
    if _HELD_DETECTION_JOIN_RE.search(query):
        return  # enriching a detection the workflow already holds — legitimate
    if _ALERT_POPULATION_REPO_RE.search(query) and _SEVERITY_FILTER_RE.search(query):
        issues.append(
            f"ERROR: Event Query action '{label}' fetches an alert/detection "
            f"population by severity from an NG-SIEM repo. That data is a Falcon "
            f"platform object whose NG-SIEM contents are connector-dependent and "
            f"can silently return nothing — do NOT use an Event Query. Use a "
            f"CrowdStrike HTTP Request (Inline.HTTPRequest) to "
            f"/alerts/queries/alerts/v2 with FQL severity_name:'High' and a "
            f"created_timestamp bound (tenant-authenticated, no credential "
            f"config). Enriching a detection the workflow already holds "
            f"(Ngsiem.alert.id=?id) stays an Event Query. See "
            f"references/event-query-vs-api.md."
        )


def _validate_send_email(label, properties, issues):
    """Send email recipient checks: non-empty `to`, right field name, real address."""
    recipients = properties.get("to")
    if not recipients:
        if properties.get("recipients"):
            issues.append(
                f"ERROR: Send email action '{label}' puts recipients under "
                f"'recipients:', which is not a valid field — the Send email "
                f"action's recipient field is 'to'. The email reaches no one "
                f"at runtime. Rename 'recipients' to 'to'."
            )
        else:
            issues.append(
                f"ERROR: Send email action '{label}' has an empty or missing "
                f"'to:' recipient list. The email would be delivered to no "
                f"one at runtime. Ask the user for a recipient (Send email "
                f"only delivers to Falcon users and CID-approved domains)."
            )
        return
    # A populated `to` can still be a fabricated stand-in
    # (soc-team@yourcompany.com). It looks real, so nothing flags it — but it
    # dead-ends at runtime. Force a real address or a data ref.
    addrs = recipients if isinstance(recipients, list) else [recipients]
    for addr in addrs:
        if isinstance(addr, str) and FAKE_EMAIL_DOMAIN_PATTERN.search(addr):
            issues.append(
                f"ERROR: Send email action '{label}' has a placeholder "
                f"recipient '{addr}'. It looks real but is a fabricated "
                f"stand-in that dead-ends at runtime. Ask the user for a "
                f"real recipient (a Falcon user or CID-approved domain); "
                f"do not invent a 'yourcompany.com'-style address. In "
                f"headless/CI runs, use a plausible address on the org "
                f"domain."
            )


def _validate_next_refs(label, action, all_labels, issues):
    """Check that next references point to defined labels and use list form."""
    if not isinstance(action, dict):
        return
    if "next" not in action:
        return
    next_refs = action["next"]
    if isinstance(next_refs, str):
        # A scalar `next: Target` passes local YAML parsing but the import API
        # rejects the whole file with "import file must be a valid YAML file".
        # Every valid workflow uses list form. Flag it as an error with the fix.
        issues.append(
            f"ERROR: Action '{label}' has a scalar 'next: {next_refs}'. The "
            f"import API requires list form — write 'next:' with a '- {next_refs}' "
            f"item beneath it. A scalar value is rejected at import as \"import "
            f"file must be a valid YAML file\"."
        )
        return
    if isinstance(next_refs, list):
        for ref in next_refs:
            if ref not in all_labels:
                issues.append(
                    f"WARNING: Action '{label}' references '{ref}' in 'next' "
                    f"but no action/loop/condition with that name exists"
                )


def _validate_conditions(conditions, issues):
    """Check that each condition node routes via a rule or a default flow.

    Every entry under ``conditions`` is an exclusive-gateway outgoing flow. The
    release-time validator rejects any node whose flow has neither a match
    expression (``cel_expression`` / ``expression``) nor ``default: true`` —
    the error reads "exclusive gateway ... has no condition set and is not
    marked as default". Import and API validation do not catch this, so flag it
    here to avoid a failed release.
    """
    if not isinstance(conditions, dict):
        return
    for label, cond in conditions.items():
        if not isinstance(cond, dict):
            continue
        has_expression = bool(cond.get("cel_expression") or cond.get("expression"))
        is_default = cond.get("default") is True
        if not has_expression and not is_default:
            issues.append(
                f"ERROR: Condition '{label}' has neither a match expression "
                f"('cel_expression'/'expression') nor 'default: true'. Release "
                f"fails with \"exclusive gateway ... has no condition set and is "
                f"not marked as default\". A gated branch needs a 'cel_expression' "
                f"(its no-match fallthrough goes in 'else:'). Do not use a bare "
                f"'default: true' pass-through to fan out — list branch targets "
                f"directly in the source node's 'next:'."
            )


def _validate_loops(data, trigger, all_labels, issues, top_level=True):
    """Validate loop definitions, their actions, and any nested loops.

    Recurses into loops-within-loops so a condition or action defined several
    levels deep is still checked. A release-time "exclusive gateway ... has no
    condition set" failure often originates in a nested-loop condition that a
    shallow (one-level) scan would skip — the same gap that let deeply-nested
    Content Library playbooks pass validation yet fail at release.

    The ``for.input`` -> ``trigger.parameters.properties`` membership check runs
    only for ``top_level`` loops: a nested loop iterates over its parent loop's
    current item or output, not a trigger parameter, so applying that check to
    inner loops would flag valid workflows.
    """
    loops = data.get("loops", {})
    if not isinstance(loops, dict):
        return
    for loop_name, loop_def in loops.items():
        if not isinstance(loop_def, dict):
            continue
        loop_actions = loop_def.get("actions", {})
        if isinstance(loop_actions, dict):
            for label, action in loop_actions.items():
                _validate_action(label, action, issues)
                _validate_next_refs(label, action, all_labels, issues)

        _validate_conditions(loop_def.get("conditions", {}), issues)

        # Recurse into nested loops (loops defined inside this loop). Nested
        # loops don't draw for.input from trigger params, so top_level=False.
        _validate_loops(loop_def, trigger, all_labels, issues, top_level=False)

        if not top_level:
            continue
        loop_for = loop_def.get("for", {})
        if not isinstance(loop_for, dict):
            continue
        loop_input = loop_for.get("input")
        if not loop_input or not isinstance(trigger, dict):
            continue
        # A loop's for.input may iterate over one of four sources: a trigger
        # parameter (bare name), a prior action's output
        # (`<action_label>.<field>...`), a custom variable
        # (`WorkflowCustomVariable.<name>`), or a Signal trigger's own event
        # namespace (`Trigger.Category.*` / `Trigger.Detection.*`). Only a BARE
        # name (no dot) is a trigger-parameter reference, so only flag that form
        # when it is undefined. A dotted reference to a defined node, a custom
        # variable, or the trigger namespace is a valid data reference, not a
        # missing trigger param.
        if "." in loop_input:
            head = loop_input.split(".", 1)[0]
            if head in all_labels or head == "WorkflowCustomVariable":
                continue  # Valid action-output / custom-variable iteration.
            if head == "Trigger":
                # Signal triggers expose event fields under Trigger.Category.* /
                # Trigger.Detection.* rather than in parameters.properties (those
                # are for On-demand triggers). Confirmed valid at release. The
                # NG-SIEM-namespace guard (_validate_ngsiem_trigger_fields) handles
                # the one Trigger.Category.* form that is actually rejected.
                continue
        params = trigger.get("parameters", {})
        if not isinstance(params, dict):
            continue
        props = params.get("properties", {})
        if isinstance(props, dict) and loop_input not in props:
            issues.append(
                f"WARNING: Loop '{loop_name}' references "
                f"'{loop_input}' in for.input but it is not "
                f"defined in trigger.parameters.properties, a prior action's "
                f"output, or a custom variable"
            )


def _edge_targets(node):
    """Return the node labels a top-level node points at.

    Follows ``next`` and ``else`` (each a scalar or a list). For ``else_if`` it
    follows the documented string form (a label naming the next condition to
    chain to) and, defensively, a list of clause dicts each carrying a ``next``.
    Non-string entries are ignored so a malformed edge can't crash the walk.
    """
    targets = []
    if not isinstance(node, dict):
        return targets
    for key in ("next", "else"):
        value = node.get(key)
        if isinstance(value, list):
            targets.extend(v for v in value if isinstance(v, str))
        elif isinstance(value, str):
            targets.append(value)
    else_if = node.get("else_if")
    if isinstance(else_if, str):
        # Documented form: else_if is a STRING naming the next condition node to
        # chain to (if / else-if / else). Real console exports use this.
        targets.append(else_if)
    elif isinstance(else_if, list):
        for clause in else_if:
            if isinstance(clause, str):
                targets.append(clause)
            elif isinstance(clause, dict):
                nxt = clause.get("next")
                if isinstance(nxt, list):
                    targets.extend(v for v in nxt if isinstance(v, str))
                elif isinstance(nxt, str):
                    targets.append(nxt)
    return targets


def _validate_reachability(data, trigger, issues):
    """Flag top-level nodes not reachable from the trigger (disjoint graph).

    The release-time validator walks the graph from the trigger and rejects any
    workflow with an unreachable node: "disjoint node" and/or "At least one
    action or valid loop should be defined after the trigger". The most common
    cause is a trigger with a ``type`` and ``event`` but no ``next:`` edge, so
    the entire action graph is severed from the trigger. Import and API
    validate_only do NOT catch this, so a draft passes every local check and
    then fails release — the exact churn seen when a weaker model authors a
    workflow but forgets to wire the trigger to its first action.

    Reachability is computed over TOP-LEVEL nodes only (actions, conditions,
    loops). A loop's internal actions have their own entry edge and are not
    top-level nodes, so they never count as disjoint here.
    """
    top_nodes = {}
    for section in ("actions", "conditions", "loops"):
        nodes = data.get(section, {})
        if isinstance(nodes, dict):
            top_nodes.update(nodes)

    if not top_nodes:
        return  # Nothing to reach — an empty workflow is caught elsewhere.

    roots = trigger.get("next", []) if isinstance(trigger, dict) else []
    if isinstance(roots, str):
        roots = [roots]
    elif not isinstance(roots, list):
        roots = []

    if not roots:
        issues.append(
            "ERROR: Trigger has no 'next:' edge, so no action is reachable from "
            "it. Release fails with \"At least one action or valid loop should "
            "be defined after the trigger\". Add a 'next:' list under 'trigger:' "
            "naming the first action to run."
        )
        return

    reachable = set()
    stack = [r for r in roots if isinstance(r, str)]
    while stack:
        label = stack.pop()
        if label in reachable:
            continue
        reachable.add(label)
        if label in top_nodes:
            stack.extend(_edge_targets(top_nodes[label]))

    unreachable = [label for label in top_nodes if label not in reachable]
    if unreachable:
        preview = ", ".join(sorted(unreachable)[:5])
        more = "" if len(unreachable) <= 5 else f" (+{len(unreachable) - 5} more)"
        issues.append(
            f"ERROR: {len(unreachable)} node(s) are not reachable from the "
            f"trigger and will be rejected at release as \"disjoint node\": "
            f"{preview}{more}. Every action, condition, and loop must be "
            f"connected via a 'next:'/'else:' edge on a path from the trigger."
        )


def _validate_data_refs(file_path, issues):
    """Check data reference syntax: unclosed ${data[...]} and wrong-form refs."""
    with open(file_path, encoding="utf-8") as f:
        content = f.read()
    for match in DATA_REF_PATTERN.finditer(content):
        start = match.start()
        rest = content[start:]
        if "${data['" not in rest[:10] and '${data["' not in rest[:10]:
            continue
        bracket_count = 0
        closed = False
        for ch in rest:
            if ch == "{":
                bracket_count += 1
            elif ch == "}":
                bracket_count -= 1
                if bracket_count == 0:
                    closed = True
                    break
        if not closed:
            line_num = content[:start].count("\n") + 1
            issues.append(
                f"WARNING: Unclosed data reference at line {line_num}"
            )

    # Flag wrong-form data references. These pass YAML parsing and import, then
    # fail at release (or silently pass through as a literal string) because the
    # runtime can't resolve them. The only valid form is ${data['<node>.<field>']}.
    seen = set()
    for pattern, advice in BAD_DATA_REF_PATTERNS:
        for m in pattern.finditer(content):
            snippet = m.group(0)
            line_num = content[:m.start()].count("\n") + 1
            key = (line_num, snippet)
            if key in seen:
                continue
            seen.add(key)
            issues.append(
                f"WARNING: Invalid data reference '{snippet}' at line {line_num}: "
                f"{advice}."
            )

    # Release-time-only wrong forms (unknown-variable rejections). These are
    # ERRORs, not warnings: they pass import but fail release, forcing an
    # edit/re-import cycle.
    for pattern, advice in RELEASE_BAD_REF_PATTERNS:
        for m in pattern.finditer(content):
            snippet = m.group(0)
            line_num = content[:m.start()].count("\n") + 1
            key = (line_num, snippet)
            if key in seen:
                continue
            seen.add(key)
            issues.append(
                f"ERROR: Data reference '{snippet}' at line {line_num} is {advice}"
            )


def _validate_ngsiem_trigger_fields(trigger, file_path, issues):
    """Flag trigger fields that release rejects on the NG-SIEM Signal trigger.

    Three release-only failures, all caught only here (import and structural
    checks pass):

    1. ``Trigger.Detection.Product`` / ``Trigger.Detection.Description`` resolve on
       the base ``event: Investigatable`` trigger but are rejected on the
       dedicated ``event: Investigatable/NGSIEM`` trigger.
    2. Any ``Trigger.Category.Investigatable.*`` reference — that is the EPP
       payload namespace and does not resolve on the NG-SIEM trigger, which uses
       ``Trigger.Detection.*`` instead. Confirmed live: release rejects it as
       "unknown variable".
    3. A CEL comparison of an NG-SIEM array field (``SourceIPs``, ``UserNames``,
       etc.) against a string literal (``!= ''``). Those fields are
       ``list(string)``; release rejects the comparison as a type error. Use
       ``.size() > 0`` instead.

    Only check when the workflow actually uses the NG-SIEM trigger, so the base-
    ``Investigatable`` (EPP) examples that legitimately use these fields are
    unaffected.
    """
    if not isinstance(trigger, dict) or trigger.get("event") != NGSIEM_EVENT:
        return
    with open(file_path, encoding="utf-8") as f:
        content = f.read()
    for field in NGSIEM_REJECTED_TRIGGER_FIELDS:
        if field in content:
            issues.append(
                f"ERROR: '{field}' does not resolve on the "
                f"'{NGSIEM_EVENT}' trigger — release rejects it as an unknown "
                f"variable. It is available on the base 'event: Investigatable' "
                f"trigger only. On the NG-SIEM trigger the product is fixed "
                f"('NGSIEM'); use a static string instead. See "
                f"references/trigger-types.md."
            )
    for field in NGSIEM_REJECTED_MITRE_FIELDS:
        if field in content:
            issues.append(
                f"ERROR: '{field}' is advertised by trigger discovery but "
                f"release rejects it as an unknown variable on the "
                f"'{NGSIEM_EVENT}' trigger. MITRE tactics/techniques are not on "
                f"the NG-SIEM trigger payload at release time — source them from "
                f"the hydrated detection (Event Query results) or omit them. See "
                f"references/trigger-types.md."
            )
    if NGSIEM_REJECTED_NAMESPACE in content:
        issues.append(
            f"ERROR: '{NGSIEM_REJECTED_NAMESPACE}.*' does not resolve on the "
            f"'{NGSIEM_EVENT}' trigger — that is the EPP detection namespace "
            f"(event: Investigatable). Release rejects it as an unknown variable. "
            f"NG-SIEM detections use the 'Trigger.Detection.*' namespace instead "
            f"(e.g. Trigger.Detection.DetectionID, Trigger.Detection.Name, "
            f"Trigger.Detection.SeverityDisplayName). See references/trigger-types.md."
        )
    if _NGSIEM_ARRAY_STRING_CMP_RE.search(content):
        issues.append(
            "ERROR: a CEL expression compares an NG-SIEM array field "
            "(e.g. Trigger.Detection.NGSIEM.SourceIPs) against a string literal "
            "(\"!= ''\" or \"== ''\"). These fields are multivalued "
            "(list(string)), so release rejects the comparison with \"found no "
            "matching overload for '_!=_' applied to '(list(string), string)'\". "
            "Test presence with \".size() > 0\" and index an element with "
            "\"[0]\" (e.g. for a URL or variable value). Array fields: "
            f"{', '.join(NGSIEM_ARRAY_FIELDS)}. See references/trigger-types.md."
        )


def _validate_http_output_schema(data, file_path, issues):
    """Release requires _cs_inline_output_schema to reference HTTP response fields.

    An Inline.HTTPRequest whose response fields are read by a downstream action
    (``${data['<label>.<json.path>']}``) MUST declare a ``_cs_inline_output_schema``
    inside its ``http_transaction`` block. Without it, import and structural
    checks pass but release rejects every body-field reference as "property
    contains unknown variable". Confirmed live; the shipped VirusTotal example
    carries the schema.
    """
    actions = data.get("actions", {})
    if not isinstance(actions, dict):
        return
    http_labels = {
        label
        for label, action in actions.items()
        if isinstance(action, dict) and action.get("class") == "Inline.HTTPRequest"
    }
    if not http_labels:
        return
    try:
        with open(file_path, encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        return
    for label in http_labels:
        action = actions[label]
        transaction = action.get("properties", {}).get("http_transaction")
        has_schema = (
            isinstance(transaction, dict)
            and transaction.get("_cs_inline_output_schema")
        )
        if has_schema:
            continue
        # Referenced with a sub-path? (a bare ${data['<label>']} with no field
        # doesn't need a response schema — only field access does.)
        referenced = re.search(
            r"data\['" + re.escape(label) + r"\.[^']+'\]", content
        )
        if referenced:
            issues.append(
                f"ERROR: HTTP action '{label}' has its response fields referenced "
                f"downstream but is missing '_cs_inline_output_schema' in its "
                f"http_transaction. Release rejects the references as unknown "
                f"variables. Add the response JSON-schema string. See "
                f"references/http-actions.md."
            )


def _collect_pinned_action_labels(data):
    """Return the set of action labels that declare a ``version_constraint``.

    Actions live under ``actions:`` but can also nest inside condition branches
    and loops, so walk the whole structure. A real action node is a mapping that
    carries an ``id``; its label is the key that maps to it.
    """
    pinned = set()

    def walk(node):
        if isinstance(node, dict):
            for label, value in node.items():
                if (
                    isinstance(value, dict)
                    and "id" in value
                    and "version_constraint" in value
                ):
                    pinned.add(label)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return pinned


def _validate_pinned_data_paths(data, file_path, issues):
    """Flag long-form namespaced data refs to a PINNED action.

    ``${data['<node>.<namespace>.<field>']}`` releases fine when <node> is
    unpinned, but with a ``version_constraint`` the platform addresses the
    collapsed ``${data['<node>.<field>']}`` output and release rejects the
    namespaced path as an unknown variable (confirmed live, PR #34). Import and
    api_validate both pass, so this only surfaces at release.
    """
    pinned = _collect_pinned_action_labels(data)
    if not pinned:
        return
    try:
        with open(file_path, encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        return
    seen = set()
    for match in re.finditer(r"data\[\??'([^']+)'\]", content):
        path = match.group(1)
        node, _, rest = path.partition(".")
        if node not in pinned or not rest or path in seen:
            continue
        lowered = rest.lower()
        for namespace, collapsed in PINNED_COLLAPSE_NAMESPACES.items():
            if lowered.startswith(namespace + "."):
                seen.add(path)
                issues.append(
                    f"ERROR: '{node}' has version_constraint set but is "
                    f"referenced with the long namespaced path "
                    f"${{data['{path}']}}. A pinned action addresses its "
                    f"collapsed output, so release rejects this as an unknown "
                    f"variable. Drop the '{namespace}' namespace — "
                    f"${{data['{node}.{collapsed}']}}. See "
                    f"references/best-practices.md."
                )
                break


def _validate_top_level_shape(data, issues):
    """Flag off-schema top-level keys and a missing ``actions:`` section.

    A Fusion workflow nests its steps under ``actions:`` (plus optional
    ``conditions:``/``loops:``). A weaker model sometimes invents a different
    graph shape — ``nodes``/``edges``, ``steps``/``outputs``, or the action
    labels dumped straight at the top level with no ``actions:`` wrapper. Every
    downstream check reads ``data.get("actions", {})``, finds nothing, and
    passes trivially, so the file validates clean and then fails at import or
    release. Catch it here: any top-level key outside the known schema is an
    error, and a workflow whose trigger fans out to actions must actually define
    an ``actions:`` section.
    """
    unknown = [k for k in data if k not in ALLOWED_TOP_LEVEL_KEYS]
    if unknown:
        issues.append(
            f"ERROR: Unknown top-level key(s): {', '.join(sorted(unknown))}. A "
            f"workflow's steps go under 'actions:' (with optional 'conditions:' "
            f"and 'loops:'), not under invented keys like 'nodes'/'edges'/"
            f"'steps', and action labels must be nested inside 'actions:', not "
            f"placed at the top level. Allowed top-level keys: "
            f"{', '.join(sorted(ALLOWED_TOP_LEVEL_KEYS))}."
        )
    trigger = data.get("trigger", {})
    trigger_targets = trigger.get("next") if isinstance(trigger, dict) else None
    has_node_section = any(
        isinstance(data.get(section), dict)
        for section in ("actions", "conditions", "loops")
    )
    if trigger_targets and not has_node_section:
        issues.append(
            "ERROR: The trigger points at a node via 'next:', but the workflow "
            "defines no 'actions:' (or 'conditions:'/'loops:') section. Nest "
            "every action node under a top-level 'actions:' mapping."
        )


def structural_check(file_path):
    """
    Validate YAML structure against workflow schema rules.
    Returns list of issue strings. Empty list means all checks passed.
    """
    issues = []

    try:
        with open(file_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        return [f"ERROR: YAML parse error: {exc}"]

    if not isinstance(data, dict):
        return ["ERROR: YAML did not parse as a dictionary"]

    _validate_top_level_shape(data, issues)

    trigger = data.get("trigger", {})
    if isinstance(trigger, dict):
        trigger_type = trigger.get("type")
        if not trigger_type:
            issues.append(
                "ERROR: Trigger is missing a 'type'. "
                f"Must be one of: {', '.join(sorted(VALID_TRIGGER_TYPES))}"
            )
        elif trigger_type not in VALID_TRIGGER_TYPES:
            issues.append(
                f"ERROR: Invalid trigger type '{trigger_type}'. "
                f"Must be one of: {', '.join(sorted(VALID_TRIGGER_TYPES))}"
            )
        # Signal triggers must name their event source in an 'event' field (the
        # trigger category, e.g. 'Investigatable/NGSIEM'). Without it the import
        # API fails with code 2003 "unknown trigger event named ". Discover the
        # value with trigger_search.py.
        if trigger_type == "Signal" and not trigger.get("event"):
            issues.append(
                "ERROR: Signal trigger is missing an 'event' field (the trigger "
                "category, e.g. 'Investigatable/NGSIEM'). Import fails without it. "
                "Discover the value with trigger_search.py."
            )

        # Scheduled triggers also require an 'event' field naming the trigger
        # category, which is 'Schedule'. Without it the import API fails the same
        # way as Signal — code 2003 "unknown trigger event named " on the trigger
        # node — even though a 'schedule:' block with cron/timezone is present.
        if trigger_type == "Scheduled" and not trigger.get("event"):
            issues.append(
                "ERROR: Scheduled trigger is missing an 'event' field. Set "
                "'event: Schedule' (the trigger category). Import fails without it "
                "with code 2003 'unknown trigger event named '. The cron and "
                "timezone go in the 'schedule:' block; the 'event' is separate."
            )

        # The Scheduled 'schedule' block's cron/timezone fields are named
        # 'time_cycle' and 'tz' — NOT 'cron'/'timezone'. The wrong names import
        # cleanly but fail at release with "missing timer_event_definition or
        # schedule parameters for trigger" (confirmed live). Flag them here so the
        # error surfaces at authoring time instead of release. Only checked when a
        # 'schedule:' block is present — an `event: Schedule` trigger with NO
        # schedule block is a valid on-demand job template (the schedule is
        # supplied by the caller at runtime), as several Foundry samples show.
        schedule = trigger.get("schedule")
        if isinstance(schedule, dict):
            if "cron" in schedule and "time_cycle" not in schedule:
                issues.append(
                    "ERROR: Scheduled trigger's schedule uses 'cron:' — the release "
                    "field is 'time_cycle:'. 'cron' imports but fails release with "
                    "'missing timer_event_definition or schedule parameters'. Rename "
                    "'cron' to 'time_cycle'."
                )
            if "timezone" in schedule and "tz" not in schedule:
                issues.append(
                    "ERROR: Scheduled trigger's schedule uses 'timezone:' — the "
                    "release field is 'tz:'. Rename 'timezone' to 'tz' (e.g. "
                    "'tz: Etc/UTC')."
                )
            if "time_cycle" not in schedule:
                issues.append(
                    "ERROR: Scheduled trigger's schedule block has no 'time_cycle' "
                    "(the cron expression, e.g. '0 */6 * * *'). A schedule block "
                    "must carry it, or omit the block entirely for a caller-scheduled "
                    "job template. See references/trigger-types.md."
                )

        # Signal triggers fire from an event and carry no caller-supplied input,
        # so a 'parameters' schema on them is invalid: any field it declares
        # becomes an undefined variable at release ("Signal triggers do not
        # support parameters"). Only On demand triggers take input parameters.
        if trigger_type == "Signal" and trigger.get("parameters"):
            issues.append(
                "ERROR: Signal trigger has a 'parameters' schema. Signal "
                "triggers fire from an event and take no input parameters — the "
                "fields it declares become undefined variables at release. Move "
                "runtime inputs to WorkflowCustomVariable/CreateVariable actions, "
                "or use an On demand trigger if the workflow needs caller inputs."
            )

        # A scalar `trigger.next: Target` parses locally but the import API
        # rejects the file with "import file must be a valid YAML file". Every
        # valid workflow uses list form under the trigger.
        trigger_next = trigger.get("next")
        if isinstance(trigger_next, str):
            issues.append(
                f"ERROR: Trigger has a scalar 'next: {trigger_next}'. The import "
                f"API requires list form — write 'next:' with a '- {trigger_next}' "
                f"item beneath it. A scalar value is rejected at import as "
                f"\"import file must be a valid YAML file\"."
            )

    all_labels = _collect_node_labels(data)

    actions = data.get("actions", {})
    if isinstance(actions, dict):
        for label, action in actions.items():
            _validate_action(label, action, issues)
            _validate_next_refs(label, action, all_labels, issues)

    _validate_conditions(data.get("conditions", {}), issues)
    _validate_loops(data, trigger, all_labels, issues)
    _validate_reachability(data, trigger, issues)
    _validate_data_refs(file_path, issues)
    _validate_http_output_schema(data, file_path, issues)
    _validate_ngsiem_trigger_fields(trigger, file_path, issues)
    _validate_pinned_data_paths(data, file_path, issues)

    return issues


def api_validate(file_path):
    """
    Validate via the CrowdStrike import API with validate_only=true.
    Returns (success: bool, message: str).
    """
    try:
        client = get_client()
        resp = client.import_definition(data_file=file_path, validate_only=True)
        body = resp["body"]
        errors = body.get("errors", [])
        if errors:
            msg = "; ".join(e.get("message", str(e)) for e in errors)
            return False, msg
        # The import API reports schema/trigger/graph problems as a 200 with an
        # empty top-level `errors` list but a non-empty `validation_errors` array
        # INSIDE each resource — e.g. a Scheduled trigger missing `event:` comes
        # back as resources[0].validation_errors = [{code: 2003, message:
        # "unknown trigger event named ", node_id: "trigger"}]. Release then
        # rejects the same file. Surface these so the dry-run matches release.
        resource_errors = []
        for resource in body.get("resources", []):
            if not isinstance(resource, dict):
                continue
            for verr in resource.get("validation_errors", []) or []:
                message = verr.get("message", "").strip() or f"code {verr.get('code')}"
                node = verr.get("node_id")
                resource_errors.append(f"{message} (node: {node})" if node else message)
        if resource_errors:
            return False, "; ".join(resource_errors)
        if resp["status_code"] not in (200, 201):
            return False, f"API returned status {resp['status_code']}"
        return True, "OK"
    except (ConnectionError, RuntimeError, OSError) as exc:
        return False, str(exc)


def validate_file(file_path, preflight_only=False):
    """
    Validate a single file. Returns (passed: bool, messages: list[str]).
    """
    messages = []

    # Pre-flight
    issues = preflight_check(file_path)
    has_errors = any(i.startswith("ERROR") for i in issues)
    messages.extend(issues)

    if has_errors:
        messages.append("Pre-flight FAILED — fix errors above before structural validation")
        return False, messages

    if not issues:
        messages.append("Pre-flight passed")

    # Structural validation
    struct_issues = structural_check(file_path)
    struct_errors = any(i.startswith("ERROR") for i in struct_issues)
    messages.extend(struct_issues)

    if struct_errors:
        messages.append("Structural validation FAILED — fix errors above before API validation")
        return False, messages

    if not struct_issues:
        messages.append("Structural validation passed")

    if preflight_only:
        return True, messages

    # API validation
    ok, msg = api_validate(file_path)
    if ok:
        messages.append("API validation passed")
    else:
        messages.append(f"API validation FAILED: {msg}")

    return ok, messages


def main():
    """CLI entry point for workflow validation."""
    parser = argparse.ArgumentParser(description="Validate Fusion workflow YAML files")
    parser.add_argument("files", nargs="+", metavar="FILE", help="YAML file(s) to validate")
    parser.add_argument("--preflight-only", action="store_true", help="Skip API validation")
    args = parser.parse_args()

    all_passed = True
    for fp in args.files:
        print(f"\n  {os.path.basename(fp)}")
        passed, messages = validate_file(fp, preflight_only=args.preflight_only)
        for m in messages:
            prefix = "    \u2713" if not m.startswith(("ERROR", "WARNING")) and "FAILED" not in m else "    \u2717"
            print(f"{prefix} {m}")
        if not passed:
            all_passed = False
        print()

    if all_passed:
        print("All files passed validation.")
    else:
        print("Some files failed validation.")
        sys.exit(1)


if __name__ == "__main__":
    main()
