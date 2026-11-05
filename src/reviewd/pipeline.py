from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from reviewd import scope
from reviewd.context import cleanup_context, prepare_context
from reviewd.models import (
    CLI,
    Candidate,
    Finding,
    PassConfig,
    PipelineConfig,
    PRInfo,
    ProjectConfig,
    ReviewResult,
    Severity,
)
from reviewd.prompt import build_find_prompt, build_verify_prompt
from reviewd.reviewer import CLIAuthError, context_dir_args, extract_json, invoke_cli, parse_finding

logger = logging.getLogger(__name__)

VERIFY_ATTEMPTS = 2
MAX_PROMPT_DIFF_CHARS = 200_000
MAX_PROMPT_COMMITS_CHARS = 20_000

_HUNK_PATTERN = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')
_SEVERITY_VALUES = {s.value for s in Severity}
_GIT_ENV = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}


def _git(cwd: str, args: list[str], timeout: int = 60) -> str:
    result = subprocess.run(
        ['git', '-c', 'core.quotePath=false', *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f'git {args[0]} failed: {result.stderr.strip()}')
    return result.stdout


def merge_base(cwd: str, base_ref: str) -> str:
    return _git(cwd, ['merge-base', base_ref, 'HEAD'], timeout=30).strip()


def parse_changed_lines(diff_text: str) -> dict[str, set[int]]:
    changed: dict[str, set[int]] = {}
    current = None
    for line in diff_text.splitlines():
        if line.startswith('+++ '):
            path = line[4:].split('\t', 1)[0]
            current = None if path == '/dev/null' else path.removeprefix('b/')
            if current is not None:
                changed.setdefault(current, set())
            continue
        if current is None:
            continue
        match = _HUNK_PATTERN.match(line)
        if match:
            start = int(match.group(1))
            count = int(match.group(2)) if match.group(2) is not None else 1
            changed[current].update(range(start, start + count))
    return {path: lines for path, lines in changed.items() if lines}


def _diff_targets(base: str, include_worktree: bool) -> list[str]:
    return [base] if include_worktree else [base, 'HEAD']


def changed_lines(
    cwd: str, base_ref: str, include_worktree: bool = False, watch_paths: list[str] | None = None
) -> dict[str, set[int]]:
    base = merge_base(cwd, base_ref)
    diff = _git(
        cwd,
        [
            'diff',
            '-U0',
            '--no-color',
            '--no-ext-diff',
            *_diff_targets(base, include_worktree),
            *scope.pathspec_args(watch_paths or []),
        ],
    )
    return parse_changed_lines(diff)


def collect_change(
    cwd: str, base_ref: str, include_worktree: bool = False, watch_paths: list[str] | None = None
) -> tuple[str, str]:
    base = merge_base(cwd, base_ref)
    pathspec = scope.pathspec_args(watch_paths or [])
    commits = _git(cwd, ['log', '--reverse', '--no-color', '--format=%h %s', f'{base}..HEAD', *pathspec]).strip()
    if len(commits) > MAX_PROMPT_COMMITS_CHARS:
        commits = commits[:MAX_PROMPT_COMMITS_CHARS] + '\n... (commit list truncated)'
    targets = _diff_targets(base, include_worktree)
    diff = _git(cwd, ['diff', '--no-color', '--no-ext-diff', *targets, *pathspec]).strip()
    if len(diff) > MAX_PROMPT_DIFF_CHARS:
        stat = _git(cwd, ['diff', '--stat=200', '--no-color', *targets, *pathspec]).strip()
        cut = diff.rfind('\ndiff --git ', 0, MAX_PROMPT_DIFF_CHARS)
        shown = diff[: cut if cut > 0 else MAX_PROMPT_DIFF_CHARS]
        logger.warning('Diff is %d chars, embedding the first %d plus --stat', len(diff), len(shown))
        diff = (
            f'{shown}\n\n'
            '... DIFF TRUNCATED. Files changed in the full diff (read the ones not shown above in full '
            f'with your file-reading tool):\n{stat}'
        )
    if not diff:
        logger.warning('Empty diff between %s and %s', base[:8], 'worktree' if include_worktree else 'HEAD')
    return commits, diff


def anchor_findings(findings: list[Finding], changed: dict[str, set[int]]) -> None:
    for f in findings:
        if f.line is None or f.line in changed.get(f.file, set()):
            continue
        logger.info('Finding %s:%s not on a changed line, posting in summary instead of inline', f.file, f.line)
        f.inline = False
        f.fix = None


def parse_candidates(data: dict) -> list[Candidate]:
    return [
        Candidate(
            id=f'C{i}',
            kind='edge_case' if c.get('kind') == 'edge_case' else 'finding',
            finding=parse_finding(c),
            evidence=[str(e) for e in c.get('evidence') or []],
        )
        for i, c in enumerate(data.get('candidates', []), start=1)
    ]


def merge_verdicts(
    candidates: list[Candidate], verdicts: list[dict]
) -> tuple[list[Finding], list[Finding], list[tuple[Candidate, str]]]:
    known = {c.id for c in candidates}
    by_id: dict[str, dict] = {}
    for v in verdicts:
        vid = str(v.get('id', ''))
        if vid in known:
            by_id[vid] = v
        else:
            logger.warning('Ignoring verdict for unknown candidate %r', vid)

    findings: list[Finding] = []
    edge_cases: list[Finding] = []
    dropped: list[tuple[Candidate, str]] = []
    for c in candidates:
        v = by_id.get(c.id)
        if v is None:
            logger.warning('No verdict for %s (%s), treating as unverified edge case', c.id, c.finding.title)
            edge_cases.append(c.finding)
            continue
        verdict = v.get('verdict')
        if verdict == 'confirmed':
            if v.get('severity') in _SEVERITY_VALUES:
                c.finding.severity = Severity(v['severity'])
            findings.append(c.finding)
        elif verdict == 'dropped':
            dropped.append((c, str(v.get('reason', ''))))
        else:
            edge_cases.append(c.finding)
    return findings, edge_cases, dropped


_FINDING_PROPS = {
    'severity': {'type': 'string', 'enum': [s.value for s in Severity]},
    'category': {'type': 'string'},
    'title': {'type': 'string'},
    'file': {'type': 'string'},
    'line': {'type': ['integer', 'null']},
    'issue': {'type': 'string'},
    'fix': {'type': ['string', 'null']},
}

FIND_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'overview': {'type': 'string'},
        'candidates': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'kind': {'type': 'string', 'enum': ['finding', 'edge_case']},
                    **_FINDING_PROPS,
                    'evidence': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['kind', *_FINDING_PROPS, 'evidence'],
                'additionalProperties': False,
            },
        },
        'summary': {'type': 'string'},
    },
    'required': ['overview', 'candidates', 'summary'],
    'additionalProperties': False,
}

