#!/usr/bin/env bash
# PreToolUse(Bash) hook: refuse `make shot-go` once the month's generation spend
# has reached OM_MONTHLY_CAP_USD. Everything else passes untouched, and so does
# shot-go when no cap is set or the venv is missing (the session check reports
# that separately). Reads the tool call as JSON on stdin; a deny is returned as
# the harness's JSON decision with the reason from scripts/om_spend.py.

repo="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
input=$(cat)

command=$(printf '%s' "$input" | "$repo/.venv/bin/python" -c \
  'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null) || exit 0

case "$command" in
  *"make shot-go"*|*"om_shot.py"*"--go"*) ;;
  *) exit 0 ;;
esac

message=$("$repo/.venv/bin/python" "$repo/scripts/om_spend.py" --check 2>/dev/null)
rc=$?
if [ "$rc" -eq 3 ]; then
  "$repo/.venv/bin/python" -c 'import json,sys; print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": sys.argv[1]}}))' "$message"
  exit 0
fi
[ -n "$message" ] && echo "$message"
exit 0
