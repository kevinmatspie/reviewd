#!/bin/zsh
# Install reviewd as a per-user LaunchAgent on macOS.
# Run this AS the account that will run the daemon (it must be GUI-logged-in):
#   ./deploy/install.sh [--label com.reviewd.daemon]
#
# Idempotent: re-running updates run.sh + the plist and restarts the agent.
# It never overwrites an existing secrets.env or config.yaml.

set -euo pipefail

LABEL="com.reviewd.daemon"
if [[ "${1:-}" == "--label" && -n "${2:-}" ]]; then
    LABEL="$2"
fi

SRC_DIR="${0:A:h}"                       # directory this script lives in
CONFIG_DIR="$HOME/.config/reviewd"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/reviewd"
PLIST_DEST="$AGENTS_DIR/$LABEL.plist"

command -v reviewd >/dev/null 2>&1 || {
    echo "reviewd not found on PATH. Install it first (e.g. uv tool install reviewd)." >&2
    exit 1
}

mkdir -p "$CONFIG_DIR" "$AGENTS_DIR" "$LOG_DIR"

# Wrapper — always refreshed.
install -m 755 "$SRC_DIR/run.sh" "$CONFIG_DIR/run.sh"
echo "Installed $CONFIG_DIR/run.sh"

# Secrets — seed once, never clobber.
if [[ ! -f "$CONFIG_DIR/secrets.env" ]]; then
    install -m 600 "$SRC_DIR/secrets.env.template" "$CONFIG_DIR/secrets.env"
    echo "Seeded $CONFIG_DIR/secrets.env — fill in real values (chmod 600)."
else
    echo "Kept existing $CONFIG_DIR/secrets.env"
fi

if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
    echo "No $CONFIG_DIR/config.yaml yet — create one (see src/reviewd/config.example.yaml)."
fi

# Plist — substitute __HOME__ and (re)install.
sed "s|__HOME__|$HOME|g; s|<string>com.reviewd.daemon</string>|<string>$LABEL</string>|" \
    "$SRC_DIR/com.reviewd.daemon.plist" > "$PLIST_DEST"
echo "Installed $PLIST_DEST"

# (Re)load into the GUI domain for this user.
DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST_DEST"
launchctl kickstart -k "$DOMAIN/$LABEL"
echo "Loaded and started $LABEL in $DOMAIN"
echo
echo "Watch it boot:  tail -f $LOG_DIR/reviewd.log"
