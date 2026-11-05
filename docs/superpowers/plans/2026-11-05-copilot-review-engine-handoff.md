# Handoff: Copilot Review Engine (laptop → Mac mini)

Written 2026-11-05 at the end of a brainstorming and planning session on Kevin's MacBook Air. The implementation runs on the Mac mini so it keeps going when the laptop lid is closed. **A fresh session has none of the conversation that produced this.** This file, the spec, and the plan are the whole context.

## Start here

1. **Read, in order:**
   - This file.
   - `docs/superpowers/specs/2026-11-05-copilot-review-engine-design.md`: the approved design (what and why).
   - `docs/superpowers/plans/2026-11-05-copilot-review-engine.md`: the task-by-task plan (how).
2. **Follow** "Before writing any code" below.
3. **Execute the plan** in your session or with subagent driven development if you prefer, Phase 0 through Phase 6. Check in with your advisor along the way. **Stop before Phase 7.** It needs Kevin.

## Why this exists (30-second version)

- **The problem:** After switching reviewd to Sonnet 5.5, reviews on the mobile apps mostly said "Looks good." Prompt tweaks and model comparisons didn't fix it. Kevin's manual reviews through Copilot (OpenAI models) were better.
- **What the manual reviews did better:**
  - They reasoned about code *outside* the diff that the change affects.
  - They raised edge cases.
  - Most of all, they knew the backend (`spie-dev/mobile-services`, its `Api` and `Task` projects) and caught app/API contract mismatches such as int vs. string parameters.
- **Regression risk:** The mobile apps are regression-prone because of their architecture, so guarding against regressions is a primary goal.
- **Decision:** Keep reviewd's infrastructure (daemon, providers, state, worktrees, posting) and add a new per-repo `pipeline` engine:
  - A Copilot CLI find pass, then a verify pass.
  - Read-only `mobile-services` context at `main`.
  - Today's single-prompt engine stays as `legacy`, unchanged, for the GitHub repos (`mobile-tests`, `QAdevelopment`).

## Decisions already made (don't reopen)

All by Kevin on 2026-11-05. Details are in the spec.

| Topic | Decision |
|---|---|
| Rework vs. new repo | Rework reviewd; rewrite only the review logic. |
| Runner | GitHub Copilot CLI, authenticated with **Kevin's** Copilot seat for now (`COPILOT_GITHUB_TOKEN`). He's looking into a service account. |
| Passes | Find, then verify. Verify can be toggled off in config. |
| Context | `mobile-services` at `main` (configurable `ref`). No branch matching yet. Branch names will be synced with releases later. |
| Scope | `pipeline` for the Bitbucket apps `spieconferences-ios` and `spieconferences-android`. `mobile-tests` and `QAdevelopment` (GitHub) stay on `legacy`. |
| Engine selection | One daemon. Per-repo `engine: legacy \| pipeline` with a global default of `legacy`. |
| Verify failure | Retry once, then **post nothing**. The backoff then applies: 5/15/60 minutes, giving up after the 4th failure on the same commit. A new commit starts fresh. |
| Rollout | iOS and Android **together**, auto-approve **off** for the first week. Kevin will monitor. |
| Tests | Every test in the plan is approved. Don't add others. |

## Mac mini facts

These come from earlier session history (via episodic memory on the laptop) and were only partially verified. **Check each one before relying on it.**

- **Live daemon:**
  - Runs under the **`mobiletest`** macOS account via a **launchd** plist.
  - Logs: `/Users/mobiletest/Library/Logs/reviewd`.
  - Config: `~mobiletest/.config/reviewd/config.yaml`; secrets in `~mobiletest/.config/reviewd/secrets.env`.
- **Watched repos:**
  - `spieconferences-ios` and `spieconferences-android` (Bitbucket). Clones were seen under `~/repos/spiemobilerepo`.
  - `mobile-tests` and `QAdevelopment` (GitHub). Kevin confirmed these on 2026-11-05.
- **Current engine:** The live daemon currently uses the Claude CLI. Claude rejects nested sessions, which is why `CLAUDECODE` is unset in the subprocess.
- **Deploy folder:** The deploy folder is kept under git.
- **Past breakage on the mini:**
  - MDM-delivered TLS certificates once broke `git` and `pip` until env vars were set (PIP_CERT was discussed) and ownership was fixed.
  - launchd log locations caused confusion once.
  - A GitHub fine-grained PAT expired in June 2026 and broke polling with 401s on `spie-dev/mobile-tests` and `mobile-services`.
- **`mobile-services`:** Trunk-based on `main`. Projects under `src/`: `Api`, `Dashboard`, `Data`, `Migrations`, `Portal`, `Shared`, `StaffTools`, `Task`.
  - "Mobile Task" is `src/Task` (`SPIE.MobileTask.csproj`), the backend background/import jobs. That mapping is inferred.
  - DTOs and models probably live in `src/Shared` and `src/Data`.
- **Copilot CLI:** 1.0.105 on the laptop. The version on the mini is unknown; check with `copilot --version`. Flags differ between versions, which is one reason for the Phase 0 spike.

## Before writing any code

