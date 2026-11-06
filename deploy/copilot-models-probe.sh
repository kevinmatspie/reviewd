#!/bin/zsh
# Lists which Copilot models the daemon account's COPILOT_GITHUB_TOKEN can use.
# Run as the daemon account (e.g. mobiletest). An unavailable model fails immediately
# at no cost; each available model uses one premium request. Never prints the token.
#
# Usage: copilot-models-probe.sh [model ...]   (defaults to the GPT/Claude candidates below)

set -u
SECRETS=${SECRETS:-$HOME/.config/reviewd/secrets.env}
export COPILOT_HOME=${COPILOT_HOME:-$HOME/.config/reviewd/copilot}
export COPILOT_AUTO_UPDATE=${COPILOT_AUTO_UPDATE:-false}
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
unset GH_TOKEN GITHUB_TOKEN

TOKEN=$( set -a; source "$SECRETS" >/dev/null 2>&1; print -rn -- "${COPILOT_GITHUB_TOKEN:-}" )
if [[ -z "$TOKEN" ]]; then
  print -r -- "COPILOT_GITHUB_TOKEN is not set in $SECRETS"; exit 1
fi
export COPILOT_GITHUB_TOKEN=$TOKEN

MODELS=("$@")
(( ${#MODELS} )) || MODELS=(gpt-6.1-sol gpt-6-sol gpt-6-luna gpt-6-astra gpt-5.6-sol gpt-5.5 claude-sonnet-5.5 auto)

print -r -- "user=$(whoami) copilot=$(copilot --version 2>/dev/null | head -1)"
cd "$COPILOT_HOME" || exit 1
for m in $MODELS; do
  out=$(print -r -- 'Reply with exactly: PONG' | copilot -s --no-color --no-custom-instructions --no-ask-user \
    --available-tools=view --model "$m" 2>&1 | head -3 | tr '\n' ' ')
  if [[ "$out" == *PONG* ]]; then
    print -r -- "AVAILABLE    $m"
  else
    print -r -- "unavailable  $m  (${out[1,140]})"
  fi
done
