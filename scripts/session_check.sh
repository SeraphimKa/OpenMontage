#!/usr/bin/env bash
# SessionStart check for Claude Code: say in plain words what is missing before
# anyone spends a turn on it. Advisory only, so it always exits 0. Its stdout
# reaches the agent as context; it never prints a secret.
#
# The desktop app starts Claude without scripts/openmontage.sh, so the venv is
# not on PATH there and a bare `python` is the system one: without Pillow the
# Ark tool calls a healthy image "unreadable or corrupt". Homebrew's bin is not
# on PATH either in an app launched from Finder, which hides ffmpeg.

repo="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$repo" || exit 0

problems=()

if [ -x "$repo/.venv/bin/python" ]; then
  if ! "$repo/.venv/bin/python" -c "import PIL, dotenv, yaml" >/dev/null 2>&1; then
    problems+=("The virtual environment is incomplete. Run 'make setup' in $repo.")
  fi
else
  problems+=("OpenMontage is not set up: there is no virtual environment. On a Mac run scripts/bootstrap-mac.sh; on Linux run 'make setup' (docs/LINUX_SETUP.md).")
fi

if ! command -v ffmpeg >/dev/null 2>&1 \
   && [ ! -x /opt/homebrew/bin/ffmpeg ] && [ ! -x /usr/local/bin/ffmpeg ]; then
  problems+=("ffmpeg is not installed, so no cut can be assembled. Mac: 'brew install ffmpeg'. Ubuntu: 'sudo apt install ffmpeg'. Arch: 'sudo pacman -S ffmpeg'.")
fi

if [ ! -f "$repo/.env" ]; then
  problems+=("There is no .env file. For cloud providers, copy .env.example to .env (chmod 600 .env) and add only your own selected provider keys. Local tools may still work without keys.")
fi

if [ "${#problems[@]}" -eq 0 ]; then
  echo "OpenMontage session check: venv, ffmpeg and .env are in place. Run Python as .venv/bin/python or through the make targets. Check the tool registry's provider_menu_summary() for actual provider availability; .env presence alone does not imply a key is configured."
else
  echo "OpenMontage session check found ${#problems[@]} problem(s). Tell the user in these words before doing anything else:"
  for p in "${problems[@]}"; do
    echo "- $p"
  done
fi
exit 0
