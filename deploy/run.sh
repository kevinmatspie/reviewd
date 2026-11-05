#!/bin/zsh
# Wrapper invoked by launchd. Sets up PATH, loads secrets, then execs reviewd.
# Installed by install.sh to: ~/.config/reviewd/run.sh   (chmod 755)

set -euo pipefail

# launchd does NOT inherit your shell PATH. Export everything reviewd needs:
#   - $HOME/.local/bin             — uv tool installs (reviewd lives here)
#   - $HOME/.claude/local          — claude CLI (when installed via the
#                                    official installer rather than npm)
#   - /opt/homebrew/bin            — Apple Silicon Homebrew (git, etc.)
#   - /usr/local/bin               — Intel Homebrew + npm-global claude
#   - /usr/bin:/bin                — system tools
export PATH="$HOME/.local/bin:$HOME/.claude/local:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# Load secrets from a private file. Keep it chmod 600.
SECRETS_FILE="$HOME/.config/reviewd/secrets.env"
if [[ ! -f "$SECRETS_FILE" ]]; then
    echo "Missing secrets file at $SECRETS_FILE" >&2
    exit 78   # EX_CONFIG
fi

# Auto-export every variable defined while sourcing.
set -a
# shellcheck disable=SC1090
source "$SECRETS_FILE"
set +a

# Require at least one valid auth method. claude CLI prefers
# CLAUDE_CODE_OAUTH_TOKEN over ANTHROPIC_API_KEY when both are set,
# so leaving both is fine — the token wins.
if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" && -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "Neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY set in $SECRETS_FILE" >&2
    exit 78
fi

# Avoid nested-session detection in claude CLI subprocesses.
unset CLAUDECODE

# -v for info-level logging.
# --review-existing sweeps unreviewed open PRs on every boot, so reviews missed
# while the daemon was offline (new PRs or new commits) get picked up on restart.
# It is idempotent: PRs already reviewed at their current commit are skipped.
exec reviewd watch -v --review-existing
