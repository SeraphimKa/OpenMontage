#!/usr/bin/env bash
# Start Claude Code inside OpenMontage with the project's Python environment.
# Usage: scripts/openmontage.sh [claude arguments...]
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# A Terminal opened from Finder may not have Homebrew or ~/.local/bin on PATH.
if [ -x /opt/homebrew/bin/brew ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -x /usr/local/bin/brew ]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi
export PATH="$HOME/.local/bin:$PATH"

if [ ! -f "$repo/.venv/bin/activate" ]; then
  echo "OpenMontage is not set up yet. On a Mac run $repo/scripts/bootstrap-mac.sh;"
  echo "on Linux run 'make setup' in $repo (see docs/LINUX_SETUP.md)."
  exit 1
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code is not installed yet. Paste this into Terminal to install it:"
  echo ""
  echo "    curl -fsSL https://claude.ai/install.sh | bash"
  echo ""
  echo "Then type 'claude' once to log in, and start OpenMontage again."
  echo "Official instructions: https://code.claude.com/docs/en/setup"
  exit 1
fi

# venv activate scripts are not written for `set -u`.
set +u
# shellcheck disable=SC1091
source "$repo/.venv/bin/activate"
set -u
export PATH="$repo/.venv/bin:$PATH"

cd "$repo"
exec claude "$@"
