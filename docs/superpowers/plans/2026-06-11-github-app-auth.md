# GitHub App Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Planned, not started. Two design decisions below are still **OPEN** — confirm them before implementing.

**Goal:** Authenticate the GitHub provider as a **GitHub App installation** instead of a fine-grained PAT, so the only durable secret is a non-expiring private key. reviewd mints short-lived installation tokens on demand and auto-refreshes them — eliminating the ≤366-day PAT rotation that silently broke polling (401s on `spie-dev/mobile-tests` + `mobile-services`, June 2026).

**Why:** Fine-grained PATs cap at 366 days (org policy may be shorter) and cannot be refreshed — only regenerated. A GitHub App installation token is valid 1 hour and is re-minted from the private key automatically, so there is nothing to rotate on a schedule.

**Architecture:** `GithubConfig` becomes a union — either a static `token` (existing behavior, kept for backward compat) **or** App credentials (`app_id` + private key + optional `installation_id`). `GithubProvider` holds the App creds and, on each request, ensures a valid installation token in its `Authorization` header — minting/refreshing under a lock when within ~5 min of `expires_at` or on a 401. The JWT (RS256, ~10 min life) is signed with the private key and exchanged at `POST /app/installations/{id}/access_tokens`. The provider cache key switches from the token string to `('github-app', app_id, installation_id)`.

**Tech Stack:** Python 3.12+, `httpx`, SQLite via stdlib. New dependency for RS256 signing (see OPEN DECISION 2). Project conventions in `CLAUDE.md`: Google style, single quotes for strings, double quotes for messages, no broad except, no docstrings/comments, tests only when explicitly asked, commits only when explicitly asked.

**Project conventions deviating from this skill's defaults:**
- **No new tests written** unless explicitly asked. Verification is manual (see Test Plan).
- **No mid-task commits.** Each task ends with a verification step; the user directs when to commit.

---

## OPEN DECISIONS (confirm before implementing)

### Decision 1 — Private key storage
- **(A) PEM file path (recommended).** Config field `private_key_path` points at a `.pem` on disk (e.g. `~/.config/reviewd/reviewd-app.pem`, `chmod 600`). Cleanest for a multiline RSA key; no newline escaping. Fits the existing secrets dir.
- (B) Inline via env var. Whole PEM in `secrets.env` as `GITHUB_APP_PRIVATE_KEY`, referenced as `${...}`. One-file secrets, but multiline keys in env are fiddly.
- **Recommendation: A.** (Plan below assumes A; supporting B as well is trivial — accept either `private_key_path` or `private_key`.)

### Decision 2 — JWT (RS256) signing dependency
- **(A) PyJWT[crypto] (recommended).** Adds `pyjwt` + `cryptography`. ~5 lines to mint the JWT. Standard, least chance of a crypto bug.
- (B) Hand-rolled with `cryptography` only. Adds 1 dep. ~15 lines of manual RS256. Keeps dep count minimal.
- **Recommendation: A** for safety; B is acceptable given reviewd's dependency-light ethos. Plan below assumes A.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Add `pyjwt[crypto]` dependency (Decision 2A) |
| `src/reviewd/models.py` | Modify | Extend `GithubConfig`: add `app_id`, `installation_id`, `private_key`/`private_key_path` (all optional); keep `token` optional |
| `src/reviewd/config.py` | Modify | `_parse_github_config` accepts App fields; resolve env vars / read PEM file; `get_provider` cache key for App mode |
| `src/reviewd/providers/github.py` | Modify | App-token mint/refresh with lock; per-request bearer; 401 → one forced refresh + retry |
| `src/reviewd/config.example.yaml` | Modify | Document the App-mode config block |
| `README.md` | Modify | Brief "GitHub App (recommended for service accounts)" auth section |

No changes to BitBucket, commenter, daemon, state, or the ABC — auth is fully encapsulated in the provider.

---

## GitHub-side setup (one-time, manual — prerequisite, not code)

- [ ] Create a GitHub App owned by `spie-dev` (Settings → Developer settings → GitHub Apps → New).
  - Permissions: **Pull requests: Read & Write**, **Contents: Read**, **Metadata: Read** (mandatory). Add Commit statuses/Checks: Read only if later needed.
  - Webhook: **uncheck Active** (reviewd polls; no webhook).
- [ ] Generate a private key → download `.pem`. Record the **App ID**.
- [ ] Install the App on `spie-dev`, selecting `mobile-tests` + `mobile-services` (or all repos). Record the **Installation ID** from the install URL (`…/installations/<ID>`), or rely on auto-discovery (Task 3).
- [ ] Place the `.pem` at `~mobiletest/.config/reviewd/reviewd-app.pem`, `chmod 600`, owner `mobiletest`.

---

## Implementation Tasks

