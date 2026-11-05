from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from helpers import git

from reviewd.context import cleanup_context, prepare_context
from reviewd.models import ContextRepo


@pytest.fixture()
def clone(tmp_path):
    origin = tmp_path / 'origin'
    origin.mkdir()
    git(origin, 'init', '-q')
    (origin / 'Dto.cs').write_text('public int Id;\n')
    git(origin, 'add', '.')
    git(origin, 'commit', '-qm', 'init')
    git(tmp_path, 'clone', '-q', str(origin), 'svc')
    return tmp_path / 'svc', origin


def test_prepare_creates_worktree_at_origin_ref(clone, pr):
    path, origin = clone
    checkout = prepare_context(ContextRepo(name='svc', path=str(path)), pr)
    try:
        assert checkout.worktree is not None
        assert (Path(checkout.worktree) / 'Dto.cs').exists()
        assert checkout.commit == git(origin, 'rev-parse', 'HEAD')
        assert checkout.stale is False
        assert checkout.label == f'svc@main ({checkout.commit[:7]})'
    finally:
        cleanup_context(checkout)
    assert not Path(checkout.worktree).exists()


def test_fetch_failure_uses_last_known_commit(clone, pr):
    path, origin = clone
    shutil.rmtree(origin)
    checkout = prepare_context(ContextRepo(name='svc', path=str(path)), pr)
    try:
        assert checkout.worktree is not None
        assert checkout.stale is True
        assert checkout.label.endswith('(stale: fetch failed)')
    finally:
        cleanup_context(checkout)


def test_missing_clone_runs_without_context(tmp_path, pr):
    checkout = prepare_context(ContextRepo(name='svc', path=str(tmp_path / 'nope')), pr)
    assert checkout.worktree is None
    assert checkout.label == 'svc: unavailable (not cloned)'
    cleanup_context(checkout)


def test_missing_ref_runs_without_context(clone, pr):
    path, _ = clone
    checkout = prepare_context(ContextRepo(name='svc', path=str(path), ref='nope'), pr)
    assert checkout.worktree is None
    assert 'origin/nope not found' in checkout.label
