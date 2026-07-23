from __future__ import annotations

import pytest
import yaml

from reviewd.config import load_global_config
from reviewd.models import GithubConfig
from reviewd.prompt import build_review_prompt
from reviewd.providers.github import GithubProvider


def _write_config(tmp_path, data: dict) -> str:
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.dump(data))
    return str(path)


def test_watch_paths_parsed_from_config(tmp_path):
    path = _write_config(
        tmp_path,
        {
            'github': {'token': 'x'},
            'repos': [
                {
                    'name': 'qa',
                    'path': '/tmp/qa',
                    'provider': 'github',
                    'repo_slug': 'spie-dev/QAdevelopment',
                    'watch_paths': ['Postman/', 'SSMS'],
                }
            ],
        },
    )
    config = load_global_config(path)
    assert config.repos[0].watch_paths == ['Postman/', 'SSMS']


def test_watch_paths_defaults_empty(tmp_path):
    path = _write_config(
        tmp_path,
        {'github': {'token': 'x'}, 'repos': [{'name': 'r', 'path': '/tmp/r', 'provider': 'github'}]},
    )
    assert load_global_config(path).repos[0].watch_paths == []


def test_watch_paths_bad_type_raises(tmp_path):
    path = _write_config(
        tmp_path,
        {
            'github': {'token': 'x'},
            'repos': [{'name': 'r', 'path': '/tmp/r', 'provider': 'github', 'watch_paths': 'Postman'}],
        },
    )
    with pytest.raises(SystemExit, match='watch_paths'):
        load_global_config(path)


def test_prompt_scoped_injects_pathspec_and_scope_section(pr, project_config):
    prompt = build_review_prompt(pr, project_config, watch_paths=['Postman/', 'QA Test Event .NET Project'])
    assert '## Review Scope — RESTRICTED' in prompt
    assert "git diff <merge-base>..HEAD -- Postman/ 'QA Test Event .NET Project/'" in prompt
    assert "git log --reverse --format='%h %s'" in prompt
    assert "HEAD -- Postman/ 'QA Test Event .NET Project/'" in prompt


def test_prompt_unscoped_unchanged(pr, project_config):
    prompt = build_review_prompt(pr, project_config)
    assert 'Review Scope' not in prompt
    assert 'git diff <merge-base>..HEAD' in prompt
    assert 'HEAD --' not in prompt


def test_github_list_pr_files(monkeypatch):
    provider = GithubProvider(GithubConfig(token='x'))
    captured = {}

    def fake_paginate(url, params=None):
        captured['url'] = url
        return [{'filename': 'Postman/a.json'}, {'filename': 'SSMS/b.sql'}]

    monkeypatch.setattr(provider, '_paginate', fake_paginate)
    files = provider.list_pr_files('spie-dev/QAdevelopment', 17)
    assert files == ['Postman/a.json', 'SSMS/b.sql']
    assert captured['url'] == '/repos/spie-dev/QAdevelopment/pulls/17/files'
