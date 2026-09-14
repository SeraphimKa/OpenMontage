#!/usr/bin/env bash
# One-command OpenMontage setup for macOS (Apple Silicon or Intel).
#
#   scripts/bootstrap-mac.sh [--env PATH] [--force-env] [--dir PATH] [--dry-run]
#
# Safe to re-run: every step checks before it acts. Written for the bash 3.2
# that ships with macOS, so no associative arrays, mapfile or ${var,,}.
set -euo pipefail

REPO_URL="${OPENMONTAGE_REPO_URL:-https://github.com/SeraphimKa/OpenMontage.git}"
CLAUDE_SETUP_URL="https://code.claude.com/docs/en/setup"
# Verified against $CLAUDE_SETUP_URL on 2026-09-14.
CLAUDE_INSTALL_CMD="curl -fsSL https://claude.ai/install.sh | bash"
BREW_INSTALL_URL="https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
BREW_FORMULAE="git ffmpeg node python@3.12 make deno"  # deno: yt-dlp needs it for YouTube downloads
PIPER_VOICE="en_US-lessac-medium"

DRY_RUN=0
TARGET_DIR=""
ENV_SRC=""
FORCE_ENV=0

usage() {
  cat <<'EOF'
Usage: bootstrap-mac.sh [options]

  --env PATH     Copy this .env file (from your colleague) into the repo.
  --force-env    Replace an existing repo .env (the old one is backed up).
  --dir PATH     Where to put OpenMontage (default: ~/OpenMontage, or the
                 checkout this script lives in).
  --dry-run      Print every step without installing anything (only a log file
                 is written). Works on any OS.
  -h, --help     Show this help.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --force-env) FORCE_ENV=1 ;;
    --dir)
      [ $# -ge 2 ] || { echo "--dir needs a path" >&2; exit 2; }
      TARGET_DIR="$2"; shift ;;
    --env)
      [ $# -ge 2 ] || { echo "--env needs a path" >&2; exit 2; }
      ENV_SRC="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

OS_NAME="$(uname -s)"
if [ "$OS_NAME" != "Darwin" ] && [ "$DRY_RUN" -eq 0 ]; then
  echo "This setup script is for macOS only (this computer reports '$OS_NAME')."
  echo "On Linux, follow the Quick Start in README.md, or add --dry-run to preview the steps."
  exit 1
fi

# ---- Logging --------------------------------------------------------------

if [ "$OS_NAME" = "Darwin" ]; then
  LOG_FILE="$HOME/Library/Logs/openmontage-bootstrap.log"
else
  LOG_FILE="${TMPDIR:-/tmp}/openmontage-bootstrap.log"
fi
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    echo ""
    echo "Setup stopped before finishing (exit code $rc)."
    echo "Full log: $LOG_FILE"
    echo "Send that file to the colleague who is helping you set up OpenMontage."
  fi
}
trap on_exit EXIT

echo ""
echo "==== OpenMontage bootstrap $(date '+%Y-%m-%d %H:%M:%S') ===="
[ "$DRY_RUN" -eq 1 ] && echo "DRY RUN: nothing will be installed or changed."
echo "Log: $LOG_FILE"

step() { echo ""; echo "==> $*"; }
info() { echo "    $*"; }
warn() { echo "    WARNING: $*"; }

# Runs a command, or in dry-run mode prints what it would run.
run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '    [dry-run] would run:'
    printf ' %q' "$@"
    printf '\n'
  else
    printf '    $'
    printf ' %q' "$@"
    printf '\n'
    "$@"
  fi
}

# ---- 1. Xcode Command Line Tools -----------------------------------------

step "Checking Xcode Command Line Tools"
if [ "$OS_NAME" = "Darwin" ] && xcode-select -p >/dev/null 2>&1; then
  info "Installed at $(xcode-select -p)."
elif [ "$DRY_RUN" -eq 1 ]; then
  run xcode-select --install
  info "[dry-run] would wait until 'xcode-select -p' succeeds."
else
  info "Not installed. A macOS window will ask to install them: click Install."
  xcode-select --install >/dev/null 2>&1 || true
  waited=0
  until xcode-select -p >/dev/null 2>&1; do
    if [ "$waited" -ge 3600 ]; then
      echo "Command Line Tools did not finish installing within an hour."
      exit 1
    fi
    sleep 15
    waited=$((waited + 15))
    info "Still waiting for the Command Line Tools install... (${waited}s)"
  done
  info "Command Line Tools installed."
fi

# ---- 2. Homebrew -----------------------------------------------------------

