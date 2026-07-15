#!/usr/bin/env bash
#
# test-validate.sh
#
# Lightweight structural checks for the fusion-skills plugin:
#   - Every SKILL.md has valid YAML frontmatter
#   - Every Python script is syntactically valid
#   - Reference docs named in each skill's Reading Guide exist
#
# Exits 1 if any check fails.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

if [ -t 1 ]; then
  GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; NC=$'\033[0m'
else
  GREEN=""; RED=""; NC=""
fi

PASS=0
FAIL=0
pass() { echo "  ${GREEN}PASS${NC}: $1"; PASS=$((PASS + 1)); }
fail() { echo "  ${RED}FAIL${NC}: $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "1. SKILL.md frontmatter is valid YAML"
echo "─────────────────────────────────────"
while IFS= read -r skill; do
  if python3 -c "
import sys, yaml
text = open('$skill', encoding='utf-8').read()
if not text.startswith('---'):
    sys.exit('no frontmatter')
fm = text.split('---', 2)[1]
data = yaml.safe_load(fm)
assert isinstance(data, dict), 'frontmatter is not a mapping'
for key in ('name', 'description', 'version'):
    assert key in data, f'missing key: {key}'
" 2>/dev/null; then
    pass "$(echo "$skill" | sed "s#$ROOT/##")"
  else
    fail "$(echo "$skill" | sed "s#$ROOT/##") — invalid frontmatter"
  fi
done < <(find "$ROOT" -name SKILL.md -not -path '*/node_modules/*' -not -path '*/.venv/*' -not -path '*/.git/*')

echo ""
echo "2. Python scripts are syntactically valid"
echo "─────────────────────────────────────────"
while IFS= read -r py; do
  if python3 -c "import ast, sys; ast.parse(open('$py', encoding='utf-8').read())" 2>/dev/null; then
    pass "$(echo "$py" | sed "s#$ROOT/##")"
  else
    fail "$(echo "$py" | sed "s#$ROOT/##") — syntax error"
  fi
done < <(find "$ROOT" -name '*.py' -not -path '*/__pycache__/*' -not -path '*/.venv/*')

echo ""
echo "3. Reference docs referenced in Reading Guides exist"
echo "────────────────────────────────────────────────────"
# Check the known reference docs that skills point to.
check_ref() {
  if [ -f "$ROOT/$1" ]; then pass "$1"; else fail "$1 — missing"; fi
}
check_ref "skills/lookup-files/references/cql-match-function.md"
check_ref "skills/lookup-files/references/lookup-file-formats.md"
check_ref "skills/workflows/references/yaml-schema.md"
check_ref "skills/workflows/references/json-structure.md"
check_ref "skills/workflows/references/cel-expressions.md"
check_ref "skills/workflows/references/trigger-types.md"
check_ref "skills/workflows/references/best-practices.md"

echo ""
echo "─────────────────────────────────────"
echo "Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"
echo ""

if [ "$FAIL" -gt 0 ]; then exit 1; fi
exit 0