VERIFY_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'verdicts': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string'},
                    'verdict': {'type': 'string', 'enum': ['confirmed', 'edge_case', 'dropped']},
                    'severity': {'type': ['string', 'null']},
                    'reason': {'type': 'string'},
                },
                'required': ['id', 'verdict', 'severity', 'reason'],
                'additionalProperties': False,
            },
        },
        'summary': {'type': 'string'},
        'approve': {'type': 'boolean'},
        'approve_reason': {'type': ['string', 'null']},
    },
    'required': ['verdicts', 'summary', 'approve', 'approve_reason'],
    'additionalProperties': False,
}


def _usage_summary(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:1000]
    if not isinstance(data, dict):
        return raw[:1000]
    model = data.get('currentModel')
    metrics = (data.get('modelMetrics') or {}).get(model) or {}
    usage = metrics.get('usage') or {}
    requests = metrics.get('requests') or {}
    return (
        f'premium_requests={data.get("totalPremiumRequestCost")} model={model} '
        f'model_calls={requests.get("count")} input_tokens={usage.get("inputTokens")} '
        f'output_tokens={usage.get("outputTokens")} api_ms={data.get("totalApiDurationMs")}'
    )


def _run_pass(
    prompt: str,
    cwd: str,
    pass_config: PassConfig,
    add_dirs: list[str],
    cli_defaults: dict[CLI, list[str]] | None,
    schema: dict,
) -> dict:
    extra = [*pass_config.args, *context_dir_args(pass_config.cli, add_dirs)]
    usage_file = None
    if pass_config.cli == CLI.COPILOT:
        fd, usage_file = tempfile.mkstemp(prefix='reviewd-usage-', suffix='.json')
        os.close(fd)
        extra += ['--usage-output-file', usage_file]
    try:
        output = invoke_cli(
            prompt,
            cwd,
            cli=pass_config.cli,
            timeout=pass_config.timeout,
            model=pass_config.model,
            cli_args=extra,
            cli_defaults=cli_defaults,
            output_schema=schema,
        )
        if usage_file:
            usage = Path(usage_file).read_text().strip()
            if usage:
                logger.info('%s usage: %s', pass_config.cli.value, _usage_summary(usage))
            else:
                logger.warning('%s wrote no usage data to %s', pass_config.cli.value, usage_file)
        return extract_json(output)
    finally:
        if usage_file:
            Path(usage_file).unlink(missing_ok=True)


