"""BitBucket provider: Basic auth (email:token) and null source commit handling."""

from __future__ import annotations

import httpx
import respx

from reviewd.providers.bitbucket import BitbucketProvider


def test_basic_auth_from_email_token():
    """email:token format → httpx Basic auth, not Bearer."""
    provider = BitbucketProvider('team', 'user@example.com:secret-token')
    assert provider.client._auth is not None
    # Bearer header should NOT be present
    assert 'Authorization' not in provider.client.headers


def test_bearer_auth_from_plain_token():
    """Plain token without @ → Bearer auth."""
    provider = BitbucketProvider('team', 'plain-oauth-token')
    assert provider.client._auth is None
    assert provider.client.headers['Authorization'] == 'Bearer plain-oauth-token'


@respx.mock
def test_null_source_commit_pr():
    """PR with deleted source branch (commit=None) → empty string, not crash."""
    respx.get('https://api.bitbucket.org/2.0/repositories/team/repo/pullrequests').mock(
        return_value=httpx.Response(
            200,
            json={
                'values': [
                    {
                        'id': 5,
                        'title': 'Old PR',
                        'author': {'display_name': 'bob'},
                        'source': {'branch': {'name': 'deleted-branch'}, 'commit': None},
                        'destination': {'branch': {'name': 'main'}},
                        'links': {'html': {'href': 'https://bb.org/pr/5'}},
                    }
                ],
            },
        ),
    )
    provider = BitbucketProvider('team', 'fake-token')
    prs = provider.list_open_prs('repo')
    assert len(prs) == 1
    assert prs[0].source_commit == ''
