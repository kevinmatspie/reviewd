from __future__ import annotations

import importlib.metadata
import logging
import logging.handlers
import os
import subprocess
import sys
from pathlib import Path

import click

from reviewd.colors import BOLD_RED, CLEAR_LINE, CYAN, DIM, GREEN, RED, RESET, YELLOW
from reviewd.config import get_provider, load_global_config
from reviewd.daemon import review_single_pr, run_poll_loop
from reviewd.models import CLI, GlobalConfig, RepoConfig
from reviewd.state import StateDB

try:
    VERSION = importlib.metadata.version('reviewd')
except importlib.metadata.PackageNotFoundError:
    VERSION = '0.0.0-dev'

CONFIG_DIR = Path(os.environ.get('XDG_CONFIG_HOME', '~/.config')).expanduser() / 'reviewd'
CONFIG_PATH = CONFIG_DIR / 'config.yaml'


def _apply_cli_override(config: GlobalConfig, cli: str | None):
    if cli is None:
        return
    cli_enum = CLI(cli)
    config.cli = cli_enum
    for repo in config.repos:
        repo.cli = cli_enum


def _get_repo_config(config: GlobalConfig, repo_arg: str) -> RepoConfig | None:
    repo_config = next((r for r in config.repos if r.name == repo_arg), None)
    if repo_config:
        return repo_config
    target = (Path.cwd() if repo_arg == '.' else Path(repo_arg).expanduser()).resolve()
    return next((r for r in config.repos if Path(r.path).resolve() == target), None)


def _interactive_select(options: list[tuple[str, str]]) -> str | None:
    """Pick an option via fzf if available, else a numbered prompt. Returns the value, or None."""
    if not options:
        return None

    import shutil

    if shutil.which('fzf'):
        input_text = '\n'.join(f'{i}\t{i + 1:2}) {display}' for i, (display, _) in enumerate(options))
        try:
            result = subprocess.run(
                [
                    'fzf',
                    '--prompt=Select a PR to review> ',
                    '--height=40%',
                    '--layout=reverse',
                    '--border',
                    '--ansi',
                    '--delimiter=\t',
                    '--with-nth=2..',
                ],
                input=input_text,
                text=True,
                capture_output=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                idx = int(result.stdout.split('\t', 1)[0])
                return options[idx][1]
        except (ValueError, OSError):
            pass
        return None

    for i, (display, _) in enumerate(options, 1):
        click.echo(f'{i}) {display}')
    while True:
        try:
            choice = input('\nEnter number to review a PR (empty to cancel): ').strip()
        except (KeyboardInterrupt, EOFError):
            return None
        if not choice:
            return None
        try:
            idx = int(choice) - 1
        except ValueError:
            click.echo('Please enter a valid number.')
            continue
        if 0 <= idx < len(options):
            return options[idx][1]
        click.echo('Invalid selection.')


PROGRESS_LOG_LEVEL = 22
logging.addLevelName(PROGRESS_LOG_LEVEL, 'PROGRESS')

REVIEW_LOG_LEVEL = 25
logging.addLevelName(REVIEW_LOG_LEVEL, 'REVIEW')


class _ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: DIM,
        PROGRESS_LOG_LEVEL: CYAN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: BOLD_RED,
        REVIEW_LOG_LEVEL: GREEN,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, '')
        record.levelname = f'{color}{record.levelname:<8}{RESET}'
        if color:
            record.msg = f'{color}{record.msg}{RESET}'
        # Clear any in-place status line before writing the log line
        return CLEAR_LINE + super().format(record)


def _setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_ColorFormatter('%(asctime)s %(levelname)s %(name)s — %(message)s', datefmt='%H:%M:%S'))
    logging.root.addHandler(handler)
    logging.root.setLevel(level)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)


LOG_FILE_MAX_BYTES = 10 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 7