def _verify_with_retry(
    prompt: str,
    cwd: str,
    verify: PassConfig,
    add_dirs: list[str],
    cli_defaults: dict[CLI, list[str]] | None,
) -> dict:
    for attempt in range(1, VERIFY_ATTEMPTS + 1):
        try:
            return _run_pass(prompt, cwd, verify, add_dirs, cli_defaults, VERIFY_SCHEMA)
        except CLIAuthError:
            raise
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            if attempt == VERIFY_ATTEMPTS:
                raise RuntimeError(f'Verify pass failed after {VERIFY_ATTEMPTS} attempts: {e}') from e
            logger.warning('Verify pass failed (attempt %d), retrying: %s', attempt, e)
    raise AssertionError('unreachable')


def run_pipeline(
    cwd: str,
    pr: PRInfo,
    project_config: ProjectConfig,
    pipeline: PipelineConfig,
    cli_defaults: dict[CLI, list[str]] | None = None,
    watch_paths: list[str] | None = None,
) -> ReviewResult:
    t0 = time.monotonic()
    base_ref = pr.destination_branch if pr.is_local else f'origin/{pr.destination_branch}'
    base = merge_base(cwd, base_ref)
    commits, diff = collect_change(cwd, base, include_worktree=pr.is_local, watch_paths=watch_paths)
    change = {'commits': commits, 'diff': diff, 'scope_paths': watch_paths}

    contexts = []
    try:
        for repo in pipeline.context:
            contexts.append(prepare_context(repo, pr))
        add_dirs = [c.worktree for c in contexts if c.worktree]
        find_prompt = build_find_prompt(pr, project_config, contexts, **change)
        find_data = _run_pass(find_prompt, cwd, pipeline.find, add_dirs, cli_defaults, FIND_SCHEMA)
        candidates = parse_candidates(find_data)
        logger.info('Find pass produced %d candidates', len(candidates))

        summary = find_data.get('summary', '')
        approve = False
        approve_reason = None
        if pipeline.verify.enabled and (candidates or project_config.auto_approve.enabled):
            verify_prompt = build_verify_prompt(pr, project_config, contexts, candidates, **change)
            verify_data = _verify_with_retry(verify_prompt, cwd, pipeline.verify, add_dirs, cli_defaults)
            findings, edge_cases, dropped = merge_verdicts(candidates, verify_data.get('verdicts', []))
            for c, reason in dropped:
                logger.info(
                    'Verifier dropped %s [%s] %s:%s %r: %s',
                    c.id,
                    c.kind,
                    c.finding.file,
                    c.finding.line,
                    c.finding.title,
                    reason,
                )
            summary = verify_data.get('summary') or summary
            approve = bool(verify_data.get('approve', False))
            approve_reason = verify_data.get('approve_reason')
        else:
            findings = [c.finding for c in candidates if c.kind == 'finding']
            edge_cases = [c.finding for c in candidates if c.kind == 'edge_case']

        anchor_findings(findings, changed_lines(cwd, base, include_worktree=pr.is_local, watch_paths=watch_paths))
        logger.info('Pipeline result: %d findings, %d edge cases', len(findings), len(edge_cases))
        return ReviewResult(
            overview=find_data.get('overview', ''),
            findings=findings,
            summary=summary,
            approve=approve,
            approve_reason=approve_reason,
            duration_seconds=time.monotonic() - t0,
            edge_cases=edge_cases,
            context_refs=[c.label for c in contexts],
        )
    finally:
        for c in contexts:
            try:
                cleanup_context(c)
            except (OSError, subprocess.SubprocessError) as e:
                logger.warning('Could not clean up context worktree %s: %s', c.worktree, e)
