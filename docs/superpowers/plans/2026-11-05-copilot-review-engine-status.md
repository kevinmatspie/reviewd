# Status: Copilot Review Engine, Phases 0–6 (Mac mini, 2026-11-05)

Phases 0–6 are done on `feat/copilot-review-engine`, and the branch is pushed. Phase 7 (evaluation and rollout) needs Kevin. Nothing touched the live daemon, `main`, PyPI, or any real PR.

## Before starting

- **Checkout:** I worked in `~kevinm/repos/git/reviewd`, not a fresh `reviewd-dev` clone. Its uv tool receipt shows it is `kevinm`'s editable install (a stale v0.6.3), not `mobiletest`'s live clone. Kevin's uncommitted doc edits were also here.
- **Live daemon:** Not verified. No `reviewd` process was visible, and I couldn't read `mobiletest`'s plist or logs (permission denied, no sudo). It may be down. I did not touch it.
- **Merged `main`:** Local `main` at `904e268` is merged into the branch: watch_paths, fork-safe head checkout, repo-level skip_severities, json-repair, the revised legacy prompt, and Claude `--effort high`. **`main` itself is not pushed:** `904e268` exists only on the mini's local `main` and in this branch.
- **Mistaken commit:** `fbb94fb` accidentally committed the untracked `docs/superpowers/plans/2026-06-11-github-app-auth.md` (`git add docs`). It is untracked again in `bd692fb` and still on disk. It contains no secrets, but it remains in the pushed branch history. Removing it from history would need a force-push, so that's your call.

## Phase 0 spike results

Full detail is in the spec, "Spike results".

**The first flag set failed the read-only check:**

- The allowlisted `git grep -O'<cmd>'` ran arbitrary commands, and `curl` through it fetched example.com **with `--sandbox` on**.
- `cat ~/.ssh/known_hosts` was approved by Copilot's tool layer, and only the sandbox stopped it.
- A user-level `~/.copilot` hook ran despite the flags.

I stopped. **You chose option A: no shell for Copilot.**

**Option A's flags passed:** `--sandbox --disallow-temp-dir --disable-builtin-mcps --no-custom-instructions --secret-env-vars=COPILOT_GITHUB_TOKEN --deny-tool=write --available-tools=view,rg,glob`, with the prompt on stdin.

- **Tools available:** only `view`, `rg`, and `glob`, and no `canary` MCP server.
- **Blocked:** outside reads, `~/.ssh`, `/etc`, and `$TMPDIR`. The transcript shows these as failed tool calls.

**Other answers:**

- **Model IDs:** `gpt-6.1-sol`, `gpt-6-sol` (the default), `gpt-6-luna`, `gpt-6-astra`, and `gpt-5.6-*`, plus others. `--reasoning-effort none…max` is supported.
- **Output:** With `-s`, stdout is the final answer only. Copilot sometimes returns unfenced JSON, which `extract_json`'s raw-object fallback handles.
- **Usage file:** JSON with `totalPremiumRequestCost` and per-model calls and tokens. It still lands with `--disallow-temp-dir` on.
- **Auth:** Not captured. A bogus `COPILOT_GITHUB_TOKEN` still authenticated, even with an empty `COPILOT_HOME`, because `kevinm`'s stored login (likely in the keychain) wins. **The token type, its expiry, and the auth-failure text are deferred to Phase 7**, under `mobiletest` with no stored login.
- **Q7 (`--sandbox` for `mobiletest`):** Deferred to Phase 7. Note that `mobiletest` is in the `admin` group.
- **Cost:** The spike used 7 Copilot sessions. Counting the `invoke_cli` check and the 2-pass smoke test, this session used about 12 premium requests on your seat (including 2 symlink tests after the code review).

## Tests and lint

- **Tests:** 62 at the original base, then 87 after merging `main`, now **124 passed**. The 37 new tests are the plan's approved tests. One was rewritten for option A: `test_copilot_command_is_read_only` now asserts the no-shell flags and the stdin prompt.
- **Lint:** All new files pass `ruff check` and `ruff format --check`. The repo-wide lint baseline is unchanged: the same two E501 errors (`commenter.py:535`, `providers/github.py:188`) and the same three files `ruff format` would rewrite. For `commenter.py`, I confirmed the formatter's suggestions are all pre-existing.

