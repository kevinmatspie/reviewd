# Formal PR Reviews

Status: design approved 2026-05-26

## Problem

Today reviewd posts findings as scattered GitHub comments:
- N inline review comments (one per finding, via `/pulls/{id}/comments`)
- One issue comment with the summary (via `/issues/{id}/comments`)
- Optionally, a separate APPROVE review via `/pulls/{id}/reviews` when `auto_approve` gates pass

This means reviewd's findings — including critical issues — don't *count* toward branch protection. A PR with five critical findings looks the same to GitHub as a PR with none. The only way reviewd influences PR state is via auto-approve.

We want reviewd to be able to *drive* the review process on opted-in repos: a single formal review whose `event` reflects the verdict (REQUEST_CHANGES blocks the PR; APPROVE counts as an approval; COMMENT is informational).

## Goal

Let reviewd submit a single, formal GitHub PR Review per pass, with the appropriate `event`, so that:
- Critical findings actually block merge under branch protection
- Approvals count as formal approvals (parity with the existing auto-approve path)
- All findings appear as one timeline entry instead of N scattered comments

Feature is opt-in per repo. Default off — existing behavior is fully preserved.

## Non-goals

- BitBucket parity. BB has no formal review concept beyond approve; BB repos fall back to today's behavior even if the flag is set.
- Replacing the existing comment-based path. Both paths coexist; per-repo flag selects which is used.
- Multi-line inline suggestions (still single-line, same as today).
- Reading or reacting to other reviewers' reviews.

## Config

Add `formal_review: bool = False` to both `GlobalConfig` and `RepoConfig`. Per-repo overrides global. Default is `False` everywhere.

```yaml
# global default applies to all repos unless overridden
formal_review: false

repos:
  - name: mobile-app-ios
    provider: github
    repo_slug: spie/mobile-ios
    formal_review: true   # this repo: drive the review process

  - name: mobile-app-android
    provider: github
    repo_slug: spie/mobile-android
    # inherits global (false)
```

Effective value: `repo.formal_review if explicitly set else global.formal_review`. Match the existing override pattern in `config.py`.

## Behavior when `formal_review: true`

One API call: `POST /repos/{slug}/pulls/{id}/reviews` with:
- `commit_id` — the PR head SHA
- `body` — the summary text (current summary content, formatted for the review body)
- `comments[]` — inline findings, each `{path, line, body}` (or `{path, start_line, line, side, start_side, body}` if multi-line is ever supported)
- `event` — one of `COMMENT`, `REQUEST_CHANGES`, `APPROVE`

### Event selection

Evaluated in order:

| Condition | Event |
|---|---|
| `auto_approve.enabled` AND `_resolve_auto_approve` returns approved=True | `APPROVE` |
| Any finding has `severity == critical` | `REQUEST_CHANGES` |
| Otherwise | `COMMENT` |

Auto-approve gating logic (`_check_auto_approve_gates`, `_resolve_auto_approve`) is reused unchanged.

### Inline vs body

Existing `inline_comments_for` and `max_inline_comments` config still control which findings get inline placement. Findings selected for inline placement go into the review's `comments[]` array. Findings not inlined appear in the body, formatted exactly as today's summary comment formats them.

### Self-PR

The bot cannot `REQUEST_CHANGES` or `APPROVE` its own PR (GitHub returns 422). Detect this the same way the existing `approve_pr` does: catch 422, log a warning, return False. Fall back to NOT submitting the formal review at all in this case — log clearly and leave the PR untouched. (This preserves the current self-PR experience where auto-approve is silently skipped.)

### Empty review

If there are no findings at all and no summary text, do not submit a review. Match today's behavior of not posting empty content.

## State management

`REQUEST_CHANGES` is sticky on GitHub: a subsequent `COMMENT` review from the same reviewer does NOT unblock the PR. The block lifts only when the reviewer either (a) submits an `APPROVE` review or (b) dismisses the prior `REQUEST_CHANGES` review.

So on every re-review pass in formal-review mode:

1. Read prior reviewd review IDs from state DB for this `(repo, pr)`
2. For each prior review, `GET /repos/{slug}/pulls/{pr}/reviews/{review_id}` and inspect `state`. Only if `state == 'CHANGES_REQUESTED'`, dismiss via `PUT /repos/{slug}/pulls/{pr}/reviews/{review_id}/dismissals` with `{message: 'Superseded by newer reviewd review'}`. COMMENT and APPROVED reviews don't block anything, so leave them alone to keep the timeline quiet. If the GET 404s (review deleted externally) or the dismissal fails (already dismissed, permission, etc.), log at WARNING and continue — never block the new review on a failed dismissal.
3. After successful processing of a prior review (dismissed or skipped because non-blocking), remove its ID from the state DB — we don't need to keep re-checking it on future passes
4. Delete prior inline comments (existing logic in `post_review`, unchanged — inline review comments are deleted by ID just like today)
5. Submit the new review
6. Record the new review ID in state DB

### Schema

Add a new table `posted_reviews`:

```sql
CREATE TABLE posted_reviews (
    repo_slug TEXT NOT NULL,
    pr_id INTEGER NOT NULL,
    review_id INTEGER NOT NULL,
    PRIMARY KEY (repo_slug, pr_id, review_id)
);
```

Mirrors `posted_comments`. Add methods on `StateDB`:
- `record_review(repo_slug, pr_id, review_id)`
- `get_review_ids(repo_slug, pr_id) -> list[int]`
- `delete_reviews(repo_slug, pr_id)`

Migration: create table on `StateDB.__init__` if it doesn't exist, same pattern as `posted_comments`.

## Provider interface

Add to `providers/base.py`:

```python
class GitProvider:
    supports_formal_review: bool = False

    def submit_review(
        self,
        repo_slug: str,
        pr_id: int,
        body: str,
        event: Literal['COMMENT', 'REQUEST_CHANGES', 'APPROVE'],
        inline_comments: list[InlineComment],
        source_commit: str,
    ) -> int | None: ...

    def dismiss_review(
        self,
        repo_slug: str,
        pr_id: int,
        review_id: int,
        message: str,
    ) -> bool: ...
```

`InlineComment` is a small dataclass in `models.py`:

```python
@dataclass
class InlineComment:
    path: str
    line: int
    body: str
```

`GithubProvider`:
- Sets `supports_formal_review = True`
- Implements `submit_review` against `POST /repos/{slug}/pulls/{pr}/reviews`. Returns the new review's `id`, or `None` if 422 self-PR.
- Implements `dismiss_review` against `PUT /repos/{slug}/pulls/{pr}/reviews/{review_id}/dismissals`. Returns True on success, False (with warning log) on any failure.

`BitbucketProvider`:
- Leaves `supports_formal_review = False`
- Does not implement `submit_review` / `dismiss_review` (raises `NotImplementedError` from base, but commenter never calls them because of the capability check)

## Hallucinated lines

The AI sometimes references lines that aren't in the diff. Today this fails silently per-comment — each inline comment POST that 422s gets logged and skipped, but the others succeed. With a single review submission, one bad line makes the whole review 422.

Pre-filter inline comments against the diff before building the review payload:

1. Fetch the diff once at review time (we already have the PR head SHA; use `GET /repos/{slug}/pulls/{pr}/files` or parse the existing local worktree diff)
2. Build a set of `(path, valid_line_numbers)` from the diff hunks
3. Drop any inline finding whose `(file, line)` isn't in that set; log each drop at INFO level
4. Dropped findings fall back into the summary body (so they're still visible to the reviewer, just not inline)

This also incidentally improves the existing comment-based path — apply the pre-filter there too rather than relying on per-comment 422 fallback. (Yes, this is a scope nudge beyond strictly "formal reviews" — but it's the right cleanup for code we're touching, and it removes a class of silent failures the user already flagged in [[feedback_anti_hallucination]].)

## Commenter changes

`commenter.py:post_review`:

```python
def post_review(provider, state_db, pr, result, project_config, global_config, ...):
    # ... existing dedup, severity filtering, inline selection ...

    use_formal = (
        project_config.formal_review
        and provider.supports_formal_review
    )
    if use_formal:
        _post_formal_review(...)
    else:
        _post_comment_review(...)  # existing logic, extracted into helper
```

`_post_formal_review`:
1. Pre-filter inline findings against diff
2. Determine event (APPROVE / REQUEST_CHANGES / COMMENT) as above
3. Dismiss prior reviews (state DB → provider.dismiss_review)
4. Delete prior inline comments (existing logic)
5. Build review body (summary text, same formatter as today, minus the inline tally lead since they're in the same review entry)
6. Call `provider.submit_review`
7. Record review ID via `state_db.record_review`

(Critical-task sync — `_sync_critical_task` in `commenter.py` — is unaffected: it's already gated by `hasattr(provider, 'list_tasks')`, which only BitBucket implements. Formal-review is GitHub-only, so the two features never interact.)

Dry-run mode: print what the review submission would contain (event, body, inline list), mirroring `_print_dry_run` for the existing path.

## Files touched

| File | Change |
|---|---|
| `src/reviewd/models.py` | Add `formal_review` to `GlobalConfig` and `RepoConfig`; add `InlineComment` dataclass |
| `src/reviewd/config.py` | Wire `formal_review` through YAML loading + global→repo override |
| `src/reviewd/state.py` | Add `posted_reviews` table + methods |
| `src/reviewd/providers/base.py` | Add `supports_formal_review`, `submit_review`, `dismiss_review` to ABC |
| `src/reviewd/providers/github.py` | Implement `submit_review`, `dismiss_review`, set capability flag |
| `src/reviewd/providers/bitbucket.py` | No change (capability flag stays False) |
| `src/reviewd/commenter.py` | Branch on `use_formal`; extract existing path into `_post_comment_review`; add `_post_formal_review`; add diff-based pre-filter helper used by both paths |
| `src/reviewd/config.example.yaml` | Document `formal_review` flag with example |
| `README.md` | Brief mention in features + config reference |

## Test plan

This is a Python project with `tests/` already present, but per project conventions (`CLAUDE.md`: "Tests only when explicitly asked"), automated tests are NOT included in this scope. Verification is manual.

Manual verification steps after implementation:

1. **Default-off preservation** — leave `formal_review` unset on a watched repo, run a review. Confirm scattered comments + issue summary still posted exactly as before.
2. **REQUEST_CHANGES blocks the PR** — enable on test repo with branch protection requiring approving reviews. Open a PR with a planted critical issue. Run reviewd. Confirm:
   - One review entry appears in the PR timeline with the "Changes requested" badge
   - PR merge button is disabled with "Changes requested" reason
3. **Re-review dismisses prior block** — fix the critical issue, push. Re-run reviewd. Confirm:
   - Prior REQUEST_CHANGES review shows "Superseded by newer reviewd review" dismissal note
   - New COMMENT or APPROVE review posted
   - PR merge no longer blocked by reviewd
4. **APPROVE flow** — enable `auto_approve` alongside `formal_review`. Submit a clean PR. Confirm a single APPROVE review is posted (not separate summary + approval call).
5. **Self-PR safety** — open a PR as the reviewd bot user. Run reviewd. Confirm no review submitted, warning logged, no exception.
6. **Hallucinated line filter** — feed a synthetic AI result with an inline finding pointing at a line not in the diff. Confirm finding is dropped from inline and surfaced in the body, no 422.
7. **Dry-run** — run with `--dry-run` against a formal-review repo; confirm output shows the review payload preview, no API calls made.
8. **BitBucket fallback** — set `formal_review: true` on a BB repo. Confirm commenter takes the existing comment-based path with a warning logged that BB doesn't support formal reviews.

## Open questions

None. Open decisions resolved during brainstorming:

- **Critical→REQUEST_CHANGES coupling**: coupled. If a user wants to drive reviews but never request changes, they can suppress critical via existing severity config.
- **Hallucinated-line handling**: pre-filter against diff hunks. Also applied to existing comment-based path as targeted cleanup.
- **Dismissal scope**: selective — only dismiss prior reviews whose `state == CHANGES_REQUESTED`. Leave COMMENT/APPROVED reviews alone to keep the PR timeline readable.

## Risks

- **Diff fetch adds latency**: pre-filter requires the diff. We may already have it locally via the worktree — verify during implementation and use that path if so to avoid the extra API call.
- **Dismissal timeline entries**: when reviewd does need to dismiss a prior REQUEST_CHANGES review, that produces a "dismissed: superseded" entry in the PR timeline. Selective dismissal keeps this minimal — only fires when something was actually blocking — but on PRs that cycle critical→fixed→critical→fixed it can still accumulate.
- **Branch protection misconfiguration**: if the user enables `formal_review` without branch protection requiring approving reviews, REQUEST_CHANGES has no enforcement effect — it just shows up as a review status. Document this in the README.
