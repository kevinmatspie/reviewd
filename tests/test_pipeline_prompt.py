from __future__ import annotations

import json

from helpers import make_finding

from reviewd.context import ContextCheckout
from reviewd.models import AutoApproveConfig, Candidate, ContextRepo, ProjectConfig
from reviewd.prompt import build_find_prompt, build_verify_prompt


def _contexts():
    ok = ContextCheckout(
        repo=ContextRepo(name='mobile-services', path='/repos/ms', description='Api endpoints and DTOs'),
        worktree='/repos/ms/.reviewd-worktrees/ctx-x-pr-42',
        commit='abcdef1234567',
    )
    missing = ContextCheckout(repo=ContextRepo(name='other', path='/nope'), note='not cloned')
    return [ok, missing]


def test_find_prompt_lists_allowed_dirs_and_context(pr):
    prompt = build_find_prompt(pr, ProjectConfig(instructions='DataManager is fragile'), _contexts())

    assert '/repos/ms/.reviewd-worktrees/ctx-x-pr-42' in prompt
    assert 'mobile-services@main (abcdef1)' in prompt
    assert 'Api endpoints and DTOs' in prompt
    assert 'other: unavailable (not cloned)' in prompt
    assert 'Regression sweep' in prompt
    assert 'Contract check' in prompt
    assert 'DataManager is fragile' in prompt
    assert 'origin/main' in prompt
    assert '"candidates"' in prompt
    assert 'empty findings list is a valid and preferred' not in prompt


def test_find_prompt_without_context_skips_contract_step(pr):
    prompt = build_find_prompt(pr, ProjectConfig(), [])
    assert 'Contract check' not in prompt
    assert 'Regression sweep' in prompt


def test_verify_prompt_embeds_candidates_and_approve_rules(pr):
    candidate = Candidate(id='C1', kind='finding', finding=make_finding(title='int vs string'), evidence=['/x:1'])
    config = ProjectConfig(auto_approve=AutoApproveConfig(enabled=True, rules='No migrations'))
    prompt = build_verify_prompt(pr, config, _contexts(), [candidate])

    payload = prompt.split('## Candidates\n```json\n', 1)[1].split('\n```', 1)[0]
    assert json.loads(payload)[0] == {
        'id': 'C1',
        'kind': 'finding',
        'severity': 'suggestion',
        'category': 'General',
        'title': 'int vs string',
        'file': 'src/main.py',
        'line': 10,
        'issue': 'Something is wrong',
        'evidence': ['/x:1'],
    }
    assert 'No migrations' in prompt
    assert 'Do NOT add new issues' in prompt
    assert '"verdicts"' in prompt
