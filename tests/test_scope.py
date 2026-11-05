from reviewd import scope


def test_file_in_scope_matches_under_folder():
    assert scope.file_in_scope('Postman/collections/api.json', ['Postman/'])
    assert scope.file_in_scope('Postman/collections/api.json', ['Postman'])


def test_file_in_scope_respects_directory_boundary():
    assert not scope.file_in_scope('PostmanX/thing.json', ['Postman'])
    assert not scope.file_in_scope('src/Postman/x', ['Postman'])


def test_file_in_scope_exact_path():
    assert scope.file_in_scope('Postman', ['Postman'])


def test_file_in_scope_multiple_paths_with_spaces():
    watch = ['Postman/', 'QA Test Event .NET Project']
    assert scope.file_in_scope('QA Test Event .NET Project/Program.cs', watch)
    assert not scope.file_in_scope('WebApps/index.html', watch)


def test_any_in_scope():
    watch = ['Postman/']
    assert scope.any_in_scope(['WebApps/a', 'Postman/b'], watch)
    assert not scope.any_in_scope(['WebApps/a', 'SSMS/b'], watch)


def test_empty_watch_paths_scopes_nothing():
    assert not scope.file_in_scope('anything', [])
    assert not scope.any_in_scope(['a', 'b'], [])
    assert scope.pathspec_args([]) == []
    assert scope.pathspec_suffix([]) == ''


def test_pathspec_args_for_subprocess():
    assert scope.pathspec_args(['Postman/', 'SSMS']) == ['--', 'Postman/', 'SSMS/']


def test_pathspec_suffix_quotes_spaces():
    suffix = scope.pathspec_suffix(['Postman', 'QA Test Event .NET Project'])
    assert suffix == " -- Postman/ 'QA Test Event .NET Project/'"


def test_normalize_ignores_blank_entries():
    assert scope.pathspec_args(['', '  ', 'Postman/']) == ['--', 'Postman/']