## Smoke test (Task 13)

**Setup:**

- **Target:** `scan main --engine pipeline` on a scratch clone of reviewd (`~/reviewd-smoke/repo`), on a branch with one planted regression. `scope.file_in_scope` drops the `/` boundary check.
- **Context:** A second reviewd clone stood in for `mobile-services`.
- **Model:** `gpt-6-sol` for both passes.

**Results:**

- **Run time:** 36 s total, with find ≈ 21 s and verify ≈ 15 s.
- **Usage:** **2 premium requests per review** (1 per pass). The find pass used 5 model calls and about 85k input tokens; the verify pass used 6 calls and about 61k.
- **Plumbing:** The context worktree was created, passed via `--add-dir`, and removed. The summary shows `**Context:** smoke-context@main (904e268)`.
- **Review quality:** It caught the planted regression. It traced the effect to `_collect_eligible_prs` and noticed the existing boundary test.

Output tail:

```
### 🟡 Suggestion (1)

- **Sibling directories trigger out-of-scope reviews** — `src/reviewd/scope.py` (line 11)
  When watch_paths contains `Postman` and a PR changes only `PostmanX/thing.json`, this returns true. `_collect_eligible_prs` then queues a PR whose pathspec-filtered diff contains no watched changes; the review can still be posted and marked complete. The existing boundary test explicitly requires this path not to match.

**Bottom line:** Restore directory-boundary matching so sibling paths cannot trigger out-of-scope reviews.

**Context:** smoke-context@main (904e268)

*Automated review by gpt-6-sol in 36s. Findings are AI-generated and may not be accurate.*
```

The smoke test is a plumbing check on a toy change, not evidence of quality on the mobile apps. That's what the Phase 7 evaluation is for.

**Not yet exercised end to end:** the non-local path the daemon and `reviewd pr` use. That covers:

- `create_worktree` checking out the PR head SHA
- `run_pipeline` with `origin/<dest>` as base, on a detached HEAD
- `_filter_inline_findings_by_diff` against a real provider
- a context repo fetched from a real remote

