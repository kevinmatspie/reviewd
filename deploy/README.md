# deploy — run reviewd as a macOS LaunchAgent

Templates for running `reviewd watch` continuously under launchd, as a
per-user **LaunchAgent** (the `gui/<uid>` domain). The account that runs it
must be GUI-logged-in; this is intentionally not a system LaunchDaemon.

| File | Installed to | Purpose |
|------|--------------|---------|
| `run.sh` | `~/.config/reviewd/run.sh` (755) | Wrapper: sets PATH, loads secrets, execs `reviewd watch -v --review-existing` |
| `secrets.env.template` | `~/.config/reviewd/secrets.env` (600) | Auth tokens, sourced by `run.sh` |
| `com.reviewd.daemon.plist` | `~/Library/LaunchAgents/<label>.plist` | launchd job (`__HOME__` is substituted at install) |
| `install.sh` | — | Installs the above and (re)loads the agent |

`config.yaml` (watched repos, `log_file`, poll interval) is **not** templated
here — start from `src/reviewd/config.example.yaml`.

## Install

Run **as the account that will run the daemon**:

```bash
./deploy/install.sh                      # label com.reviewd.daemon
./deploy/install.sh --label com.spie.reviewd   # custom label
```

Then fill in `~/.config/reviewd/secrets.env`, create
`~/.config/reviewd/config.yaml`, and restart:

```bash
launchctl kickstart -k gui/$(id -u)/<label>
```

Re-running `install.sh` refreshes `run.sh` and the plist and restarts the
agent; it never overwrites an existing `secrets.env` or `config.yaml`.

## Why `--review-existing`

On every boot the daemon sweeps open PRs and reviews any that haven't been
reviewed at their current commit — so PRs opened, or pushed to, while the
daemon was offline get picked up on restart. It's idempotent: PRs already
reviewed at their current HEAD are skipped, so this is safe under launchd's
`KeepAlive` restarts. Drop the flag from `run.sh` to only review PRs that
appear after startup.

## Operate

```bash
launchctl list | grep reviewd                    # status
launchctl kickstart -k gui/$(id -u)/<label>      # restart
launchctl bootout   gui/$(id -u)/<label>         # stop/unload
tail -f ~/Library/Logs/reviewd/reviewd.log       # reviewd's own logs
tail -f ~/Library/Logs/reviewd/launchd.log       # launchd-captured stdout/stderr
```

Controlling another account's agent over SSH needs
`sudo -u <account> launchctl ... gui/<their-uid>/<label>`, and that account
must be GUI-logged-in.
