# Formal PR Reviews Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let reviewd submit a single formal GitHub PR Review (with `event: COMMENT` / `REQUEST_CHANGES` / `APPROVE`) on opt-in repos, so reviewd's verdict counts toward branch protection.

**Architecture:** Add a `formal_review` flag (global + per-repo). When set on a GitHub repo, commenter branches to a new path that submits one Review via `POST /pulls/{id}/reviews` with `event` derived from severity + auto-approve gates. Selective dismissal of prior `CHANGES_REQUESTED` reviews keeps the timeline quiet. A diff-based pre-filter drops inline findings on lines outside the diff hunks — applied to both new and existing paths.

**Tech Stack:** Python 3.12+, `httpx`, SQLite via stdlib `sqlite3`. Project conventions in `CLAUDE.md`: Google style, single quotes for strings, double quotes for messages, no broad except, no docstrings/comments, tests only when explicitly asked, commits only when explicitly asked.

**Project conventions deviating from this skill's defaults:**
- **No new tests written.** Per project `CLAUDE.md`: "Tests only when explicitly asked." Verification is manual via the test plan in the spec.
- **No mid-task commits.** Per project `CLAUDE.md`: "Only commit or release when explicitly asked." Each task ends with a verification step instead. The user will direct when to commit.

**Spec:** `docs/superpowers/specs/2026-05-26-formal-pr-reviews-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/reviewd/models.py` | Modify | Add `formal_review` to GlobalConfig + RepoConfig; add `InlineComment` + `ReviewEvent` |
| `src/reviewd/config.py` | Modify | YAML loading + global→repo override for `formal_review` |
| `src/reviewd/state.py` | Modify | New `posted_reviews` table + record/get/delete methods |
| `src/reviewd/providers/base.py` | Modify | Add `supports_formal_review`, `submit_review`, `dismiss_review`, `get_diff_lines` to ABC |
| `src/reviewd/providers/github.py` | Modify | Implement the three new methods; set capability flag |
| `src/reviewd/providers/bitbucket.py` | Modify | Leave capability flag False; raise NotImplementedError on new methods (inherited) |
| `src/reviewd/commenter.py` | Modify | Branch on capability+flag; extract existing path; add formal path; add diff pre-filter applied to both |
| `src/reviewd/config.example.yaml` | Modify | Document `formal_review` |
| `README.md` | Modify | Brief feature mention |

---

## Task 1: Add `InlineComment`, `ReviewEvent`, and `formal_review` to models

**Files:**
- Modify: `src/reviewd/models.py`

- [ ] **Step 1: Add `ReviewEvent` enum and `InlineComment` dataclass**

Open `src/reviewd/models.py`. After the `Severity` enum (around line 11), add:

```python
class ReviewEvent(enum.StrEnum):
    COMMENT = 'COMMENT'
    REQUEST_CHANGES = 'REQUEST_CHANGES'
    APPROVE = 'APPROVE'
```

After the `Finding` dataclass (around line 32), add:

```python
@dataclass
class InlineComment:
    path: str
    line: int
    body: str
```

- [ ] **Step 2: Add `formal_review` field to `RepoConfig`**

Find `RepoConfig` (around line 96). Add a new field after `model`:

```python
    formal_review: bool | None = None
```

`None` means "inherit from global." Explicit `True`/`False` overrides.

- [ ] **Step 3: Add `formal_review` field to `GlobalConfig`**

Find `GlobalConfig` (around line 112). Add a new field near `auto_approve`:

```python
    formal_review: bool = False
```

- [ ] **Step 4: Verify file parses**

Run: `uv run python -c "from reviewd.models import GlobalConfig, RepoConfig, ReviewEvent, InlineComment; print('ok')"`
Expected: `ok`

---

## Task 2: Wire `formal_review` through config loading

**Files:**
- Modify: `src/reviewd/config.py`

- [ ] **Step 1: Locate where `GlobalConfig` is constructed from YAML**

Run: `grep -n "GlobalConfig\|formal_review\|auto_approve" src/reviewd/config.py`

Expected: lines where YAML keys are mapped onto `GlobalConfig` and `RepoConfig` fields. Use the existing `auto_approve` wiring as the pattern to follow — `formal_review` is a simpler bool, so it's a strict subset.

- [ ] **Step 2: Add `formal_review` to the global YAML→config mapping**

Wherever the YAML dict is read into `GlobalConfig` (look for `GlobalConfig(` or a dict-merge with global defaults), add:

```python
formal_review=data.get('formal_review', False),
```

Match the surrounding style (kwarg or dict-spread, whichever exists).

- [ ] **Step 3: Add `formal_review` to the per-repo YAML→`RepoConfig` mapping**

In the repo-loading block, add:

```python
formal_review=repo_data.get('formal_review'),  # None if absent — inherits global
```

Note the explicit `None` default (not `False`) so we can distinguish "unset" from "explicitly off."

- [ ] **Step 4: Add a resolver helper**

Add a small helper at module scope:

```python
def effective_formal_review(global_config: GlobalConfig, repo: RepoConfig) -> bool:
    return repo.formal_review if repo.formal_review is not None else global_config.formal_review
```

