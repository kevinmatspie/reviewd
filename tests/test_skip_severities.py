from __future__ import annotations

import os
import subprocess

import pytest
import yaml

from reviewd.config import load_global_config, load_project_config
from reviewd.models import ProjectConfig, RepoConfig
from reviewd.prompt import build_review_prompt

_ID_ENV = {
    'GIT_AUTHOR_NAME': 't',
    'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't',
    'GIT_COMMITTER_EMAIL': 't@e',
}


def _write_config(tmp_path, repo_extra: dict) -> str:
    path = tmp_path / 'config.yaml'
    path.write_text(
        yaml.dump(
            {
                'github': {'token': 'x'},
                'repos': [{'name': 'r', 'path': '/tmp/r', 'provider': 'github', **repo_extra}],
            }
        )
    )
    return str(path)


def test_repo_skip_severities_parsed(tmp_path):
    config = load_global_config(_write_config(tmp_path, {'skip_severities': ['nitpick', 'good']}))
    assert config.repos[0].skip_severities == ['nitpick', 'good']


def test_repo_skip_severities_defaults_empty(tmp_path):
    assert load_global_config(_write_config(tmp_path, {})).repos[0].skip_severities == []


def test_invalid_severity_value_raises(tmp_path):
    with pytest.raises(SystemExit, match='invalid values'):
        load_global_config(_write_config(tmp_path, {'skip_severities': ['nitpicks']}))


def test_skip_severities_bad_type_raises(tmp_path):
    with pytest.raises(SystemExit, match='must be a list of strings'):
        load_global_config(_write_config(tmp_path, {'skip_severities': 'nitpick'}))


def test_repo_and_project_skips_union(tmp_path):
    repo_dir = tmp_path / 'repo'
    repo_dir.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo_dir)], check=True, env={**os.environ, **_ID_ENV})
    (repo_dir / '.reviewd.yaml').write_text(yaml.dump({'skip_severities': ['good']}))

    global_config = load_global_config(_write_config(tmp_path, {}))
    repo_config = RepoConfig(name='r', path=str(repo_dir), provider='github', skip_severities=['nitpick'])

    pc = load_project_config(str(repo_dir), global_config, repo_config)
    assert pc.skip_severities == ['nitpick', 'good']


def test_prompt_omits_skipped_severity(pr):
    project_config = ProjectConfig(skip_severities=['nitpick'])
    prompt = build_review_prompt(pr, project_config)
    assert 'Do NOT include nitpick findings.' in prompt
    assert '- nitpick:' not in prompt
