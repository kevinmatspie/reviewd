from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from reviewd.models import ContextRepo, PRInfo

logger = logging.getLogger(__name__)

_GIT_ENV = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_LFS_SKIP_SMUDGE': '1'}
_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


@dataclass
class ContextCheckout:
    repo: ContextRepo
    worktree: str | None = None
    commit: str | None = None
    stale: bool = False
    note: str | None = None

    @property
    def label(self) -> str:
        if self.worktree is None or self.commit is None:
            return f'{self.repo.name}: unavailable ({self.note})'
        label = f'{self.repo.name}@{self.repo.ref} ({self.commit[:7]})'
        if self.stale:
            label += ' (stale: fetch failed)'
        return label


def _git(args: list[str], cwd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, env=_GIT_ENV, timeout=timeout)


def _worktree_dir(repo: ContextRepo, pr: PRInfo) -> Path:
    slug = re.sub(r'[^A-Za-z0-9_.-]', '-', pr.repo_slug)
    return Path(repo.path) / '.reviewd-worktrees' / f'ctx-{slug}-pr-{pr.pr_id}'


def prepare_context(repo: ContextRepo, pr: PRInfo) -> ContextCheckout:
    checkout = ContextCheckout(repo=repo)
    if not (Path(repo.path) / '.git').exists():
        checkout.note = 'not cloned'
        logger.warning('Context repo %s not found at %s, reviewing without it', repo.name, repo.path)
        return checkout

    target = _worktree_dir(repo, pr)
    with _locks[repo.path]:
        try:
            fetch = _git(['fetch', 'origin', repo.ref], repo.path, timeout=120)
        except subprocess.TimeoutExpired:
            checkout.stale = True
            logger.warning('Fetch timed out for context repo %s', repo.name)
        else:
            if fetch.returncode != 0:
                checkout.stale = True
                logger.warning('Fetch failed for context repo %s: %s', repo.name, fetch.stderr.strip())

        rev = _git(['rev-parse', '--verify', f'origin/{repo.ref}^{{commit}}'], repo.path)
        if rev.returncode != 0:
            checkout.note = f'origin/{repo.ref} not found'
            logger.warning('Context repo %s has no origin/%s, reviewing without it', repo.name, repo.ref)
            return checkout
        checkout.commit = rev.stdout.strip()

        if target.exists():
            _git(['worktree', 'remove', '--force', str(target)], repo.path)
            shutil.rmtree(target, ignore_errors=True)
        _git(['worktree', 'prune'], repo.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        add = _git(
            [
                '-c',
                'filter.git-crypt.smudge=cat',
                '-c',
                'filter.git-crypt.required=false',
                'worktree',
                'add',
                '--detach',
                str(target),
                checkout.commit,
            ],
            repo.path,
            timeout=120,
        )
        if add.returncode != 0:
            checkout.note = 'worktree creation failed'
            logger.warning('Could not create worktree for context repo %s: %s', repo.name, add.stderr.strip())
            return checkout

    checkout.worktree = str(target)
    logger.info('Context ready: %s at %s', checkout.label, checkout.worktree)
    return checkout


def cleanup_context(checkout: ContextCheckout) -> None:
    if checkout.worktree is None:
        return
    with _locks[checkout.repo.path]:
        _git(['worktree', 'remove', '--force', checkout.worktree], checkout.repo.path)
    shutil.rmtree(checkout.worktree, ignore_errors=True)