def _attach_file_logging(log_file: str | None):
    if not log_file:
        return
    path = Path(log_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=LOG_FILE_MAX_BYTES,
        backupCount=LOG_FILE_BACKUP_COUNT,
    )
    handler.setFormatter(
        logging.Formatter('%(asctime)s %(levelname)-8s %(name)s — %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    )
    logging.root.addHandler(handler)

    # When stderr is captured to a regular file (e.g. launchd
    # StandardErrorPath, or a shell `2>file` redirect), every INFO line
    # gets double-written — once via RotatingFileHandler to log_file, and
    # once via stderr → captured file — and the captured file isn't rotated
    # by us. Raise stderr to WARNING in that case so the captured file
    # only catches startup crashes and real errors. Non-file stderr
    # (journald socket, docker pipe, bare pipe, TTY) is left alone so
    # those deployments keep full observability.
    if _stderr_is_regular_file():
        for h in logging.root.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                h.setLevel(logging.WARNING)


def _stderr_is_regular_file() -> bool:
    import stat

    try:
        mode = os.fstat(sys.stderr.fileno()).st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode)


def _resolve_verbose(ctx, local_verbose: bool) -> bool:
    verbose = ctx.obj['verbose'] or local_verbose
    if verbose:
        logging.root.setLevel(logging.DEBUG)
    return verbose


@click.group(invoke_without_command=True)
@click.option('--config', 'config_path', default=None, help='Path to global config file')
@click.option('-v', '--verbose', is_flag=True, help='Enable verbose logging')
@click.pass_context
def main(ctx, config_path: str | None, verbose: bool):
    ctx.ensure_object(dict)
    ctx.obj['config_path'] = config_path
    ctx.obj['verbose'] = verbose
    _setup_logging(verbose)
    click.echo(f'reviewd v{VERSION}')

    if ctx.invoked_subcommand is None:
        path = Path(config_path).expanduser() if config_path else CONFIG_PATH
        if not path.exists():
            ctx.invoke(init)
        else:
            click.echo(ctx.get_help())


UPDATE_CHECK_CACHE = Path(os.environ.get('XDG_CACHE_HOME', '~/.cache')).expanduser() / 'reviewd' / 'latest_version'
UPDATE_CHECK_INTERVAL = 6 * 3600  # seconds


def _check_for_updates():
    try:
        import time

        now = time.time()
        latest = None

        if UPDATE_CHECK_CACHE.exists():
            stat = UPDATE_CHECK_CACHE.stat()
            if now - stat.st_mtime < UPDATE_CHECK_INTERVAL:
                latest = UPDATE_CHECK_CACHE.read_text().strip()

        if latest is None:
            import httpx

            resp = httpx.get('https://pypi.org/pypi/reviewd/json', timeout=2)
            latest = resp.json()['info']['version']
            UPDATE_CHECK_CACHE.parent.mkdir(parents=True, exist_ok=True)
            UPDATE_CHECK_CACHE.write_text(latest)

        installed = tuple(int(x) for x in VERSION.split('.'))
        remote = tuple(int(x) for x in latest.split('.'))
        if remote > installed:
            exe = sys.executable
            if 'uv/tools' in exe or 'uv\\tools' in exe:
                cmd = 'uv tool upgrade reviewd'
            elif 'pipx' in exe:
                cmd = 'pipx upgrade reviewd'
            else:
                cmd = 'pip install --upgrade reviewd'
            click.echo(f'{YELLOW}Update available: v{VERSION} \u2192 v{latest}  ({cmd}){RESET}')
    except Exception:
        pass


def _ensure_global_config(config_path: str | None) -> Path:
    path = Path(config_path).expanduser() if config_path else CONFIG_PATH
    if not path.exists():
        from reviewd.wizard import run_wizard

        click.echo(f'No config found at {path}. Starting setup wizard...')
        run_wizard()
        if not path.exists():
            raise SystemExit(1)
    return path


@main.command()
@click.option('--sample', is_flag=True, help='Write annotated sample config (non-interactive, for VPS/CI)')
@click.option('-v', '--verbose', is_flag=True, help='Enable verbose logging')
@click.pass_context
def init(ctx, sample: bool, verbose: bool):
    """Interactive setup wizard — configure repos, credentials, and AI CLI."""
    _resolve_verbose(ctx, verbose)
    from reviewd.wizard import SAMPLE_CONFIG, run_wizard

    if sample:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(SAMPLE_CONFIG)
        click.echo(f'Created sample config at {CONFIG_PATH}')
        click.echo('Edit it to add your tokens and repos.')
        return

    if CONFIG_PATH.exists():
        click.echo(f'Global config already exists at {CONFIG_PATH}. \u2713')
        if not click.confirm('Re-run setup wizard?', default=False):
            return

    run_wizard()


@main.command()
@click.option('-v', '--verbose', is_flag=True, help='Enable verbose logging')
@click.option('--dry-run', is_flag=True, help='Print reviews without posting')
@click.option('--review-existing', is_flag=True, help='Review unreviewed open PRs on startup')
@click.option('--cli', type=click.Choice(['claude', 'gemini', 'codex']), default=None, help='Override AI CLI')
@click.option('--concurrency', type=int, default=None, help='Max concurrent reviews (default: 4)')
@click.pass_context
def watch(ctx, verbose: bool, dry_run: bool, review_existing: bool, cli: str | None, concurrency: int | None):
    """Start the daemon — polls for new PRs and reviews them."""
    verbose = _resolve_verbose(ctx, verbose)
    _check_for_updates()
    _ensure_global_config(ctx.obj['config_path'])
    config = load_global_config(ctx.obj['config_path'])
    _attach_file_logging(config.log_file)
    _apply_cli_override(config, cli)
    if concurrency is not None:
        config.max_concurrent_reviews = concurrency
    run_poll_loop(config, dry_run=dry_run, review_existing=review_existing, verbose=verbose)


@main.command()
@click.argument('repo')
@click.argument('pr_id', type=int)
@click.option('-v', '--verbose', is_flag=True, help='Enable verbose logging')
@click.option('--dry-run', is_flag=True, help='Print review without posting')
@click.option('--force', is_flag=True, help='Review even if already reviewed (bypasses cooldown/skip)')
@click.option('--cli', type=click.Choice(['claude', 'gemini', 'codex']), default=None, help='Override AI CLI')
@click.pass_context
def pr(ctx, repo: str, pr_id: int, verbose: bool, dry_run: bool, force: bool, cli: str | None):
    """One-shot review of a specific PR."""
    _resolve_verbose(ctx, verbose)
    _ensure_global_config(ctx.obj['config_path'])
    config = load_global_config(ctx.obj['config_path'])
    _apply_cli_override(config, cli)
    review_single_pr(config, repo, pr_id=pr_id, dry_run=dry_run, force=force)


@main.command()
@click.argument('base_branch', required=False)
@click.option('-v', '--verbose', is_flag=True, help='Enable verbose logging')
@click.option('--cli', type=click.Choice(['claude', 'gemini', 'codex']), default=None, help='Override AI CLI')
@click.pass_context
def scan(ctx, base_branch: str | None, verbose: bool, cli: str | None):
    """Review local changes (including uncommitted work) against a base branch. Prints findings, posts nothing."""
    from reviewd.commenter import post_review
    from reviewd.config import load_project_config
    from reviewd.models import PRInfo
    from reviewd.reviewer import get_base_branch, get_current_branch, get_diff_lines, review_pr

    _resolve_verbose(ctx, verbose)
    _ensure_global_config(ctx.obj['config_path'])
    config = load_global_config(ctx.obj['config_path'])
    _apply_cli_override(config, cli)

    repo_config = _get_repo_config(config, '.')
    if not repo_config:
        available = ', '.join(r.name for r in config.repos) or '(none)'
        click.echo(f'Current directory is not a configured repo. Available: {available}', err=True)
        raise SystemExit(1)

    repo_path = Path(repo_config.path)
    try:
        source_branch = get_current_branch(repo_path)
        if base_branch is None:
            base_branch = get_base_branch(repo_path)
        # Prefer a remote-tracking base so the diff reflects what's new vs the server.
        base_ref = base_branch
        for remote in ('origin', 'upstream'):
            res = subprocess.run(
                ['git', 'rev-parse', '--verify', f'{remote}/{base_branch}'],
                cwd=repo_path,
                capture_output=True,
                timeout=5,
            )
            if res.returncode == 0:
                base_ref = f'{remote}/{base_branch}'
                break
    except RuntimeError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e

    click.echo(f'{CYAN}Scanning: {source_branch} → {base_ref}{RESET}\n')

    pr = PRInfo(
        repo_slug=repo_config.slug,
        pr_id=0,
        title=f'Local Scan: {source_branch}',
        author='local',
        source_branch=source_branch,
        destination_branch=base_ref,
        source_commit='HEAD',
        url='',
        is_local=True,
    )

    project_config = load_project_config(repo_path, config)
    active_model = repo_config.model or config.model

    diff_lines = get_diff_lines(str(repo_path), pr)
    if diff_lines == 0:
        click.echo(f'{GREEN}No changes detected between {source_branch} and {base_ref}.{RESET}')
        return

    result = review_pr(
        str(repo_path),
        pr,
        project_config,
        cli=repo_config.cli,
        model=active_model,
        cli_args=config.cli_args,
        cli_defaults=config.cli_defaults,
    )

    state_db = StateDB(config.state_db)
    try:
        post_review(
            None,
            state_db,
            pr,
            result,
            repo_config,
            project_config,
            config,
            cli=repo_config.cli,
            model=active_model,
            dry_run=True,
            diff_lines=diff_lines,
        )
    finally:
        state_db.close()


@main.command(name='ls')
@click.argument('repo', metavar='[repo_name_or_path]', required=False)
@click.option('-v', '--verbose', is_flag=True, help='Enable verbose logging')
@click.option('--dry-run', is_flag=True, help='If a PR is selected, print review without posting')
@click.option('--force', is_flag=True, help='If a PR is selected, review even if already reviewed')
@click.pass_context
def ls_repos(ctx, repo: str | None, verbose: bool, dry_run: bool, force: bool):
    """List watched repos and their open PRs. In a terminal, select one to review."""
    _resolve_verbose(ctx, verbose)
    _ensure_global_config(ctx.obj['config_path'])
    config = load_global_config(ctx.obj['config_path'])

    target_repos = config.repos
    if repo is not None:
        repo_config = _get_repo_config(config, repo)
        if not repo_config:
            available = ', '.join(r.name for r in config.repos) or '(none)'
            click.echo(f'Repo "{repo}" not found. Available: {available}', err=True)
            raise SystemExit(1)
        target_repos = [repo_config]

    pr_options: list[tuple[str, str]] = []
    state_db = StateDB(config.state_db)
    try:
        for repo_config in target_repos:
            provider_name = repo_config.provider or 'bitbucket'
            click.echo(f'\n{repo_config.name}  ({provider_name}, {repo_config.cli.value})')
            try:
                provider = get_provider(config, repo_config)
                prs = provider.list_open_prs(repo_config.slug)
                if not prs:
                    click.echo('  No open PRs')
                    continue
                for pr in prs:
                    reviewed = state_db.has_review(pr.repo_slug, pr.pr_id, pr.source_commit)
                    marker = '\u2713' if reviewed else '\u2022'
                    click.echo(f'  {marker} #{pr.pr_id}  {pr.title}  ({pr.author})')
                    display = (
                        f'{CYAN}[{repo_config.name}]{RESET} {YELLOW}#{pr.pr_id}{RESET} '
                        f'{pr.title} {DIM}({pr.author}){RESET} {marker}'
                    )
                    pr_options.append((display, f'{repo_config.name} {pr.pr_id}'))
            except Exception as e:
                click.echo(f'  Error: {e}')
    finally:
        state_db.close()

    if not pr_options or not sys.stdin.isatty():
        click.echo()
        click.echo('To review a PR:  reviewd pr <repo> <id>')
        click.echo('To review a PR (dry run):  reviewd pr <repo> <id> --dry-run')
        return

    click.echo()
    selected = _interactive_select(pr_options)
    if not selected:
        return
    sel_repo, sel_pr_id = selected.rsplit(' ', 1)
    click.echo(f'\n{CYAN}Reviewing: reviewd pr {sel_repo} {sel_pr_id}{RESET}\n')
    review_single_pr(config, sel_repo, pr_id=int(sel_pr_id), dry_run=dry_run, force=force)


@main.command()
@click.argument('repo')
@click.option('-v', '--verbose', is_flag=True, help='Enable verbose logging')
@click.option('--limit', default=20, help='Number of recent reviews to show')
@click.pass_context
def status(ctx, repo: str, verbose: bool, limit: int):
    """Show review history for a repo."""
    _resolve_verbose(ctx, verbose)
    _ensure_global_config(ctx.obj['config_path'])
    config = load_global_config(ctx.obj['config_path'])
    state_db = StateDB(config.state_db)
    try:
        history = state_db.get_review_history(repo, limit=limit)
        if not history:
            click.echo(f'No review history for {repo}')
            return
        for row in history:
            status_str = row['status']
            pr = row['pr_id']
            commit = row['source_commit'][:8]
            ts = row['created_at']
            err = row.get('error_message', '')
            line = f'PR #{pr}  {commit}  {status_str:<10}  {ts}'
            if err:
                line += f'  error: {err}'
            click.echo(line)
    finally:
        state_db.close()