load_brew_env() {
  if [ -x /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
}

step "Checking Homebrew"
load_brew_env
if command -v brew >/dev/null 2>&1; then
  info "Found $(command -v brew)."
else
  info "Installing Homebrew. It may ask for your Mac login password."
  if [ "$DRY_RUN" -eq 1 ]; then
    info "[dry-run] would run: /bin/bash -c \"\$(curl -fsSL $BREW_INSTALL_URL)\""
  else
    # Run on the terminal directly so its "Press RETURN" prompt is never
    # stuck behind the log pipe.
    info "Homebrew installer output is shown on screen only (not in this log)."
    /bin/bash -c "$(curl -fsSL "$BREW_INSTALL_URL")" </dev/tty >/dev/tty 2>&1
    load_brew_env
    command -v brew >/dev/null 2>&1 || { echo "Homebrew installed but 'brew' is not on PATH."; exit 1; }
  fi
fi

# Make brew tools available in every new Terminal window, not just this run.
if [ -x /opt/homebrew/bin/brew ]; then
  BREW_BIN=/opt/homebrew/bin/brew
elif [ -x /usr/local/bin/brew ]; then
  BREW_BIN=/usr/local/bin/brew
elif [ "$(uname -m)" = "arm64" ] || [ "$OS_NAME" != "Darwin" ]; then
  BREW_BIN=/opt/homebrew/bin/brew
else
  BREW_BIN=/usr/local/bin/brew
fi
SHELLENV_LINE="eval \"\$($BREW_BIN shellenv)\""
ZPROFILE="$HOME/.zprofile"
if [ -f "$ZPROFILE" ] && command grep -qF "$BREW_BIN shellenv" "$ZPROFILE"; then
  info "Homebrew is already set up in $ZPROFILE."
elif [ "$DRY_RUN" -eq 1 ]; then
  info "[dry-run] would append to $ZPROFILE: $SHELLENV_LINE"
else
  printf '\n%s\n' "$SHELLENV_LINE" >> "$ZPROFILE"
  info "Added Homebrew to $ZPROFILE."
fi

# ---- 3. Command-line tools from Homebrew ----------------------------------

step "Checking git, ffmpeg, node, python@3.12, make, deno"
missing=""
for formula in $BREW_FORMULAE; do
  if [ "$DRY_RUN" -eq 0 ] && brew list --formula "$formula" >/dev/null 2>&1; then
    info "$formula: installed"
  elif [ "$DRY_RUN" -eq 1 ] && command -v brew >/dev/null 2>&1 && brew list --formula "$formula" >/dev/null 2>&1; then
    info "$formula: installed"
  else
    missing="$missing $formula"
  fi
done
if [ -n "$missing" ]; then
  info "Installing:$missing (this can take several minutes)"
  # shellcheck disable=SC2086  # intentional word splitting of the formula list
  run brew install $missing
fi

if [ "$DRY_RUN" -eq 0 ]; then
  PY312="$(brew --prefix python@3.12)/bin/python3.12"
else
  PY312="/opt/homebrew/opt/python@3.12/bin/python3.12"
fi
if command -v gmake >/dev/null 2>&1; then
  MAKE_CMD=gmake
elif [ "$DRY_RUN" -eq 1 ]; then
  MAKE_CMD=gmake
else
  MAKE_CMD="make"
fi

# ---- 4. The OpenMontage repository ----------------------------------------

step "Locating OpenMontage"
SCRIPT_REPO=""
SCRIPT_SRC="${BASH_SOURCE[0]:-}"
if [ -n "$SCRIPT_SRC" ] && [ -f "$SCRIPT_SRC" ]; then
  candidate="$(cd "$(dirname "$SCRIPT_SRC")/.." && pwd)"
  [ -f "$candidate/AGENT_GUIDE.md" ] && SCRIPT_REPO="$candidate"
fi

if [ -n "$TARGET_DIR" ]; then
  REPO_DIR="$TARGET_DIR"
elif [ -n "$SCRIPT_REPO" ]; then
  REPO_DIR="$SCRIPT_REPO"
else
  REPO_DIR="$HOME/OpenMontage"
fi

if [ -n "$SCRIPT_REPO" ] && [ "$REPO_DIR" = "$SCRIPT_REPO" ]; then
  info "Using this checkout: $REPO_DIR"
elif [ -d "$REPO_DIR/.git" ]; then
  info "Already downloaded at $REPO_DIR; fetching updates."
  if [ "$DRY_RUN" -eq 1 ]; then
    run git -C "$REPO_DIR" pull --ff-only
  elif ! git -C "$REPO_DIR" pull --ff-only; then
    warn "Could not update $REPO_DIR (continuing with the copy you already have)."
  fi
elif [ -e "$REPO_DIR" ] && [ -n "$(ls -A "$REPO_DIR" 2>/dev/null)" ]; then
  echo "$REPO_DIR exists but is not an OpenMontage download. Move it aside or pass --dir."
  exit 1
else
  info "Downloading OpenMontage into $REPO_DIR"
  run git clone "$REPO_URL" "$REPO_DIR"
fi

if [ "$DRY_RUN" -eq 0 ]; then
  cd "$REPO_DIR"
  chmod +x scripts/bootstrap-mac.sh scripts/openmontage.sh scripts/OpenMontage.command 2>/dev/null || true
else
  run chmod +x "$REPO_DIR/scripts/bootstrap-mac.sh" "$REPO_DIR/scripts/openmontage.sh" "$REPO_DIR/scripts/OpenMontage.command"
fi

# ---- 5. API keys (.env) ----------------------------------------------------
# Done before `make setup`, which would otherwise create a blank .env first.

step "Installing your .env (API keys)"
REPO_ENV="$REPO_DIR/.env"
if [ -z "$ENV_SRC" ]; then
  if [ -f "$REPO_ENV" ]; then
    info ".env already present; leaving it alone."
  else
    info "No --env file given. Setup will create an empty .env; cloud tools stay off until keys are added."
  fi
elif [ "$DRY_RUN" -eq 0 ] && [ ! -f "$ENV_SRC" ]; then
  echo "The .env file you pointed to does not exist: $ENV_SRC"
  exit 1
elif [ -f "$REPO_ENV" ] && [ "$FORCE_ENV" -eq 0 ]; then
  info ".env already present; keeping it. Re-run with --force-env to replace it."
else
  [ "$DRY_RUN" -eq 1 ] && [ ! -f "$ENV_SRC" ] && warn "$ENV_SRC does not exist on this machine."
  if [ -f "$REPO_ENV" ]; then
    backup="$REPO_ENV.bak.$(date +%Y%m%d%H%M%S)"
    run cp -p "$REPO_ENV" "$backup"
    run chmod 600 "$backup"
  fi
  # Only the path is shown, never the contents.
  run cp "$ENV_SRC" "$REPO_ENV"
  run chmod 600 "$REPO_ENV"
  if [ "$DRY_RUN" -eq 0 ]; then
    info "Copied your .env into $REPO_DIR (private to your user, ignored by git)."
  fi
fi

# ---- 6. make setup ---------------------------------------------------------

step "Installing OpenMontage's own dependencies (make setup)"
info "This is the longest step: expect 5-15 minutes."
run "$MAKE_CMD" -C "$REPO_DIR" setup "BASE_PYTHON=$PY312"

if [ "$DRY_RUN" -eq 1 ]; then
  info "[dry-run] if $PIPER_VOICE.onnx is missing, would run (in $REPO_DIR): .venv/bin/python -m piper.download_voices $PIPER_VOICE"
elif [ -f "$REPO_DIR/$PIPER_VOICE.onnx" ]; then
  info "Piper voice $PIPER_VOICE is present."
else
  info "Downloading the free Piper narration voice ($PIPER_VOICE)"
  if ! (cd "$REPO_DIR" && .venv/bin/python -m piper.download_voices "$PIPER_VOICE"); then
    warn "Piper voice download failed; free narration will be unavailable until it succeeds."
  fi
fi

# ---- 7. Claude Code --------------------------------------------------------

step "Checking Claude Code"
export PATH="$HOME/.local/bin:$PATH"
CLAUDE_OK=0
if command -v claude >/dev/null 2>&1; then
  CLAUDE_OK=1
  info "Found: $(claude --version 2>/dev/null || command -v claude)"
else
  info "Claude Code is not installed yet. Install it by pasting this into Terminal:"
  info ""
  info "    $CLAUDE_INSTALL_CMD"
  info ""
  info "Then log in once by typing: claude"
  info "Official instructions: $CLAUDE_SETUP_URL"
fi

# ---- 8. Readiness report ---------------------------------------------------

step "Checking what OpenMontage can do on this Mac"
if [ "$DRY_RUN" -eq 1 ]; then
  info "[dry-run] would run (in $REPO_DIR, with .venv/bin on PATH): .venv/bin/python -c <readiness report from registry.provider_menu_summary()>"
else
  (
    cd "$REPO_DIR"
    PATH="$REPO_DIR/.venv/bin:$PATH" .venv/bin/python - <<'PY'
import contextlib, io

from tools.tool_registry import registry

noise = io.StringIO()
with contextlib.redirect_stdout(noise):
    registry.discover()
    summary = registry.provider_menu_summary()

print("")
print("  What's ready:")
for cap in summary.get("capabilities", []):
    name = cap["capability"].replace("_", " ")
    if cap.get("configured", 0) > 0:
        providers = ", ".join(cap.get("available_providers", []))
        print(f"    {name:<28} ready ({providers})")
    else:
        print(f"    {name:<28} not configured")

print("")
print("  Video assembly engines:")
for runtime, ok in summary.get("composition_runtimes", {}).items():
    print(f"    {runtime:<28} {'ready' if ok else 'not available'}")
PY
  ) || warn "Could not produce the readiness report; see the log above."
fi

# ---- Next steps ------------------------------------------------------------

echo ""
echo "==== Setup finished ===="
[ "$CLAUDE_OK" -eq 0 ] && echo "First install Claude Code (see the command above). Then:"
echo ""
echo "  1. Start OpenMontage: double-click scripts/OpenMontage.command in Finder,"
echo "     or run: $REPO_DIR/scripts/openmontage.sh"
echo "  2. Paste a starter prompt, for example:"
echo "     \"Make a 45-second animated explainer about why the sky is blue\""
echo "  3. Finished videos land in: $REPO_DIR/projects/<name>/renders/"
echo ""
echo "Log saved to $LOG_FILE"
