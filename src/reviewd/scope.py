from __future__ import annotations

import shlex


def _normalize(watch_paths: list[str]) -> list[str]:
    return [p.strip().rstrip('/') for p in watch_paths if p.strip().rstrip('/')]


def file_in_scope(path: str, watch_paths: list[str]) -> bool:
    return any(path == prefix or path.startswith(f'{prefix}/') for prefix in _normalize(watch_paths))


def any_in_scope(paths: list[str], watch_paths: list[str]) -> bool:
    return any(file_in_scope(p, watch_paths) for p in paths)


def pathspec_args(watch_paths: list[str]) -> list[str]:
    dirs = _normalize(watch_paths)
    if not dirs:
        return []
    return ['--', *(f'{d}/' for d in dirs)]


def pathspec_suffix(watch_paths: list[str]) -> str:
    dirs = _normalize(watch_paths)
    if not dirs:
        return ''
    return ' -- ' + ' '.join(shlex.quote(f'{d}/') for d in dirs)
