"""JSON extraction from AI output: edge cases."""

from __future__ import annotations

import pytest

from reviewd.reviewer import extract_json, parse_review_result


def test_extract_last_json_block():
    """Multiple JSON blocks → extract the last one."""
    output = '```json\n{"a": 1}\n```\nSome text\n```json\n{"b": 2}\n```'
    assert extract_json(output) == {'b': 2}


def test_no_json_block_raises():
    output = 'No JSON here, just text.'
    with pytest.raises(ValueError, match='No JSON block found'):
        extract_json(output)


def test_malformed_json_raises():
    output = '```json\n{broken json\n```'
    with pytest.raises(ValueError, match='Malformed JSON'):
        extract_json(output)


def test_parse_unknown_severity_defaults():
    """Unknown severity string → defaults to suggestion."""
    data = {
        'overview': 'test',
        'findings': [{'severity': 'banana', 'title': 'Bad', 'file': 'x.py', 'issue': 'err'}],
        'summary': 'done',
    }
    result = parse_review_result(data)
    assert result.findings[0].severity.value == 'suggestion'


def test_parse_empty_findings():
    data = {'overview': 'Clean', 'findings': [], 'summary': 'LGTM'}
    result = parse_review_result(data)
    assert result.findings == []
    assert result.approve is False
