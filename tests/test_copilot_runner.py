from __future__ import annotations

import pytest

from reviewd.models import CLI
from reviewd.reviewer import CLI_DEFAULTS, _build_cli_command, context_dir_args

READ_ONLY_FLAGS = [
    '--sandbox',
    '--disallow-temp-dir',
    '--disable-builtin-mcps',
    '--no-custom-instructions',
    '--deny-tool=write',
    '--secret-env-vars=COPILOT_GITHUB_TOKEN',
    '--available-tools=view,rg,glob',
]


def test_copilot_command_is_read_only(tmp_path):
    prompt_file = tmp_path / 'p.txt'
    prompt_file.write_text('review this')
    cmd, stdin_input = _build_cli_command(
        CLI.COPILOT, str(prompt_file), model='gpt-x', extra_args=['--add-dir', '/ctx']
    )

    assert stdin_input == 'review this'
    assert cmd[0] == 'copilot'
    assert '-p' not in cmd and '-' not in cmd
    for flag in READ_ONLY_FLAGS:
        assert flag in cmd
    for arg in cmd:
        assert not arg.startswith('--allow-tool')
        assert not arg.startswith('--allow-all')
    assert '--yolo' not in cmd
    tools = [arg.removeprefix('--available-tools=') for arg in cmd if arg.startswith('--available-tools=')]
    assert tools == ['view,rg,glob']
    assert cmd[cmd.index('--model') + 1] == 'gpt-x'
    assert cmd[cmd.index('--add-dir') + 1] == '/ctx'


def test_codex_default_is_read_only():
    codex = CLI_DEFAULTS[CLI.CODEX]
    assert codex[codex.index('--sandbox') + 1] == 'read-only'


def test_context_dir_args_for_copilot_and_claude():
    assert context_dir_args(CLI.COPILOT, ['/a', '/b']) == ['--add-dir', '/a', '--add-dir', '/b']
    assert context_dir_args(CLI.CLAUDE, ['/a']) == ['--add-dir', '/a']


def test_context_dir_args_empty_is_noop_for_any_cli():
    assert context_dir_args(CLI.GEMINI, []) == []


def test_context_dir_args_unsupported_cli_raises():
    with pytest.raises(ValueError, match='gemini'):
        context_dir_args(CLI.GEMINI, ['/a'])


def test_invoke_cli_timeout_kills_hung_process():
    import subprocess
    import time

    from reviewd.reviewer import invoke_cli

    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        invoke_cli('x', '.', cli=CLI.CLAUDE, timeout=1, cli_defaults={CLI.CLAUDE: ['sh', '-c', 'sleep 30 #']})
    assert time.monotonic() - t0 < 10
