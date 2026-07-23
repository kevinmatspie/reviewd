from __future__ import annotations

import os
import subprocess

from reviewd.models import PRInfo
from reviewd.reviewer import create_worktree

_ID_ENV = {
    'GIT_AUTHOR_NAME': 't',
    'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't',
    'GIT_COMMITTER_EMAIL': 't@e',
}


def _git(cwd, *args) -> str:
    result = subprocess.run(
        ['git', *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={**os.environ, **_ID_ENV},
    )
    assert result.returncode == 0, f'git {args} failed: {result.stderr}'
    return result.stdout.strip()


def _seed_origin(tmp_path):
    origin = tmp_path / 'origin.git'
    _git(tmp_path, 'init', '--bare', '-b', 'main', str(origin))
    seed = tmp_path / 'seed'
    _git(tmp_path, 'clone', str(origin), str(seed))
    (seed / 'base.txt').write_text('base\n')
    _git(seed, 'add', '.')
    _git(seed, 'commit', '-m', 'base on main')
    _git(seed, 'push', 'origin', 'main')
    return origin, seed


def test_create_worktree_checks_out_fork_head_not_colliding_branch(tmp_path):
    origin, seed = _seed_origin(tmp_path)
    base_sha = _git(seed, 'rev-parse', 'HEAD')

    # Fork head: a divergent commit published to origin ONLY under the PR ref
    # (as GitHub does for fork PRs), while origin/main stays at base.
    _git(seed, 'checkout', '-b', 'forkhead')
    (seed / 'PlaywrightFramework').mkdir()
    (seed / 'PlaywrightFramework' / 'x.py').write_text('print(1)\n')
    _git(seed, 'add', '.')
    _git(seed, 'commit', '-m', 'fork change')
    head_sha = _git(seed, 'rev-parse', 'HEAD')
    _git(seed, 'push', 'origin', f'{head_sha}:refs/pull/1/head')

    clone = tmp_path / 'clone'
    _git(tmp_path, 'clone', str(origin), str(clone))
    assert head_sha != base_sha

    pr = PRInfo(
        repo_slug='owner/repo',
        pr_id=1,
        title='t',
        author='a',
        source_branch='main',  # collides with origin's own main — the bug trigger
        destination_branch='main',
        source_commit=head_sha,
        url='',
    )
    worktree = create_worktree(str(clone), pr)
    assert _git(worktree, 'rev-parse', 'HEAD') == head_sha


def test_create_worktree_same_repo_branch(tmp_path):
    origin, seed = _seed_origin(tmp_path)

    _git(seed, 'checkout', '-b', 'feature')
    (seed / 'f.txt').write_text('feature\n')
    _git(seed, 'add', '.')
    _git(seed, 'commit', '-m', 'feature change')
    head_sha = _git(seed, 'rev-parse', 'HEAD')
    _git(seed, 'push', 'origin', 'feature')

    clone = tmp_path / 'clone'
    _git(tmp_path, 'clone', str(origin), str(clone))

    pr = PRInfo(
        repo_slug='owner/repo',
        pr_id=2,
        title='t',
        author='a',
        source_branch='feature',
        destination_branch='main',
        source_commit=head_sha,
        url='',
    )
    worktree = create_worktree(str(clone), pr)
    assert _git(worktree, 'rev-parse', 'HEAD') == head_sha