1. **Find out how the live daemon runs, and leave it alone.**
   - Run `launchctl list | grep -i reviewd`, both as `mobiletest` and with `sudo launchctl list`.
   - Read the plist's `ProgramArguments` (look under `~mobiletest/Library/LaunchAgents/` or `/Library/LaunchDaemons/`).
   - Work out which `reviewd` binary and which checkout it runs from. If it runs from an editable checkout, that checkout is **production**.
2. **Work in a separate checkout:**
   ```bash
   git clone git@github.com:kevinmatspie/reviewd.git ~/repos/reviewd-dev   # or any path that is NOT the deploy folder
   cd ~/repos/reviewd-dev && git switch feat/copilot-review-engine && uv sync
   uv run pytest -q   # expect 62 passed (baseline; see the plan's lint baseline note too)
   ```
   If `git clone` or `uv sync` fails with TLS errors, that's the MDM certificate problem above. Tell Kevin rather than disabling TLS verification.
3. **Check the tools:**
   - `which copilot uv git`.
   - `copilot --version`.
   - Confirm Copilot is authenticated for *your* user: `copilot -s -p 'reply OK'`.
   - If it isn't, stop and ask Kevin. Don't create, guess, or copy tokens.
4. **Know which user you are** (`whoami`).
   - Plan Task 0 question 7 (does `--sandbox` work for `mobiletest`) and all of Phase 7 need the `mobiletest` account.
   - If you aren't `mobiletest`, record Q7 as "deferred to Phase 7" in the spec and carry on.
5. **Run long work in tmux** (`tmux new -s reviewd-impl`), so an SSH disconnect from the laptop doesn't kill the session.

## Autonomy and stop conditions

**Do without asking:**

- Everything in Phases 0–6.
- At each checkpoint, commit to `feat/copilot-review-engine` and push that branch.
- Update the spec with spike results and the backoff wording, as the plan says.

**Stop and wait for Kevin if any of these happen:**

1. **Read-only failure:** The Phase 0 spike shows any operation that should be blocked actually succeeding: an outside read, a write, curl, `git stash`/`checkout`/`push`. Or a canary instruction/MCP file got loaded.
2. **No Copilot auth:** Copilot isn't authenticated for your user, or it needs a token you don't have.
3. **Legacy behavior would change:** A plan step would change `legacy` behavior beyond the two intended changes (the `invoke_cli` timeout fix and the failure backoff). An existing test needing an edit counts as a signal here.
4. **Plan contradicts code:** The plan contradicts the code in a way that changes the design. Small mechanical corrections (an import, a line number) are fine; note them in the commit message.
5. **Phase 7:** The evaluation and rollout need Kevin and touch production.

**Never:**

- Modify, restart, or reload the **live** reviewd: its launchd job, `~mobiletest/.config/reviewd/*`, its checkout or installed package, or its state DB.
- Merge to `main`, publish to PyPI, or bump the version.
- Post to real PRs. Use only `--dry-run` / `scan`, which never post.
- Put tokens or secrets in code, commits, logs you paste, or chat.
- Run Copilot without the read-only flags from Task 1.

## Things a fresh session is likely to get wrong

- **Lint baseline:** `main` already fails lint (two E501 errors, three files `ruff format` would rewrite). Don't reformat pre-existing code. See the plan.
- **Don't trust the "timeout" in `invoke_cli` yet:** Before Task 1 it never fires. That's the bug Task 1 fixes, and it was verified on the laptop.
- **`post_review` rebuilds `ReviewResult`:** If you forget to copy `edge_cases`/`context_refs` into the rebuilt object, they vanish silently (Task 10).
- **Import cycle:** `pipeline.py` imports `reviewer.py`, so `review_pr` must import `run_pipeline` locally inside the function (Task 9).
- **Copilot flags are provisional** until Phase 0 confirms them. If the spike disagrees with the plan, the spike wins: update Task 1 and the spec.
- **Project conventions** (`CLAUDE.md`): single quotes, no docstrings or comments, no broad `except` in new code, no Co-Authored-By trailers.
- **SPIE org rules:**
  - Least privilege.
  - No secrets anywhere.
  - Before finishing, review your changes for security (injection, secrets, unsafe input, read-only enforcement) and reliability (error handling, edge cases, tests), and report what you found.
  - Flag code that is now unused instead of silently deleting it. `REVIEW_TEMPLATE` stays; it's the `legacy` engine.

## Final report (end of Phase 6)

When Phases 0–6 are done, write a short report for Kevin. Put it in the last commit message, or in `docs/superpowers/plans/2026-11-05-copilot-review-engine-status.md`, and push. Include:

- **Phase 0 spike results:** Especially the read-only checks, the model IDs, the token type and its expiry, and the auth-failure text.
- **Test results:** Count before and after, plus lint status on new files.
- **The smoke test (Task 13):** Output tail, run time, and premium-request usage.
- **Deviations:** Anything that differed from the plan, and why.
- **Security/reliability review:** What you found.
- **Next steps:** What Kevin needs to do for Phase 7:
  - The `mobiletest` Copilot setup.
  - The deploy key and dedicated `mobile-services` clone.
  - Model IDs in the config.
  - Disabling auto-approve on the app repos.
  - Choosing the evaluation PRs.

## Untracked file on the laptop (FYI)

`docs/superpowers/plans/2026-06-11-github-app-auth.md` (the GitHub App auth plan from June) exists only on Kevin's laptop and is untracked. It's not needed for this work; context repos use a read-only deploy key instead.
