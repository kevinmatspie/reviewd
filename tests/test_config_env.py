"""Config: env var substitution, max_concurrent_reviews, GIT_TERMINAL_PROMPT."""

from __future__ import annotations

import pytest
import yaml

from reviewd.config import load_global_config


def _write_config(tmp_path, data: dict) -> str:
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.dump(data))
    return str(path)


def test_env_var_substitution(tmp_path, monkeypatch):
    monkeypatch.setenv('TEST_GH_TOKEN', 'ghp_secret123')
    path = _write_config(
        tmp_path,
        {
            'github': {'token': '${TEST_GH_TOKEN}'},
            'repos': [{'name': 'r', 'path': '/tmp/r', 'provider': 'github'}],
        },
    )
    config = load_global_config(path)
    assert config.github.token == 'ghp_secret123'


def test_missing_env_var_raises(tmp_path, monkeypatch):
    monkeypatch.delenv('NONEXISTENT_VAR_XYZ', raising=False)
    path = _write_config(
        tmp_path,
        {
            'github': {'token': '${NONEXISTENT_VAR_XYZ}'},
            'repos': [{'name': 'r', 'path': '/tmp/r', 'provider': 'github'}],
        },
    )
    with pytest.raises(ValueError, match='NONEXISTENT_VAR_XYZ is not set'):
        load_global_config(path)


def test_max_concurrent_reviews_parsed(tmp_path):
    path = _write_config(
        tmp_path,
        {
            'max_concurrent_reviews': 8,
            'repos': [{'name': 'r', 'path': '/tmp/r', 'provider': 'github'}],
        },
    )
    config = load_global_config(path)
    assert config.max_concurrent_reviews == 8


def test_max_concurrent_reviews_default(tmp_path):
    path = _write_config(
        tmp_path,
        {
            'repos': [{'name': 'r', 'path': '/tmp/r', 'provider': 'github'}],
        },
    )
    config = load_global_config(path)
    assert config.max_concurrent_reviews == 4


def test_git_env_has_terminal_prompt_disabled():
    from reviewd.reviewer import _GIT_ENV

    assert _GIT_ENV['GIT_TERMINAL_PROMPT'] == '0'

    from reviewd.config import _GIT_ENV as _CONFIG_GIT_ENV

    assert _CONFIG_GIT_ENV['GIT_TERMINAL_PROMPT'] == '0'