### Task 1 — Extend `GithubConfig` (models.py)
- [ ] Make `token: str | None = None`. Add `app_id: str | None = None`, `installation_id: str | None = None`, `private_key: str | None = None` (the PEM contents, resolved by config.py whether from file or env).
- [ ] Add a helper/property `is_app` = `app_id is not None and private_key is not None`.
- **Verify:** `uv run python -c "from reviewd.models import GithubConfig; GithubConfig(app_id='1', private_key='x')"`.

### Task 2 — Parse + resolve config (config.py)
- [ ] `_parse_github_config`: if `token` present → static mode (unchanged). Else if `app_id` present → App mode: resolve `app_id`, `installation_id` (optional) via `_resolve_env_vars`; load the key from `private_key_path` (read file) or `private_key` (env). Raise a clear `SystemExit`/`ValueError` if neither token nor app creds present.
- [ ] `get_provider`: for App mode, cache key `('github-app', config.app_id, config.installation_id)`; for static mode keep `('github', config.token)`.
- **Verify:** load a config with an App block; confirm `resolve_github_config` returns populated App fields and the PEM contents are present.

### Task 3 — App-token mint/refresh in `GithubProvider` (providers/github.py)
- [ ] `__init__`: if `config.is_app`, store `app_id`, `installation_id`, `private_key`; build the `httpx.Client` **without** a static `Authorization` header (set per-request). If static token, behavior unchanged.
- [ ] `_app_jwt()`: build RS256 JWT — claims `iat` (now-60s clock skew), `exp` (now+9min), `iss` = app_id — signed with the private key (PyJWT).
- [ ] `_discover_installation_id()` (only if `installation_id` is None): `GET /app/installations` with JWT auth, pick the single installation (error if 0 or >1 — user must specify).
- [ ] `_installation_token()`: thread-safe (lock). If cached token exists and `expires_at` > now + 5 min, return it. Else `POST /app/installations/{id}/access_tokens` with `Authorization: Bearer <JWT>`; cache `token` + parse `expires_at`.
- [ ] `_request` / `_request_raw`: in App mode, set `Authorization: Bearer <installation token>` before the call. On a `401`, force one refresh (clear cache) and retry once before raising — covers clock skew / early invalidation.
- **Verify:** with real App creds, `uv run python -c "...GithubProvider(cfg).list_open_prs('spie-dev/mobile-tests')"` returns PRs; log shows a token mint then reuse on a second call (no re-mint within the hour).

### Task 4 — Docs
- [ ] `config.example.yaml`: add a commented App-mode block alongside the existing `token` example.
- [ ] `README.md`: short subsection under GitHub auth — "GitHub App (recommended for service accounts): no token expiry to manage."
- **Verify:** example YAML parses; README renders.

---

## Config shape (target)

```yaml
# Static PAT (still supported, unchanged):
github:
  token: ${GITHUB_TOKEN}

# OR GitHub App (new):
github:
  app_id: "123456"
  installation_id: "78901234"        # optional — auto-discovered if omitted and only one install
  private_key_path: ~/.config/reviewd/reviewd-app.pem
  # or, with Decision 1B:
  # private_key: ${GITHUB_APP_PRIVATE_KEY}
```

---

## Test Plan (manual)

1. **Backward compat:** an existing `token:` config still authenticates (no regression).
2. **App happy path:** App config lists open PRs on both repos; posting a review still works (installation token has PR write).
3. **Refresh:** force a near-expiry state (or wait >55 min) and confirm a fresh mint; confirm reuse within the hour (one mint, many requests).
4. **401 recovery:** temporarily feed a bad cached token; confirm one forced refresh + retry recovers instead of bubbling a 401.
5. **Missing/garbled key:** clear error message, not a stack trace.

---

## Deployment (mobiletest)

- [ ] Place `.pem` (`chmod 600`, owner mobiletest); add the `github:` App block to the global config; remove/keep `GITHUB_TOKEN` (unused once migrated).
- [ ] Pull the merged code into the editable-install clone.
- [ ] Restart the agent: `sudo launchctl kickstart -k gui/502/com.spie.reviewd`.
- [ ] Tail the log: clean boot summary, GitHub repos polling, no 401s.

---

## Related context

- Provider/auth flow facts and the credential-keyed provider cache live in `src/reviewd/config.py` (`get_provider`, `_provider_cache`) and `src/reviewd/providers/github.py`.
- Deployment specifics: editable install under `mobiletest`, LaunchAgent `com.spie.reviewd` in `gui/502`, secrets in `secrets.env` loaded by `run.sh`. See memory `project_spie_deployment`, `auth_conventions`, `feedback_launchagent_only`.
- This is independent of the open prompt/scan PR (`feat/upstream-prompt-and-scan`, PR #5) and the still-owed clean-401-error change.
