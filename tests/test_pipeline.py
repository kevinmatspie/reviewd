from __future__ import annotations

import pytest
from helpers import git, make_finding

from reviewd import pipeline as pipeline_mod
from reviewd.models import PassConfig, PipelineConfig, ProjectConfig, Severity
from reviewd.pipeline import anchor_findings, changed_lines, merge_verdicts, parse_candidates, parse_changed_lines
from reviewd.reviewer import CLIAuthError

DIFF = """\
diff --git a/app/Api.swift b/app/Api.swift
--- a/app/Api.swift
+++ b/app/Api.swift
@@ -10,0 +11,2 @@ class Api {
+    let a = 1
+    let b = 2
@@ -40 +42 @@ func load() {
-    old()
+    new()
diff --git a/gone.txt b/gone.txt
--- a/gone.txt
+++ /dev/null
@@ -1 +0,0 @@
-bye
"""


def test_parse_changed_lines():
    assert parse_changed_lines(DIFF) == {'app/Api.swift': {11, 12, 42}}


def test_changed_lines_from_real_repo(tmp_path):
    git(tmp_path, 'init', '-q')
    (tmp_path / 'f.txt').write_text('1\n2\n3\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'base')
    git(tmp_path, 'checkout', '-qb', 'feature')
    (tmp_path / 'f.txt').write_text('1\nTWO\n3\n4\n')
    git(tmp_path, 'commit', '-qam', 'change')

    assert changed_lines(str(tmp_path), 'main') == {'f.txt': {2, 4}}


def test_changed_lines_includes_uncommitted_for_local(tmp_path):
    git(tmp_path, 'init', '-q')
    (tmp_path / 'f.txt').write_text('1\n2\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'base')
    (tmp_path / 'f.txt').write_text('1\n2\n3\n')

    assert changed_lines(str(tmp_path), 'main', include_worktree=True) == {'f.txt': {3}}


def test_anchor_demotes_out_of_diff_findings_and_clears_fix():
    inside = make_finding(file='app/Api.swift', line=11, fix='x')
    outside = make_finding(file='app/Api.swift', line=99, fix='y')
    other_file = make_finding(file='app/Other.swift', line=11)
    no_line = make_finding(file='app/Api.swift', line=None)

    anchor_findings([inside, outside, other_file, no_line], {'app/Api.swift': {11, 12}})

    assert inside.inline is True and inside.fix == 'x'
    assert outside.inline is False and outside.fix is None and outside.line == 99
    assert other_file.inline is False
    assert no_line.inline is True


FIND_DATA = {
    'overview': 'o',
    'summary': 's',
    'candidates': [
        {
            'kind': 'finding',
            'severity': 'critical',
            'category': 'Contract',
            'title': 'int vs string',
            'file': 'a.swift',
            'line': 3,
            'issue': 'i',
            'fix': None,
            'evidence': ['/ctx/Dto.cs:4'],
        },
        {
            'kind': 'edge_case',
            'severity': 'suggestion',
            'category': 'Logic',
            'title': 'empty list',
            'file': 'a.swift',
            'line': 5,
            'issue': 'i',
            'fix': None,
            'evidence': ['a.swift:5'],
        },
        {'kind': 'bogus', 'title': 'no severity', 'file': 'b.swift', 'line': 1, 'issue': 'i'},
    ],
}


def test_parse_candidates_assigns_ids_and_normalizes_kind():
    candidates = parse_candidates(FIND_DATA)
    assert [c.id for c in candidates] == ['C1', 'C2', 'C3']
    assert [c.kind for c in candidates] == ['finding', 'edge_case', 'finding']
    assert candidates[0].evidence == ['/ctx/Dto.cs:4']
    assert candidates[2].finding.severity == Severity.SUGGESTION


def test_merge_verdicts_routes_each_candidate():
    candidates = parse_candidates(FIND_DATA)
    verdicts = [
        {'id': 'C1', 'verdict': 'confirmed', 'severity': 'suggestion', 'reason': 'real but minor'},
        {'id': 'C2', 'verdict': 'dropped', 'severity': None, 'reason': 'guarded upstream'},
        {'id': 'C9', 'verdict': 'confirmed', 'severity': 'critical', 'reason': 'invented'},
    ]
    findings, edge_cases, dropped = merge_verdicts(candidates, verdicts)

    assert [f.title for f in findings] == ['int vs string']
    assert findings[0].severity == Severity.SUGGESTION
    assert [f.title for f in edge_cases] == ['no severity']
    assert [(c.id, reason) for c, reason in dropped] == [('C2', 'guarded upstream')]


def test_merge_verdicts_edge_case_verdict():
    candidates = parse_candidates(FIND_DATA)
    findings, edge_cases, dropped = merge_verdicts(
        candidates,
        [{'id': c.id, 'verdict': 'edge_case', 'severity': None, 'reason': 'unproven'} for c in candidates],
    )
    assert findings == [] and dropped == [] and len(edge_cases) == 3


def _pipeline(verify=True):
    return PipelineConfig(find=PassConfig(), verify=PassConfig(enabled=verify))


def test_verify_retried_once_then_succeeds(monkeypatch):
    calls = []

    def fake(prompt, cwd, pass_config, add_dirs, cli_defaults, schema):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('timeout')
        return {'verdicts': []}

    monkeypatch.setattr(pipeline_mod, '_run_pass', fake)
    assert pipeline_mod._verify_with_retry('p', '/tmp', PassConfig(), [], None) == {'verdicts': []}
    assert len(calls) == 2


def test_verify_fails_twice_raises(monkeypatch):
    def fake(*args):
        raise ValueError('No JSON block found in AI output')

    monkeypatch.setattr(pipeline_mod, '_run_pass', fake)
    with pytest.raises(RuntimeError, match='Verify pass failed after 2 attempts'):
        pipeline_mod._verify_with_retry('p', '/tmp', PassConfig(), [], None)


def test_verify_auth_error_not_retried(monkeypatch):
    calls = []

    def fake(*args):
        calls.append(1)
        raise CLIAuthError('auth')

    monkeypatch.setattr(pipeline_mod, '_run_pass', fake)
    with pytest.raises(CLIAuthError):
        pipeline_mod._verify_with_retry('p', '/tmp', PassConfig(), [], None)
    assert len(calls) == 1


def _repo_with_change(tmp_path):
    git(tmp_path, 'init', '-q')
    (tmp_path / 'a.swift').write_text('1\n2\n3\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'base')
    git(tmp_path, 'checkout', '-qb', 'feature')
    (tmp_path / 'a.swift').write_text('1\nTWO\n3\n')
    git(tmp_path, 'commit', '-qam', 'change')


def test_run_pipeline_merges_verdicts_and_anchors(tmp_path, pr, monkeypatch):
    _repo_with_change(tmp_path)
    pr.is_local = True
    pr.destination_branch = 'main'
    find_data = {
        'overview': 'o',
        'summary': 'find summary',
        'candidates': [
            {
                'kind': 'finding',
                'severity': 'critical',
                'title': 'on diff',
                'file': 'a.swift',
                'line': 2,
                'issue': 'i',
                'fix': 'x',
                'evidence': ['a.swift:2'],
            },
            {
                'kind': 'finding',
                'severity': 'suggestion',
                'title': 'off diff',
                'file': 'a.swift',
                'line': 3,
                'issue': 'i',
                'fix': 'y',
                'evidence': ['a.swift:3'],
            },
            {
                'kind': 'finding',
                'severity': 'suggestion',
                'title': 'wrong',
                'file': 'a.swift',
                'line': 2,
                'issue': 'i',
                'fix': None,
                'evidence': ['a.swift:2'],
            },
        ],
    }
    verify_data = {
        'verdicts': [
            {'id': 'C1', 'verdict': 'confirmed', 'severity': None, 'reason': 'r'},
            {'id': 'C2', 'verdict': 'confirmed', 'severity': None, 'reason': 'r'},
            {'id': 'C3', 'verdict': 'dropped', 'severity': None, 'reason': 'contradicted'},
        ],
        'summary': 'verified summary',
        'approve': False,
        'approve_reason': None,
    }

    def fake(prompt, cwd, pass_config, add_dirs, cli_defaults, schema):
        return verify_data if schema is pipeline_mod.VERIFY_SCHEMA else find_data

    monkeypatch.setattr(pipeline_mod, '_run_pass', fake)
    result = pipeline_mod.run_pipeline(str(tmp_path), pr, ProjectConfig(), _pipeline())

    assert [f.title for f in result.findings] == ['on diff', 'off diff']
    assert result.findings[0].inline is True
    assert result.findings[1].inline is False and result.findings[1].fix is None
    assert result.summary == 'verified summary'
    assert result.edge_cases == []
    assert result.context_refs == []


def test_run_pipeline_without_verify_uses_find_kinds(tmp_path, pr, monkeypatch):
    _repo_with_change(tmp_path)
    pr.is_local = True
    pr.destination_branch = 'main'
    find_data = {
        'overview': 'o',
        'summary': 's',
        'candidates': [
            {
                'kind': 'edge_case',
                'severity': 'suggestion',
                'title': 'edge',
                'file': 'a.swift',
                'line': 2,
                'issue': 'i',
                'fix': None,
                'evidence': ['a.swift:2'],
            },
        ],
    }
    monkeypatch.setattr(pipeline_mod, '_run_pass', lambda *a: find_data)
    result = pipeline_mod.run_pipeline(str(tmp_path), pr, ProjectConfig(), _pipeline(verify=False))
    assert result.findings == []
    assert [e.title for e in result.edge_cases] == ['edge']