`scan` (the plan's smoke test) uses the local path. The unit tests cover the pieces, but **the first `reviewd pr <repo> <id> --dry-run --engine pipeline` in Phase 7 is the first real run of that path.** Watch that one closely.

## First real runs on the apps (2026-11-05 evening, as `kevinm`, dry run)

These were the first end-to-end runs of the non-local path:

- the PR worktree at the head SHA
- `origin/<dest>` as the base
- a real Bitbucket provider
- `mobile-services@main (8a663fa)` as context from `~kevinm/repos/context/mobile-services`

Both completed. All worktrees were cleaned up afterwards. Logs are in `~/reviewd-eval/logs/`.

| PR | Pipeline (gpt-6.1-sol, find → verify) | Legacy (claude-sonnet-5-5, `--effort high`) |
|---|---|---|
| Android #625 (MOB-2102, substitute/journal answers, Room 19→20) | 126 s, 2 premium requests (find: 10 model calls, ~659k input tokens). **1 suggestion:** updating a migrated report replaces the row without `presentationComments`, and the email query drops them, so previously saved comments are lost. It traced the DAO, view models, report sending, and the backend email contract. | 73 s, **0 findings.** The summary noticed old comments are no longer sent but called it "appears intentional". |
| iOS #427 (MOB-2075, iPad Share button) | 325 s, 2 premium requests (find: 29 model calls, **~2.2M input tokens**). **1 suggestion:** six 75-point toolbar buttons about 72 points apart on a ~430-point iPad toolbar overlap, and "Request Paper" falls partly outside its touch area. | 93 s. **3 findings:** removing `UIStatusBarHidden` from the shared Info.plist changes the iPhone launch and loading layout (a real cross-device regression the pipeline missed); unrelated Xcode project and scheme churn; and good tests. It missed the toolbar overlap. |

**Observations for Phase 7 tuning (not acted on):**

- **Few candidates.** The find pass produced only **one candidate per PR**, even though it's told to report every supportable issue. Each engine caught a real regression the other missed. Options to evaluate:
  - `review.find.args: [--reasoning-effort, high]`
  - a find-prompt step that lists every changed file, plist and storyboard included, and assesses each one
  - asking for a minimum sweep summary per changed file
- **Token use.** Premium-request cost is flat at 1 per pass. Token use varies widely, from 659k to 2.2M input tokens on the find pass. Watch this if Copilot billing moves to AI credits (`totalNanoAiu` is in the usage file).
- **Expired token in `kevinm`'s pilot secrets.** The legacy runs first failed with Claude's "Not logged in · Please run /login" because the `CLAUDE_CODE_OAUTH_TOKEN` in `kevinm`'s `~/.config/reviewd/secrets.env` appears expired. The eval runner now unsets it and uses Kevin's stored Claude login. This doesn't affect `mobiletest`'s live token. The run also showed the message wasn't classified as an auth error, so `_AUTH_ERROR_PATTERN` now includes it (`3a0f1cc`).

## Evaluation: shipped regressions replayed (2026-11-06, as `kevinm`, dry run)

Kevin supplied three shipped regressions. Agents traced each one to the change that introduced it. Each change was rebuilt as `replay` (head) over `replay-base` (pre-merge base) in a scratch clone and reviewed with `scan` by both engines (`~/reviewd-eval/replay.sh`, logs in `~/reviewd-eval/logs/replay-*`). The context was `mobile-services@main` as of today, not as of the PR.

| Case | Introducing change | Pipeline (gpt-6.1-sol) | Legacy (claude-sonnet-5-5, `--effort high`) |
|---|---|---|---|
| **MOB-1869** (Android paging arrows) | PR #536 `d5a17b0a..c7acd173`, 15 files: the `if (event == null)` guard in `EventDetailPagingFragment.displayContent()` | ✅ **Critical, inline, exact line.** 7 findings + 1 edge case; 6 min. | ✅ **Critical, inline, exact line plus fix.** 4 findings; 51 s. |
| **MOB-1936** (iOS session report Save button hidden) | `MOB-1785` branch `3294d684..01d81b5a`, 32 files: IB shrank SAVE to 36pt while its 53pt title inset (an unchanged line) remained | ✅ **Exact cause** ("SAVE is only 36 points wide but retains a 53-point left title inset"), but as a suggestion, **finding 19 of 19**. 11 min. | ❌ Missed. 7 findings, all on the recommendations logic. 3 min. |
| **MOB-2079** (iOS People search empty after reload) | MOB-1889 merge `49d052e0..8ee822b2`, 46 files, ~337k-char diff: `DETACH symposiumdb` before close invalidates the People screen's kept prepared statement (SQLITE_MISUSE) | ❌ Missed. It read the full diff (10 findings are on hunks reachable only through `change.diff`) and reported 27 findings + 1 edge case in the same update/reload code, but didn't connect the detach to the stale statement. 14 min. | ❌ Missed. 8 findings. 6 min. |

With last night's open PRs, the pipeline caught **2 of 3** shipped regressions plus the Android #625 data-loss issue that legacy dismissed. Legacy caught **1 of 3** plus the iOS #427 status-bar regression. Each engine still finds things the other misses. Cost was a flat 2 premium requests per pipeline review. Time ranged from 2 to 14 min, against a 30-minute target.

**Tuning signals (not acted on):**

- **Noise scales with PR size.** The pipeline reported 1, 1, 7, 19, and 27 findings, against legacy's 3, 0, 4, 7, and 8. A real catch can end up last (MOB-1936). Options to consider:
  - order summary findings by severity and risk category
  - cap the summary and fold the rest into a collapsed list
  - raise the verifier's bar for `confirmed`
- **Candidate counts swing widely** with PR size (1 to 28). The find pass is thorough on big PRs and terse on small ones.
- **MOB-2079-class bugs** (cross-file SQLite or resource-lifetime interactions) are missed by both engines. Per-repo `instructions` naming fragile areas could help, e.g. "prepared statements outlive DB reloads; check `ATTACH`/`DETACH`/close against cached `sqlite3_stmt`s".
- **Real issues worth a look?** Several MOB-2079 pipeline findings describe possibly real issues in code that has since shipped, e.g. "Failed update checks can delete the live event database", delta search updates crossing ID namespaces, and an unexecuted ZoneDistance insert (which legacy also flagged). They were not verified against current code. A human should triage them before anyone files tickets.

**Bugs the evaluation found in the pipeline, fixed in `e47454b`:**

- **Context worktree collision.** `ctx-<slug>-pr-<id>` was shared by concurrent reviews of the same PR (here, two `scan`s with PR id 0; in production, the daemon plus a manual `reviewd pr`). The first review to finish deleted it under the other, whose verify pass then failed. That discarded a 22-candidate, 4.1M-token find pass, which is the concrete cost of the "verify failure discards find" open item. Each checkout now gets a unique suffix. The legacy PR worktree `pr-<id>` has the same pre-existing collision risk; flagged, not changed.
- **A diff over the cap lost whole files.** The cut fell at a file boundary, so a 198k-char file diff (the one with the bug) was dropped, and with no shell the model couldn't see what changed in it. The full diff is now written to `$XDG_CACHE_HOME/reviewd/changes/<run>/change.diff` (outside the temp directory), passed to both passes via `--add-dir`, and removed afterwards. The embedded part is cut at a hunk boundary. A crash can orphan a change directory; these are small files, and there's no sweep yet.

**Decisions (Kevin, 2026-11-06):**
- **Go live** with `engine: pipeline` on both apps, auto-approve off (part C).
- **Possible bugs in shipped code:** the non-target findings from the three replays are written up for a separate triage session in `~kevinm/reviewd-eval/shipped-code-findings.md`. It covers 11 issues both engines reported independently, plus a few high-impact pipeline-only claims, with triage instructions. None have been verified.

**Incident: Kevin's dev clones were fast-forwarded.** The first eval runs (2026-11-05 16:08) used a config copied from Kevin's, pointing at his dev clones. reviewd's `_sync_project_config` runs `git pull --ff-only` on the configured repo path, so it fast-forwarded iOS `v14.1.3` (`76e26b52` → `14d2bde8`) and Android `master` (`fae3316f` → `54137899`). Both working copies were clean, so no work was lost. The eval now uses its own clones under `~/reviewd-eval/clones/`. To undo: `git -C <clone> reset --hard <old sha>`.

## Deviations from the plan, and why

1. **Option A (no shell)** replaces the `READ_ONLY_GIT` shell allowlist, per the spike and your decision.
   - **Copilot runner:** `READ_ONLY_TOOLS = ('view', 'rg', 'glob')` and a `'pipe'` stdin prompt mode, which avoids the 1 MB `ARG_MAX` limit and keeps PR content out of `ps`.
   - **Change context from Python:** A new `collect_change` builds the commit list and the full diff, resolving the merge-base once. The diff is capped at 200k characters, with `--stat` plus a "read these files" note past the cap.
   - **Prompts:** Both prompts embed the change in a fence longer than any backtick run in it.
2. **`watch_paths` is honored by the pipeline,** as a pathspec on every git call. The plan predates `watch_paths`.
3. **Shared prompt text was lifted from `904e268`'s legacy prompt,** not the plan's older copy. The legacy prompt output is byte-identical before and after the refactor, verified in 13 configurations.
4. **`invoke_cli` writes stdin on its own thread,** started after the readers, so a large prompt can't deadlock or block past the timeout.
5. **The Copilot subprocess environment drops tokens.** It removes `GH_TOKEN`/`GITHUB_TOKEN`, which would otherwise authenticate Copilot as the polling PAT when `COPILOT_GITHUB_TOKEN` is unset. It also removes `BB_WORKSPACE_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, and `ANTHROPIC_API_KEY`.
6. **Small additions:**
   - Invalid `engine` values and context refs starting with `-` fail at config load.
   - The backoff check also runs in `_collect_eligible_prs`, so backed-off PRs don't cost a `list_pr_files` call each poll.
   - `skip_severities` also filters edge cases.
   - Copilot usage is logged as one compact line.
7. **The plan's "one real legacy `reviewd pr --dry-run`" became a direct `invoke_cli` call** against the real Claude and Copilot CLIs. Both returned parsed JSON, which exercised the new threading. A full `reviewd pr` would have needed a configured repo and real PR access.

## Security and reliability review

I reviewed the branch myself as I went, then had an independent review agent read every changed source file. It reviewed the files as they stand, not the branch diff, because it had no shell.

**Fixed:**

- **Empty-message failures recorded as success.** An exception whose `str()` is empty (e.g. `httpx.ReadTimeout`) was recorded as `success`, so it was never retried or backed off. The daemon now records `str(e) or type(e).__name__`. This affects both engines.
- **Context worktree leaks.** If a second context checkout raised, or a fetch hung past its 120 s timeout, the earlier worktrees leaked. Contexts are now built inside the `try`. A fetch timeout means "stale context", and each cleanup is guarded individually.
- **Paths with spaces.** Git appends a tab to `+++ b/<path>` when the path contains a space, so every finding in such a file was demoted from inline. Fixed and checked against a real diff.
- **Reader-thread decode errors.** Undecodable output bytes could kill a reader thread, and the CLI would then block until the timeout. `Popen` now uses `errors='replace'`. This affects both engines.
- **Auth message.** The `CLIAuthError` message no longer names `COPILOT_GITHUB_TOKEN` for every CLI.
- **Missing read-only flags.** If a `cli_defaults.copilot` override drops `--available-tools`, a warning is logged that the run is not read-only.

**Checked, not an issue:**

- **Symlink escape from the PR worktree:** Tested live with option A's flags; blocked by `view`, `rg`, and `glob`. See the spec.
- **Injection and parsing:** Git option injection (refs, base refs, `--no-ext-diff`, the sanitized slug), prompt fencing, and `.format` brace safety are fine.
- **Logic:** The backoff indexing and give-up count, the shutdown path, the schema migration, the process-group kill, the stdin writer, and verdict merging are correct.
- **Legacy path:** Unchanged apart from the stated differences.

**Open, for you to decide:**

- **Environment filtering.** The Copilot environment is still built by removing a fixed list of token names. Any other secret variable in the daemon's environment (e.g. a differently named Bitbucket token) passes through. Copilot has no shell, so the model can't read it, but hooks and the CLI itself can. Passing only allowed variables is stronger, but it could break Copilot's keychain auth or the MDM TLS setup. I'd decide this during the Phase 7 setup as `mobiletest`, once the exact variables Copilot needs are known.
- **Verify failure discards the find pass.** Two verify failures throw away a completed find pass (the spec's "post nothing" rule). Under backoff, one bad commit can cost up to 4 find passes plus up to 8 verify attempts in premium requests. An option is to cache the find output per commit.
- **Broad auth-error matching.** `_AUTH_ERROR_PATTERN` also matches stdout on a non-zero exit, so review text containing "401" or "unauthorized" could be misread as an auth error, which skips the verify retry. Tighten it once Phase 7 captures the real failure text.
- **`scan` ignores `watch_paths`** (pre-existing). The pipeline supports it, but `scan` doesn't pass it, and untracked new files don't appear in a local scan's diff.

## Behavior changes that affect `legacy` repos (mobile-tests, QAdevelopment)

This is the complete list, so you can revert any you don't want.

**The two intended changes (from the plan):**

1. **Backoff:** Failed reviews back off 5/15/60 minutes and give up after the 4th failure on the same commit. A new commit, or `reviewd pr --force`, retries. Shutdown aborts aren't counted.
2. **Timeout:** `invoke_cli`'s timeout now actually fires, and on timeout it kills the whole process group. That's 600 s by default for legacy.

**Small extras I made without pausing (stop condition 3 territory):**

3. **Empty error messages:** A failure whose exception has an empty message is recorded as an error (`type(e).__name__`) instead of silently as `success`. (`daemon.py`)
4. **Output decoding:** CLI output is decoded as UTF-8 with `errors='replace'`, so stray bytes can't kill a reader thread and hang the review until the timeout. Before, the platform default encoding applied, and an undecodable byte raised. (`reviewer.py`)
5. **Auth errors:** A non-zero exit whose stderr or stdout looks like an auth failure now raises `CLIAuthError` (a `RuntimeError` subclass) with a generic message, instead of a plain `RuntimeError`. For the daemon it's still an error plus backoff. (`reviewer.py`)
6. **stdin:** The prompt is written on its own thread. This only affects stdin-mode CLIs (Codex); Claude and Gemini take the prompt as an argument, as before.
7. **Codex:** The default sandbox is now `read-only` (in the spec). Neither SPIE repo uses Codex.
8. **CLI help:** The `--cli` help text says "(legacy engine)", and `copilot` is a new choice. No behavior change.

## Phase 7: what Kevin needs to do

Everything in A and B happens as `mobiletest`, except step 1, which runs as `kevinm`. None of it affects the live daemon until the restart in C.

**Where GitHub comes in:** The iOS and Android code stays on **Bitbucket**. reviewd checks out each PR from Bitbucket into a local worktree and posts results through the Bitbucket API, as it does today. Copilot only reads those local files. GitHub is involved in exactly two places:
- **Copilot's own sign-in**, because Copilot is a GitHub service (step 3).
- **The `mobile-services` context repo**, which is hosted on GitHub at `git@github.com:spie-dev/mobile-services.git` (steps 6–8).

### A. Copilot (do first)

1. **Upgrade Copilot, as `kevinm`.** `mobiletest`'s Homebrew Copilot is **1.0.88**. That version has the needed flags, but not the `copilot config` subcommand, and the read-only behavior was verified on **1.0.105**. Run `~kevinm/.local/bin/brew-copilot-upgrade` from Kevin's account, e.g. `! ~/.local/bin/brew-copilot-upgrade` in a Claude session. It asks for Kevin's sudo password and runs `brew` as Homebrew's owner (`mobiletest`) via `sudo -u`. `mobiletest` can't run it, because Kevin's home is mode 750. Afterwards, `/opt/homebrew/bin/copilot --version` should show at least 1.0.105.
2. **Create a dedicated settings folder with hooks off, as `mobiletest`.** It belongs to the daemon's account. Kevin's own `~/.copilot` is untouched.
   ```zsh
   mkdir -m 700 ~/.config/reviewd/copilot
   COPILOT_HOME=~/.config/reviewd/copilot copilot config disableAllHooks true
   ```
3. **Create the token.** This is a GitHub token for Copilot's sign-in and billing only; it's unrelated to where any code is hosted. Create a fine-grained PAT at <https://github.com/settings/personal-access-tokens/new>:
   - **Resource owner:** Kevin's **personal account**. "Copilot Requests" only exists on user-owned tokens and is hidden when an organization is the owner.
   - **Repository access:** **Public repositories**, the minimum.
   - **Permissions:** the **Account** tab (not Repository) → Add permissions → **Copilot Requests**. "Copilot agent settings" is a different permission.
   - The token starts with `github_pat_`. Classic `ghp_` tokens are not supported. Add `COPILOT_GITHUB_TOKEN=…` to `~/.config/reviewd/secrets.env` (mode 600), and set a reminder for its expiry.
4. **Edit the live `~/.config/reviewd/run.sh`.** Change only these two lines; the live file differs from the template on purpose.
   ```zsh
   export COPILOT_HOME="$HOME/.config/reviewd/copilot"
   export COPILOT_AUTO_UPDATE=false
   ```
   Disabling auto-update pins the version. Upgrade deliberately, and re-run step 5 after every upgrade.
5. **Run the check:** `zsh /Users/Shared/reviewd-copilot-check.sh` (the same file as `deploy/copilot-readonly-check.sh`). It takes about 2 minutes and about 4 premium requests, and it prints no secrets. It checks:
   - the version
   - the hooks setting
   - the token type
   - the auth-failure text, which feeds reviewd's `_AUTH_ERROR_PATTERN`
   - that the token works headless
   - read-only enforcement: outside, temp-dir, and symlink reads; writes; tool names; and the sandbox, each proven by a canary
   - that the context clone fetches non-interactively

   The report is copied to `/Users/Shared/reviewd-copilot-check-report.txt` for Claude to read. **Every line must be PASS before enabling `engine: pipeline`.** A WARN that a bogus token still worked means a stored login exists and wins over the token; tell Claude.

**Status 2026-11-06:** **A and B are done.** The check as `mobiletest` (Copilot 1.0.106, `gpt-6.1-sol`) gave **19 passed, 0 warnings, 0 failed**, with the context clone at `~mobiletest/repos/context/mobile-services`. The Copilot token is on Kevin's personal GitHub account for now; see the spec's "Spike results" for the service-account path. Next: the evaluation PR list, then C.

### B. mobile-services context clone

6. **Create the deploy key:** `ssh-keygen -t ed25519 -f ~/.ssh/mobile-services-deploy -N '' -C 'reviewd mobile-services (read-only)'`. Add the `.pub` file as a **read-only** Deploy key on `spie-dev/mobile-services`; that needs repo admin.
7. **Add an SSH alias** to `~/.ssh/config`:
   ```
   Host github-mobile-services
     HostName github.com
     User git
     IdentityFile ~/.ssh/mobile-services-deploy
     IdentitiesOnly yes
   ```
8. **Clone:** `git clone git@github-mobile-services:spie-dev/mobile-services.git ~/repos/context/mobile-services`. That's the check script's default `CONTEXT_REPO`, so re-run it. Use the clone only for reviewd: reviewd creates and sweeps `.reviewd-worktrees/` inside it.

### C. Go-live (after the evaluation)

9. **Back up the state DB.** Stop the daemon, then copy `~/.local/share/reviewd/state.db` to `state.db.bak-<date>`. The new code adds an `attempts` column on first start.
10. **Put the code in place.** Switch `~/repos/git/reviewd` to this branch, or to `main` after the merge. If `json-repair` isn't installed in the tool env, run `uv tool install --reinstall --editable ~/repos/git/reviewd`. The legacy repos get backoff and the timeout fix at this point.
11. **Update `config.yaml`:**
    - `context_repos.mobile-services` with `path: ~/repos/context/mobile-services` and `ref: main`
    - a `review:` block with **`gpt-6.1-sol` for both passes** (Kevin's choice). The smoke-test re-run with it caught the same regression in 36 s for 2 premium requests.
    - `engine: pipeline` and `context: [mobile-services]` on both app repos
12. **Make sure auto-approve is off for both apps.** The global config can only turn it on (`enabled = global or project`). If an app's committed `.reviewd.yaml` sets `auto_approve.enabled: true`, that file has to change.
13. **Restart:** `launchctl kickstart -k gui/502/com.spie.reviewd`, once no review is in flight.

### Evaluation (Task 14)

This can run as `kevinm` from the dev checkout, without touching the live daemon:

- **Config:** `~/reviewd-eval/config.yaml` is Kevin's config plus the pipeline settings, with its own `state_db`.
- **Runner:** `~/reviewd-eval/run.sh <repo> <pr> legacy|pipeline` does a dry run with `--force`. Logs go to `~/reviewd-eval/logs/`.
- **Still needed:** pick the 6–8 PRs, ideally including shipped regressions traced from Jira.

## Not code, but flagged

- **Bitbucket SSH host change, November 12, 2026:** Bitbucket's SSH banner says SSH traffic moves to `ssh.bitbucket.org` on that date. reviewd's clones fetch over `git@bitbucket.org:spiemobilerepo/...`. That includes `kevinm`'s clones, and probably `mobiletest`'s `~/.reviewd/spieconferences-*`. Switch the remotes (`git remote set-url origin git@ssh.bitbucket.org:spiemobilerepo/<repo>.git`, after accepting the new host key), or add a `~/.ssh/config` `Host bitbucket.org` → `HostName ssh.bitbucket.org` mapping. Otherwise worktree fetches, and with them reviews, may fail after that date.

- **Exposed runner credentials:** The Bitbucket Pipelines runners on this machine pass OAuth client secrets and a repo access token on their command lines, readable by any local user via `ps`. Flagging only; I did nothing.

## Leftovers to clean up

- `~/reviewd-spike-1793920169/` (spike transcripts and usage files), `~/reviewd-spike-scripts/`, and `~/reviewd-smoke/` (smoke clones, config, logs, and a scratch `state.db`). Keep them until Phase 7 is done, then `rm -rf` all three.
