from __future__ import annotations

import pytest

from reviewd.config import load_global_config, resolve_pipeline, review_label
from reviewd.models import CLI, Engine

BASE = """
state_db: {tmp}/state.db
cli: claude
model: sonnet
context_repos:
  mobile-services:
    path: {tmp}/mobile-services
    description: Api endpoints and DTOs
review:
  find: {{ cli: copilot, model: gpt-find, args: [--reasoning-effort, high] }}
  verify: {{ model: gpt-verify }}
repos:
  - name: ios
    path: {tmp}/ios
    provider: bitbucket
    workspace: ws
    engine: pipeline
    context: [mobile-services]
  - name: qa
    path: {tmp}/qa
    provider: github
"""


def _load(tmp_path, text):
    path = tmp_path / 'config.yaml'
    path.write_text(text.format(tmp=tmp_path))
    return load_global_config(path)


def test_engine_defaults_to_legacy(tmp_path):
    config = _load(tmp_path, BASE)
    qa = config.repos[1]
    assert config.engine == Engine.LEGACY
    assert resolve_pipeline(config, qa) is None
    assert review_label(config, qa) == (CLI.CLAUDE, 'sonnet')


def test_pipeline_repo_resolves_passes_and_context(tmp_path):
    config = _load(tmp_path, BASE)
    ios = config.repos[0]
    pipeline = resolve_pipeline(config, ios)

    assert pipeline.find.cli == CLI.COPILOT
    assert pipeline.find.model == 'gpt-find'
    assert pipeline.find.timeout == 900
    assert pipeline.find.args == ['--reasoning-effort', 'high']
    assert pipeline.verify.cli == CLI.COPILOT
    assert pipeline.verify.model == 'gpt-verify'
    assert pipeline.verify.timeout == 600
    assert pipeline.verify.enabled is True
    assert [c.name for c in pipeline.context] == ['mobile-services']
    assert pipeline.context[0].ref == 'main'
    assert pipeline.context[0].path == str(tmp_path / 'mobile-services')
    assert review_label(config, ios) == (CLI.COPILOT, 'gpt-find')


def test_unknown_context_name_fails_at_load(tmp_path):
    with pytest.raises(SystemExit, match='nope'):
        _load(tmp_path, BASE.replace('context: [mobile-services]', 'context: [nope]'))


def test_global_engine_default_can_be_pipeline(tmp_path):
    config = _load(tmp_path, 'engine: pipeline\n' + BASE)
    assert resolve_pipeline(config, config.repos[1]) is not None