This is the single source of truth for the resolved flag — call it from commenter/daemon, never read `.formal_review` directly off `RepoConfig`.

- [ ] **Step 5: Verify**

Run: `uv run python -c "
from reviewd.config import effective_formal_review
from reviewd.models import GlobalConfig, RepoConfig
g = GlobalConfig(repos=[], formal_review=True)
r1 = RepoConfig(name='x', path='/tmp', formal_review=None)
r2 = RepoConfig(name='y', path='/tmp', formal_review=False)
assert effective_formal_review(g, r1) is True
assert effective_formal_review(g, r2) is False
print('ok')
"`
Expected: `ok`

---

## Task 3: Add `posted_reviews` table to state DB

**Files:**
- Modify: `src/reviewd/state.py`

- [ ] **Step 1: Read current schema and method patterns**

Run: `grep -n "CREATE TABLE\|def record_comment\|def get_comment_ids\|def delete_comments" src/reviewd/state.py`

Expected: find the existing `posted_comments` table creation in `__init__` and the three methods that operate on it. Mirror them exactly.

- [ ] **Step 2: Add `posted_reviews` table creation**

In `StateDB.__init__`, alongside the existing `CREATE TABLE IF NOT EXISTS posted_comments ...`, add:

```python
self.conn.execute('''
    CREATE TABLE IF NOT EXISTS posted_reviews (
        repo_slug TEXT NOT NULL,
        pr_id INTEGER NOT NULL,
        review_id INTEGER NOT NULL,
        PRIMARY KEY (repo_slug, pr_id, review_id)
    )
''')
```

