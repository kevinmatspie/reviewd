"""State DB thread-safety: concurrent reads and writes don't corrupt data."""

from __future__ import annotations

import threading

from reviewd.state import StateDB


def test_concurrent_start_finish(tmp_path):
    """Multiple threads starting and finishing reviews simultaneously."""
    db = StateDB(str(tmp_path / 'test.db'))
    errors = []

    def _review(pr_id: int):
        try:
            commit = f'commit-{pr_id}'
            db.start_review('repo', pr_id, commit)
            db.finish_review('repo', pr_id, commit)
            assert db.has_review('repo', pr_id, commit)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_review, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    db.close()
    assert errors == [], f'Errors in threads: {errors}'


def test_concurrent_comment_tracking(tmp_path):
    """Multiple threads recording comments for different PRs."""
    db = StateDB(str(tmp_path / 'test.db'))
    errors = []

    def _record(pr_id: int):
        try:
            for comment_id in range(pr_id * 100, pr_id * 100 + 5):
                db.record_comment('repo', pr_id, comment_id)
            ids = db.get_comment_ids('repo', pr_id)
            assert len(ids) == 5
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_record, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    db.close()
    assert errors == [], f'Errors in threads: {errors}'
