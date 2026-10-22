#!/usr/bin/env bash
#
# test-python-wrapper.sh — regression guard for scripts/python.sh's venv check.
#
# A first install that fails (for example, on a network that blocks public PyPI)
# used to leave a venv with a Python binary but no packages. python.sh only
# checked for the binary, so every later run skipped the install and died with
# "No module named 'falconpy'". It now also requires the marker that
# setup-python-venv.sh writes after a successful install.
#
# This test is hermetic: python.sh runs from a temp copy beside a stub
# setup-python-venv.sh, against a fake HOME. No venv build, no network.
set -uo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/python-wrapper.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0
check() { # check <desc> <cond-rc>
  if [ "$2" -eq 0 ]; then echo "  ✅ $1"; PASS=$((PASS + 1));
  else echo "  ❌ $1"; FAIL=$((FAIL + 1)); fi
}

mkdir -p "$WORK/scripts"
cp "$REPO/scripts/python.sh" "$REPO/scripts/python-detect.sh" "$WORK/scripts/"
# The stub records that it ran and writes the marker, like a successful install.
cat > "$WORK/scripts/setup-python-venv.sh" <<'STUB'
#!/usr/bin/env bash
touch "$HOME/setup-ran" "$HOME/.cache/claude-code-fusion/venv/.requirements.sha256"
STUB
chmod +x "$WORK/scripts/setup-python-venv.sh"

# A fake venv whose python just echoes, so python.sh's exec is observable.
export HOME="$WORK/home"
VENV="$HOME/.cache/claude-code-fusion/venv"
mkdir -p "$VENV/bin"
printf '#!/usr/bin/env bash\necho "venv python ran"\n' > "$VENV/bin/python3"
chmod +x "$VENV/bin/python3"

echo "== a venv without the install marker is rebuilt =="
out=$("$WORK/scripts/python.sh" script.py 2>&1)
[ -f "$HOME/setup-ran" ]; check "setup-python-venv.sh ran" $?
[[ "$out" == *"venv python ran"* ]]; check "the script then ran in the venv" $?

echo "== a venv with the install marker is used as is =="
rm -f "$HOME/setup-ran"
out=$("$WORK/scripts/python.sh" script.py 2>&1)
[ ! -f "$HOME/setup-ran" ]; check "setup-python-venv.sh did not run" $?
[[ "$out" == *"venv python ran"* ]]; check "the script ran in the venv" $?

echo ""
echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