(Match the project's quoting style — single quotes — and the existing indentation of the comments table.)

- [ ] **Step 3: Add `record_review`**

After the existing `record_comment` method, add:

```python
def record_review(self, repo_slug: str, pr_id: int, review_id: int):
    self.conn.execute(
        'INSERT OR IGNORE INTO posted_reviews (repo_slug, pr_id, review_id) VALUES (?, ?, ?)',
        (repo_slug, pr_id, review_id),
    )
    self.conn.commit()
```

- [ ] **Step 4: Add `get_review_ids`**

```python
def get_review_ids(self, repo_slug: str, pr_id: int) -> list[int]:
    cur = self.conn.execute(
        'SELECT review_id FROM posted_reviews WHERE repo_slug = ? AND pr_id = ?',
        (repo_slug, pr_id),
    )
    return [row[0] for row in cur.fetchall()]
```

- [ ] **Step 5: Add `delete_review` (single) and `delete_reviews` (all for a PR)**

```python
def delete_review(self, repo_slug: str, pr_id: int, review_id: int):
    self.conn.execute(
        'DELETE FROM posted_reviews WHERE repo_slug = ? AND pr_id = ? AND review_id = ?',
        (repo_slug, pr_id, review_id),
    )
    self.conn.commit()

def delete_reviews(self, repo_slug: str, pr_id: int):
    self.conn.execute(
        'DELETE FROM posted_reviews WHERE repo_slug = ? AND pr_id = ?',
        (repo_slug, pr_id),
    )
    self.conn.commit()
```

`delete_review` is called per-prior after we've handled it (dismissed or skipped). `delete_reviews` exists for symmetry with `delete_comments`, e.g. if a PR is closed.

- [ ] **Step 6: Verify schema migration runs cleanly on existing DB**

Run: `uv run python -c "
from reviewd.state import StateDB
import tempfile, os
db_path = tempfile.mktemp(suffix='.db')
db = StateDB(db_path)
db.record_review('org/repo', 42, 12345)
db.record_review('org/repo', 42, 67890)
ids = db.get_review_ids('org/repo', 42)
assert sorted(ids) == [12345, 67890], ids
db.delete_review('org/repo', 42, 12345)
ids = db.get_review_ids('org/repo', 42)
assert ids == [67890], ids
db.delete_reviews('org/repo', 42)
assert db.get_review_ids('org/repo', 42) == []
os.unlink(db_path)
print('ok')
"`
Expected: `ok`

Also verify on the live state DB:
Run: `uv run python -c "
from reviewd.state import StateDB
db = StateDB('/Users/kevinm/Library/Application Support/reviewd/state.db')
print('Reviews recorded:', len(db.get_review_ids('any/repo', 0)))
"`
(Path may differ on user's machine — adjust to the actual state DB path. The important thing is that opening an existing DB does NOT raise and the new table is created in-place.)
Expected: `Reviews recorded: 0` (or similar — no error)

---

## Task 4: Extend provider ABC

**Files:**
- Modify: `src/reviewd/providers/base.py`

- [ ] **Step 1: Read existing ABC**

Run: `cat src/reviewd/providers/base.py`

Expected: shows current abstract methods on `GitProvider`. Match the style.

- [ ] **Step 2: Add capability flag and three abstract methods**

In `GitProvider`, add at the top of the class body (after the class-level docstring if any):

```python
    supports_formal_review: bool = False
```

Then add three new abstract methods. Place them after the existing `approve_pr` declaration:

```python
    @abstractmethod
    def submit_review(
        self,
        repo_slug: str,
        pr_id: int,
        body: str,
        event: ReviewEvent,
        inline_comments: list[InlineComment],
        source_commit: str,
    ) -> int | None: ...

    @abstractmethod
    def dismiss_review(
        self,
        repo_slug: str,
        pr_id: int,
        review_id: int,
        message: str,
    ) -> bool: ...

    @abstractmethod
    def get_diff_lines(self, repo_slug: str, pr_id: int) -> dict[str, set[int]]: ...
```

`get_diff_lines` returns a dict mapping file path → set of line numbers that are valid for inline comments (i.e. lines present in the diff hunks on the RIGHT side).

- [ ] **Step 3: Add imports**

At the top of `base.py`, add:

```python
from reviewd.models import InlineComment, ReviewEvent
```

(Add to the existing `from reviewd.models import` line if one exists.)

- [ ] **Step 4: Verify ABC parses**

Run: `uv run python -c "from reviewd.providers.base import GitProvider; print(GitProvider.supports_formal_review)"`
Expected: `False`

---

## Task 5: BitBucket provider — keep capability off, stub new methods

**Files:**
- Modify: `src/reviewd/providers/bitbucket.py`

- [ ] **Step 1: Add stub implementations**

Since the ABC declares the three new methods abstract, the BB provider must provide implementations to remain instantiable. Each one raises:

```python
    def submit_review(self, repo_slug, pr_id, body, event, inline_comments, source_commit):
        raise NotImplementedError('BitBucket does not support formal reviews')

    def dismiss_review(self, repo_slug, pr_id, review_id, message):
        raise NotImplementedError('BitBucket does not support formal reviews')

    def get_diff_lines(self, repo_slug, pr_id):
        raise NotImplementedError('BitBucket does not support formal reviews')
```

`supports_formal_review` stays at the ABC default of `False`, so commenter will never call these on the BB provider — they exist only to satisfy the ABC.

- [ ] **Step 2: Verify BB provider still instantiates**

Run: `uv run python -c "
from reviewd.models import PRInfo
from reviewd.providers.bitbucket import BitbucketProvider
# Construct with dummy config — just need to verify the class is concrete
import inspect
assert not inspect.isabstract(BitbucketProvider)
print('ok')
"`
Expected: `ok`

---

## Task 6: GitHub provider — implement the three new methods

**Files:**
- Modify: `src/reviewd/providers/github.py`

- [ ] **Step 1: Update imports and set capability flag**

At the top of `github.py`, extend the models import:

```python
from reviewd.models import GithubConfig, InlineComment, PRInfo, ReviewEvent
```

In `GithubProvider`, add at the top of the class body (above `__init__`):

```python
    supports_formal_review = True
```

- [ ] **Step 2: Implement `submit_review`**

Add after the existing `approve_pr` method:

```python
    def submit_review(
        self,
        repo_slug: str,
        pr_id: int,
        body: str,
        event: ReviewEvent,
        inline_comments: list[InlineComment],
        source_commit: str,
    ) -> int | None:
        url = f'/repos/{repo_slug}/pulls/{pr_id}/reviews'
        payload: dict = {
            'commit_id': source_commit,
            'event': event.value,
            'body': body,
        }
        if inline_comments:
            payload['comments'] = [
                {'path': c.path, 'line': c.line, 'side': 'RIGHT', 'body': f'{c.body}\n\n{BOT_MARKER}'}
                for c in inline_comments
            ]
        resp = self._request_raw('POST', url, json=payload)
        if resp.status_code == 422:
            logger.warning('Cannot submit %s review on PR #%d: %s', event.value, pr_id, resp.text[:200])
            return None
        resp.raise_for_status()
        review_id = resp.json()['id']
        logger.info('Submitted %s review %d on PR #%d (%d inline)', event.value, review_id, pr_id, len(inline_comments))
        return review_id
```

Notes:
- Inline comment bodies still get the `BOT_MARKER` suffix so we can identify them later if needed
- The review body does NOT get the marker — it's identifiable via the review ID stored in state DB
- 422 covers self-PR `REQUEST_CHANGES`/`APPROVE` rejection and validation failures (e.g. inline line not in diff — but we pre-filter to avoid this)

- [ ] **Step 3: Implement `dismiss_review`**

```python
    def dismiss_review(self, repo_slug: str, pr_id: int, review_id: int, message: str) -> bool:
        url = f'/repos/{repo_slug}/pulls/{pr_id}/reviews/{review_id}/dismissals'
        resp = self._request_raw('PUT', url, json={'message': message})
        if resp.status_code != 200:
            logger.warning('Failed to dismiss review %d on PR #%d: %d %s', review_id, pr_id, resp.status_code, resp.text[:200])
            return False
        logger.info('Dismissed review %d on PR #%d', review_id, pr_id)
        return True
```

- [ ] **Step 4: Implement `get_diff_lines`**

```python
    def get_diff_lines(self, repo_slug: str, pr_id: int) -> dict[str, set[int]]:
        files = self._paginate(f'/repos/{repo_slug}/pulls/{pr_id}/files', {'per_page': '100'})
        result: dict[str, set[int]] = {}
        for f in files:
            patch = f.get('patch')
            if not patch:
                continue
            result[f['filename']] = _parse_added_lines(patch)
        return result
```

Add a module-level helper at the bottom of the file (next to `_parse_next_link`):

```python
def _parse_added_lines(patch: str) -> set[int]:
    lines: set[int] = set()
    new_line = 0
    for raw in patch.split('\n'):
        if raw.startswith('@@'):
            # @@ -old,oldcount +new,newcount @@
            try:
                new_part = raw.split('+', 1)[1].split(' ', 1)[0]
                new_line = int(new_part.split(',', 1)[0])
            except (IndexError, ValueError):
                logger.warning('Could not parse hunk header: %s', raw[:80])
                continue
            continue
        if raw.startswith('+') and not raw.startswith('+++'):
            lines.add(new_line)
            new_line += 1
        elif raw.startswith('-') and not raw.startswith('---'):
            pass  # deleted line — RIGHT side doesn't advance
        else:
            new_line += 1  # context line
    return lines
```

Note: this returns only *added* lines (the lines reviewd is most likely to comment on). Context lines are tracked for positional accuracy but aren't included in the valid-comment set — GitHub allows comments on context lines too, but for AI review purposes, restricting to added lines is the right tradeoff: it cuts the hallucination surface without losing real findings. If we need context-line comments later, swap the `pass` branch into "add to lines" too.

- [ ] **Step 5: Add a quick parse sanity check**

Run: `uv run python -c "
from reviewd.providers.github import _parse_added_lines
patch = '@@ -1,3 +1,4 @@\\n line1\\n-old\\n+new1\\n+new2\\n line3'
result = _parse_added_lines(patch)
assert result == {2, 3}, result
print('ok')
"`
Expected: `ok`

- [ ] **Step 6: Verify GH provider is concrete**

Run: `uv run python -c "
import inspect
from reviewd.providers.github import GithubProvider
assert not inspect.isabstract(GithubProvider)
assert GithubProvider.supports_formal_review is True
print('ok')
"`
Expected: `ok`

---

## Task 7: Extract existing commenter path into helper

This task is a pure refactor — behavior must stay identical. We're carving out the formal-review branch point.

**Files:**
- Modify: `src/reviewd/commenter.py`

- [ ] **Step 1: Rename the existing posting logic**

In `commenter.py`, the current `post_review` function does dedup/filtering up front, then posts inline + summary, then optionally approves. Keep the dedup/filter prologue in `post_review`, but extract the posting logic (everything from `logger.info('Posting review: ...')` down through the auto-approve call) into a new private function:

```python
def _post_comment_review(
    provider: GitProvider,
    state_db: StateDB,
    pr: PRInfo,
    result: ReviewResult,
    inline_findings: list[Finding],
    inline_ids: set[int],
    project_config: ProjectConfig,
    global_config: GlobalConfig,
    cli: CLI,
    model: str | None,
    diff_lines: int | None,
):
    # [Move existing logic here verbatim — see Step 2]
```

- [ ] **Step 2: Show the exact code moved**

The body of `_post_comment_review` is the existing block from `commenter.py` starting at:

```python
    logger.info('Posting review: %d inline + summary comment', len(inline_findings))

    old_comment_ids = state_db.get_comment_ids(pr.repo_slug, pr.pr_id)
    if old_comment_ids:
        logger.info('Deleting %d old comments on PR #%d', len(old_comment_ids), pr.pr_id)
        deleted = 0
        for cid in old_comment_ids:
            if provider.delete_comment(pr.repo_slug, pr.pr_id, cid):
                deleted += 1
        state_db.delete_comments(pr.repo_slug, pr.pr_id)
        logger.info('Deleted %d/%d old comments', deleted, len(old_comment_ids))

    for i, finding in enumerate(inline_findings, 1):
        logger.info('Posting inline comment %d/%d: %s:%s', i, len(inline_findings), finding.file, finding.line)
        body = _format_inline_comment(finding)
        try:
            comment_id = provider.post_comment(
                pr.repo_slug,
                pr.pr_id,
                body,
                file_path=finding.file,
                line=finding.line,
                source_commit=pr.source_commit,
            )
            state_db.record_comment(pr.repo_slug, pr.pr_id, comment_id)
        except Exception:
            logger.exception('Failed to post inline comment on %s:%s, skipping', finding.file, finding.line)

    aa = project_config.auto_approve
    approved = False
    approve_blocked_reason = None
    if aa.enabled:
        approved, approve_blocked_reason = _resolve_auto_approve(aa, result, diff_lines)
        if not approved:
            logger.info('Auto-approve blocked for PR #%d: %s', pr.pr_id, approve_blocked_reason or 'AI did not approve')

    logger.info('Posting summary comment')
    summary_body = _format_summary_comment(
        result,
        inline_ids,
        global_config,
        project_config,
        cli,
        model=model,
        approved=approved,
        approve_blocked_reason=approve_blocked_reason,
    )
    comment_id = provider.post_comment(pr.repo_slug, pr.pr_id, summary_body)
    state_db.record_comment(pr.repo_slug, pr.pr_id, comment_id)

    if project_config.critical_task and hasattr(provider, 'list_tasks'):
        _sync_critical_task(provider, pr, result, project_config)

    if approved and provider.approve_pr(pr.repo_slug, pr.pr_id):
        logger.info('Auto-approved PR #%d', pr.pr_id)
```

(Note: `Exception` is allowed here because the project already has it in this exact spot — we are NOT introducing new broad excepts, just moving existing code. Project rule says "No broad except clauses" for new code, and we're respecting that elsewhere.)

- [ ] **Step 3: Update `post_review` to call the helper**

Replace the moved block in `post_review` with a single call:

```python
    _post_comment_review(
        provider,
        state_db,
        pr,
        result,
        inline_findings,
        inline_ids,
        project_config,
        global_config,
        cli,
        model,
        diff_lines,
    )
```

- [ ] **Step 4: Verify behavior unchanged**

Run: `uv run python -c "
from reviewd.commenter import post_review, _post_comment_review
print('ok')
"`
Expected: `ok`

For real behavioral verification, run a live `reviewd pr <repo> <pr_id>` against a test PR with `formal_review: false` (or unset) and confirm output matches what it would have before the refactor. (Manual — covered by the full test plan at the end.)

---

## Task 8: Add the diff pre-filter (shared by both paths)

**Files:**
- Modify: `src/reviewd/commenter.py`

- [ ] **Step 1: Add filter helper**

In `commenter.py`, add near the other helpers (e.g. above `post_review`):

```python
def _filter_inline_findings_by_diff(
    inline_findings: list[Finding],
    provider: GitProvider,
    pr: PRInfo,
) -> list[Finding]:
    """Drop inline findings whose (file, line) isn't in the PR diff hunks.

    Falls back to returning the input unchanged if the provider can't compute
    diff lines (BB) or the call fails — in that case we accept that the
    individual-comment 422 fallback still handles bad lines per finding.
    """
    if not inline_findings:
        return inline_findings
    try:
        diff_lines = provider.get_diff_lines(pr.repo_slug, pr.pr_id)
    except NotImplementedError:
        return inline_findings
    except httpx.HTTPError as e:
        logger.warning('Could not fetch diff lines for pre-filter: %s — skipping filter', e)
        return inline_findings

    kept = []
    for f in inline_findings:
        if f.file in diff_lines and f.line in diff_lines[f.file]:
            kept.append(f)
        else:
            logger.info('Dropping hallucinated inline finding %s:%s (not in diff)', f.file, f.line)
    return kept
```

Add `import httpx` at the top of `commenter.py` if not already present.

- [ ] **Step 2: Apply the filter in `post_review` before inline selection finalizes**

In `post_review`, after the line that computes `inline_findings` from `inline_severities` and before the `max_inline` check, add:

```python
    inline_findings = _filter_inline_findings_by_diff(inline_findings, provider, pr)
```

Findings dropped by the filter are still in `result.findings`, so they appear in the summary body — that's the correct fallback. Inline tally and summary formatting already work off `inline_ids` (which we recompute after the filter), so this slots in cleanly.

Remember to recompute `inline_ids = {id(f) for f in inline_findings}` AFTER the filter.

- [ ] **Step 3: Verify**

Run: `uv run python -c "
from reviewd.commenter import _filter_inline_findings_by_diff
from reviewd.models import Finding, Severity, PRInfo
from unittest.mock import MagicMock

provider = MagicMock()
provider.get_diff_lines.return_value = {'foo.py': {10, 11, 12}}
pr = PRInfo(repo_slug='org/repo', pr_id=1, title='', author='', source_branch='', destination_branch='', source_commit='', url='')
findings = [
    Finding(Severity.CRITICAL, 'cat', 'A', 'foo.py', 10, None, 'i', None),
    Finding(Severity.CRITICAL, 'cat', 'B', 'foo.py', 999, None, 'i', None),  # bad line
    Finding(Severity.CRITICAL, 'cat', 'C', 'bar.py', 5, None, 'i', None),    # bad file
]
kept = _filter_inline_findings_by_diff(findings, provider, pr)
assert len(kept) == 1 and kept[0].title == 'A', kept
print('ok')
"`
Expected: `ok`

---

## Task 9: Implement `_post_formal_review`

**Files:**
- Modify: `src/reviewd/commenter.py`

- [ ] **Step 1: Add event-selection helper**

Add near `_resolve_auto_approve`:

```python
def _select_review_event(
    result: ReviewResult,
    project_config: ProjectConfig,
    diff_lines: int | None,
) -> tuple[ReviewEvent, bool, str | None]:
    """Returns (event, approved, approve_blocked_reason).

    Order: APPROVE > REQUEST_CHANGES > COMMENT.
    """
    aa = project_config.auto_approve
    approved = False
    approve_blocked_reason = None
    if aa.enabled:
        approved, approve_blocked_reason = _resolve_auto_approve(aa, result, diff_lines)

    if approved:
        return ReviewEvent.APPROVE, True, None

    has_critical = any(f.severity == Severity.CRITICAL for f in result.findings)
    if has_critical:
        return ReviewEvent.REQUEST_CHANGES, False, approve_blocked_reason

    return ReviewEvent.COMMENT, False, approve_blocked_reason
```

Add `ReviewEvent` and `InlineComment` to the existing models import at the top of `commenter.py`.

- [ ] **Step 2: Add the formal-review posting function**

```python
def _post_formal_review(
    provider: GitProvider,
    state_db: StateDB,
    pr: PRInfo,
    result: ReviewResult,
    inline_findings: list[Finding],
    inline_ids: set[int],
    project_config: ProjectConfig,
    global_config: GlobalConfig,
    cli: CLI,
    model: str | None,
    diff_lines: int | None,
):
    event, approved, approve_blocked_reason = _select_review_event(result, project_config, diff_lines)
    logger.info('Posting formal review on PR #%d: event=%s', pr.pr_id, event.value)

    _dismiss_prior_reviews(provider, state_db, pr)

    old_comment_ids = state_db.get_comment_ids(pr.repo_slug, pr.pr_id)
    if old_comment_ids:
        logger.info('Deleting %d old inline comments on PR #%d', len(old_comment_ids), pr.pr_id)
        deleted = 0
        for cid in old_comment_ids:
            if provider.delete_comment(pr.repo_slug, pr.pr_id, cid):
                deleted += 1
        state_db.delete_comments(pr.repo_slug, pr.pr_id)
        logger.info('Deleted %d/%d old inline comments', deleted, len(old_comment_ids))

    body = _format_summary_comment(
        result,
        inline_ids,
        global_config,
        project_config,
        cli,
        model=model,
        approved=approved,
        approve_blocked_reason=approve_blocked_reason,
    )

    inline_payload = [
        InlineComment(path=f.file, line=f.line, body=_format_inline_comment(f))
        for f in inline_findings
    ]

    review_id = provider.submit_review(
        pr.repo_slug,
        pr.pr_id,
        body=body,
        event=event,
        inline_comments=inline_payload,
        source_commit=pr.source_commit,
    )
    if review_id is not None:
        state_db.record_review(pr.repo_slug, pr.pr_id, review_id)
    else:
        logger.warning('Formal review on PR #%d returned no ID (likely self-PR 422)', pr.pr_id)
```

- [ ] **Step 3: Add prior-review handling**

```python
def _dismiss_prior_reviews(provider: GitProvider, state_db: StateDB, pr: PRInfo):
    prior_ids = state_db.get_review_ids(pr.repo_slug, pr.pr_id)
    if not prior_ids:
        return
    logger.info('Processing %d prior reviews on PR #%d', len(prior_ids), pr.pr_id)
    for review_id in prior_ids:
        try:
            state = provider.get_review_state(pr.repo_slug, pr.pr_id, review_id)
        except httpx.HTTPError as e:
            logger.warning('Could not fetch prior review %d state: %s — removing from state', review_id, e)
            state_db.delete_review(pr.repo_slug, pr.pr_id, review_id)
            continue

        if state == 'CHANGES_REQUESTED':
            provider.dismiss_review(
                pr.repo_slug,
                pr.pr_id,
                review_id,
                'Superseded by newer reviewd review',
            )
        # Always clear from state — we've handled it, either by dismissal or skip.
        state_db.delete_review(pr.repo_slug, pr.pr_id, review_id)
```

This references a `get_review_state` method that doesn't exist yet — Task 10 adds it.

- [ ] **Step 4: Wire the branch in `post_review`**

Find the call to `_post_comment_review` from Task 7. Replace it with a branch:

```python
    use_formal = (
        provider.supports_formal_review
        and effective_formal_review(global_config, repo_config)
    )
    if use_formal:
        _post_formal_review(
            provider,
            state_db,
            pr,
            result,
            inline_findings,
            inline_ids,
            project_config,
            global_config,
            cli,
            model,
            diff_lines,
        )
    else:
        _post_comment_review(
            provider,
            state_db,
            pr,
            result,
            inline_findings,
            inline_ids,
            project_config,
            global_config,
            cli,
            model,
            diff_lines,
        )
```

This requires `repo_config: RepoConfig` to be passed into `post_review`. Update the signature:

```python
def post_review(
    provider: GitProvider,
    state_db: StateDB,
    pr: PRInfo,
    result: ReviewResult,
    repo_config: RepoConfig,
    project_config: ProjectConfig,
    global_config: GlobalConfig,
    cli: CLI = CLI.CLAUDE,
    model: str | None = None,
    dry_run: bool = False,
    diff_lines: int | None = None,
):
```

Add `RepoConfig` and `effective_formal_review` to the imports at the top of `commenter.py`.

- [ ] **Step 5: Update `post_review` callers**

Find every call site:

Run: `grep -rn "post_review(" src/reviewd/`

Each call site (likely `daemon.py` and possibly `cli.py`) needs to add `repo_config=<the RepoConfig>` as a positional argument. The caller already has the `RepoConfig` in scope — it's the one passed to the reviewer.

For each call site, show the exact edit. Look for:

```python
post_review(provider, state_db, pr, result, project_config, global_config, ...)
```

Change to:

```python
post_review(provider, state_db, pr, result, repo_config, project_config, global_config, ...)
```

- [ ] **Step 6: Verify imports and call sites resolve**

Run: `uv run python -c "from reviewd.commenter import post_review, _post_formal_review, _post_comment_review, _dismiss_prior_reviews, _select_review_event; print('ok')"`
Expected: `ok`

Run: `uv run python -m compileall src/reviewd/`
Expected: no errors

---

## Task 10: Add `get_review_state` to provider

This is the only piece Task 9 referenced but didn't define. We need a way to inspect a prior review's state before deciding whether to dismiss.

**Files:**
- Modify: `src/reviewd/providers/base.py`
- Modify: `src/reviewd/providers/github.py`
- Modify: `src/reviewd/providers/bitbucket.py`

- [ ] **Step 1: Add to ABC**

In `base.py`, alongside the other formal-review methods:

```python
    @abstractmethod
    def get_review_state(self, repo_slug: str, pr_id: int, review_id: int) -> str: ...
```

Returns the GitHub review state string: `'PENDING'`, `'COMMENTED'`, `'APPROVED'`, `'CHANGES_REQUESTED'`, or `'DISMISSED'`.

- [ ] **Step 2: Implement on `GithubProvider`**

In `github.py`:

```python
    def get_review_state(self, repo_slug: str, pr_id: int, review_id: int) -> str:
        url = f'/repos/{repo_slug}/pulls/{pr_id}/reviews/{review_id}'
        resp = self._request('GET', url)
        return resp.json()['state']
```

- [ ] **Step 3: Stub on `BitbucketProvider`**

```python
    def get_review_state(self, repo_slug, pr_id, review_id):
        raise NotImplementedError('BitBucket does not support formal reviews')
```

- [ ] **Step 4: Verify**

Run: `uv run python -c "
import inspect
from reviewd.providers.github import GithubProvider
from reviewd.providers.bitbucket import BitbucketProvider
assert not inspect.isabstract(GithubProvider)
assert not inspect.isabstract(BitbucketProvider)
print('ok')
"`
Expected: `ok`

---

## Task 11: Dry-run support for formal reviews

**Files:**
- Modify: `src/reviewd/commenter.py`

- [ ] **Step 1: Branch `_print_dry_run` on `use_formal`**

Currently `_print_dry_run` prints the inline+summary preview. Add a parameter and branch:

```python
def _print_dry_run(
    result: ReviewResult,
    inline_findings: list[Finding],
    inline_ids: set[int],
    global_config: GlobalConfig,
    project_config: ProjectConfig,
    cli: CLI = CLI.CLAUDE,
    model: str | None = None,
    diff_lines: int | None = None,
    use_formal: bool = False,
):
    if use_formal:
        _print_dry_run_formal(result, inline_findings, inline_ids, global_config, project_config, cli, model, diff_lines)
        return
    # ... existing body unchanged ...
```

- [ ] **Step 2: Add formal dry-run output**

```python
def _print_dry_run_formal(
    result: ReviewResult,
    inline_findings: list[Finding],
    inline_ids: set[int],
    global_config: GlobalConfig,
    project_config: ProjectConfig,
    cli: CLI,
    model: str | None,
    diff_lines: int | None,
):
    event, approved, approve_blocked_reason = _select_review_event(result, project_config, diff_lines)

    print('\n' + '=' * 60)
    print(f'DRY RUN — would submit a formal review: event={event.value}')
    print('=' * 60)

    if inline_findings:
        print(f'\n--- Inline Comments ({len(inline_findings)}) ---')
        for f in inline_findings:
            print(f'\n  File: {f.file}:{f.line}')
            print(f'  {_format_inline_comment(f)}')

    print('\n--- Review Body ---')
    print(
        _format_summary_comment(
            result,
            inline_ids,
            global_config,
            project_config,
            cli,
            model=model,
            approved=approved,
            approve_blocked_reason=approve_blocked_reason,
        )
    )
    print('=' * 60 + '\n')
```

- [ ] **Step 3: Pass `use_formal` from `post_review`**

In `post_review`, before the dry-run guard, compute `use_formal` once and pass it to `_print_dry_run`:

```python
    use_formal = (
        provider.supports_formal_review
        and effective_formal_review(global_config, repo_config)
    )

    if dry_run:
        _print_dry_run(
            result,
            inline_findings,
            inline_ids,
            global_config,
            project_config,
            cli,
            model=model,
            diff_lines=diff_lines,
            use_formal=use_formal,
        )
        return
```

Then the `if use_formal:` branch from Task 9 already has `use_formal` in scope — reuse it.

- [ ] **Step 4: Verify**

Run: `uv run python -m compileall src/reviewd/`
Expected: no errors

---

## Task 12: Update config example and README

**Files:**
- Modify: `src/reviewd/config.example.yaml`
- Modify: `README.md`

- [ ] **Step 1: Document `formal_review` in the example YAML**

In `config.example.yaml`, near the existing `auto_approve` block (or near global config options), add:

```yaml
# Drive the PR review process on GitHub — reviewd submits a single formal review
# instead of scattered comments. When findings include 'critical', the review uses
# REQUEST_CHANGES and blocks the PR (under branch protection). When auto_approve
# gates pass, the review uses APPROVE. Otherwise it's a COMMENT review.
#
# GitHub only. BitBucket repos fall back to scattered comments even if this is true.
# Default: false. Can be overridden per-repo.
# formal_review: false
```

And in the per-repo section:

```yaml
# repos:
#   - name: my-repo
#     provider: github
#     repo_slug: org/repo
#     formal_review: true  # opt this repo into formal reviews
```

- [ ] **Step 2: Add a brief mention in README**

Find the "Configuration" or "Features" section of `README.md`. Add a one-paragraph mention:

```markdown
### Formal PR reviews (GitHub)

Set `formal_review: true` globally or per-repo to have reviewd submit findings as a
single formal GitHub PR Review. Critical findings become `REQUEST_CHANGES` (blocks
merge under branch protection); auto-approved reviews become `APPROVE`; everything
else becomes `COMMENT`. Default is off — existing comment-based behavior is preserved.

For `REQUEST_CHANGES` to actually block merge, configure branch protection on the
target branch to require approving reviews.
```

- [ ] **Step 3: Verify**

Run: `head -100 src/reviewd/config.example.yaml | grep -A2 formal_review`
Expected: shows the new doc lines.

---

## Task 13: End-to-end manual verification

**Files:** No code changes — execute the spec's test plan.

- [ ] **Step 1: Default-off preservation**

On a watched repo with NO `formal_review` set, run `reviewd pr <repo> <pr_id>` against a real PR. Confirm:
- Inline review comments posted
- Issue summary comment posted
- Behavior identical to pre-change

- [ ] **Step 2: REQUEST_CHANGES blocks the PR**

On a test repo with branch protection requiring approving reviews:
1. Set `formal_review: true` in repo config
2. Open a PR containing a deliberate critical issue
3. Run reviewd
4. Confirm: one review entry with "Changes requested" badge; merge button disabled

- [ ] **Step 3: Re-review dismisses prior block**

Continuing from Step 2:
1. Push a commit fixing the critical issue
2. Re-run reviewd
3. Confirm: prior REQUEST_CHANGES shows "Superseded by newer reviewd review"; new COMMENT or APPROVE review posted; merge button no longer blocked by reviewd

- [ ] **Step 4: APPROVE flow**

Enable `auto_approve.enabled: true` alongside `formal_review: true`. Submit a clean PR. Confirm a single APPROVE review (no separate summary + approve call).

- [ ] **Step 5: Self-PR safety**

Open a PR as the reviewd bot user. Run reviewd. Confirm: no review submitted, warning logged, no exception.

- [ ] **Step 6: Hallucinated line filter**

Either:
- Feed a planted `Finding` with `file/line` outside the diff via a unit invocation, OR
- Wait for a natural occurrence in logs

Confirm dropped findings log at INFO level (`Dropping hallucinated inline finding ...`) and appear in the summary body instead.

- [ ] **Step 7: Dry-run**

Run `reviewd pr --dry-run <repo> <pr_id>` on a `formal_review: true` repo. Confirm output shows `event=...`, inline preview, and review body — no API calls made.

- [ ] **Step 8: BitBucket fallback**

Set `formal_review: true` on a BB repo. Run reviewd. Confirm comment-based path runs as today (no formal review attempted, no exception).

---

## Task 14: Code review

**Files:** No code changes.

- [ ] **Step 1: Invoke superpowers:requesting-code-review**

Per the user's explicit instruction, invoke the `superpowers:requesting-code-review` skill on the completed diff. Address any findings before declaring the feature complete. This is a hard gate — do not skip.

The skill expects the work to be ready for review (all tasks above completed, all manual verification steps passed).

- [ ] **Step 2: Address findings and re-verify**

Loop on findings until the reviewer is satisfied. Run the relevant verification steps from Task 13 again after any non-trivial change.

- [ ] **Step 3: Hand back to user**

Report results to the user. Do NOT commit, push, or release without explicit instruction (per project `CLAUDE.md`).

---

## Self-Review Notes (author)

Checked against spec sections:
- ✅ Config (`formal_review` on Global + Repo) — Tasks 1, 2
- ✅ Behavior table (APPROVE > REQUEST_CHANGES > COMMENT) — Task 9 (`_select_review_event`)
- ✅ Self-PR 422 — Task 6 step 2
- ✅ Empty review — covered implicitly (no findings + no body → `_format_summary_comment` still produces a body with the footer; we always have *something* to post. If we want to skip truly empty reviews, that's a follow-up — currently out of scope per spec.)
- ✅ State + dismissal (selective, CHANGES_REQUESTED only) — Tasks 3, 9, 10
- ✅ Provider interface (capability flag + 4 methods) — Tasks 4, 5, 6, 10
- ✅ Hallucinated line pre-filter (both paths) — Task 8 (applied in `post_review` before either branch)
- ✅ Dry-run support — Task 11
- ✅ Docs — Task 12
- ✅ Manual verification — Task 13
- ✅ Code review skill — Task 14

Type/name consistency checked: `submit_review`, `dismiss_review`, `get_diff_lines`, `get_review_state`, `record_review`, `get_review_ids`, `delete_review`, `delete_reviews`, `effective_formal_review`, `_post_formal_review`, `_post_comment_review`, `_dismiss_prior_reviews`, `_select_review_event`, `_filter_inline_findings_by_diff` — all match across tasks.
