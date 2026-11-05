from __future__ import annotations

from reviewd.state import MAX_FAILURES


def _fail(db, minutes_ago):
    db.start_review('repo', 1, 'abc')
    attempts = db.finish_review('repo', 1, 'abc', error='boom')
    db.conn.execute(f"UPDATE reviews SET finished_at = datetime('now', '-{minutes_ago} minutes')")
    db.conn.commit()
    return attempts


def test_no_row_not_in_backoff(state_db):
    assert state_db.in_backoff('repo', 1, 'abc') is False


def test_first_failure_waits_5_minutes(state_db):
    assert _fail(state_db, 2) == 1
    assert state_db.in_backoff('repo', 1, 'abc') is True
    state_db.conn.execute("UPDATE reviews SET finished_at = datetime('now', '-6 minutes')")
    assert state_db.in_backoff('repo', 1, 'abc') is False


def test_second_failure_waits_15_minutes(state_db):
    _fail(state_db, 10)
    assert _fail(state_db, 10) == 2
    assert state_db.in_backoff('repo', 1, 'abc') is True


def test_gives_up_after_max_failures(state_db):
    for _ in range(MAX_FAILURES):
        _fail(state_db, 1000)
    assert state_db.in_backoff('repo', 1, 'abc') is True


def test_new_commit_starts_fresh(state_db):
    for _ in range(MAX_FAILURES):
        _fail(state_db, 1000)
    assert state_db.in_backoff('repo', 1, 'def') is False


def test_uncounted_failure_does_not_back_off(state_db):
    state_db.start_review('repo', 1, 'abc')
    assert state_db.finish_review('repo', 1, 'abc', error='shutdown', count_failure=False) == 0
    assert state_db.in_backoff('repo', 1, 'abc') is False


def test_attempts_column_added_to_existing_db(tmp_path):
    import sqlite3

    from reviewd.state import StateDB

    path = tmp_path / 'old.db'
    conn = sqlite3.connect(path)
    conn.execute(
        'CREATE TABLE reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, repo_slug TEXT NOT NULL, pr_id INTEGER NOT NULL, '
        "source_commit TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMP DEFAULT "
        'CURRENT_TIMESTAMP, finished_at TIMESTAMP, error_message TEXT, UNIQUE(repo_slug, pr_id, source_commit))'
    )
    conn.commit()
    conn.close()
    db = StateDB(str(path))
    db.start_review('repo', 1, 'abc')
    assert db.finish_review('repo', 1, 'abc', error='boom') == 1
    db.close()
