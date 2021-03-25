# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
import os
import os.path as osp
import re
import shutil
import sys
from glob import glob
from pathlib import Path
from unittest.mock import call
from unittest.mock import MagicMock
from unittest.mock import patch
from unittest.mock import PropertyMock

import pytest
from pytest import fixture

from release_helper import changelog
from release_helper import cli
from release_helper import npm
from release_helper import python
from release_helper import util
from release_helper.tests.util import CHANGELOG_ENTRY
from release_helper.tests.util import create_npm_package
from release_helper.tests.util import create_python_package
from release_helper.tests.util import HTML_URL
from release_helper.tests.util import mock_changelog_entry
from release_helper.tests.util import MockHTTPResponse
from release_helper.tests.util import MockRequestResponse
from release_helper.tests.util import PR_ENTRY
from release_helper.tests.util import REPO_DATA
from release_helper.tests.util import TOML_CONFIG
from release_helper.tests.util import VERSION_SPEC
from release_helper.util import bump_version
from release_helper.util import normalize_path
from release_helper.util import run


def test_prep_env_simple(py_package, runner):
    """Standard local run with no env variables."""
    result = runner(["prep-env", "--version-spec", "1.0.1"], env=dict(GITHUB_ACTION=""))
    assert "branch=bar" in result.output
    assert "version=1.0.1" in result.output
    assert "is_prerelease=false" in result.output


def test_prep_env_pr(py_package, runner):
    """With GITHUB_BASE_REF (Pull Request)"""
    env = dict(GITHUB_BASE_REF="foo", RH_VERSION_SPEC="1.0.1", GITHUB_ACTION="")
    result = runner(["prep-env"], env=env)
    assert "branch=foo" in result.output


def test_prep_env_bad_version(py_package, runner):
    with pytest.raises(AssertionError):
        runner(["prep-env", "--version-spec", "a1.0.1"], env=dict(GITHUB_ACTION=""))


def test_prep_env_tag_exists(py_package, runner):
    run("git tag v1.0.1")
    with pytest.raises(AssertionError):
        runner(["prep-env", "--version-spec", "1.0.1"], env=dict(GITHUB_ACTION=""))


def test_prep_env_full(py_package, tmp_path, mocker, runner):
    """Full GitHub Actions simulation (Push)"""
    version_spec = "1.0.1a1"

    env_file = tmp_path / "github.env"

    env = dict(
        GITHUB_REF="refs/heads/foo",
        GITHUB_WORKFLOW="check-release",
        GITHUB_ACTIONS="true",
        GITHUB_REPOSITORY="baz/bar",
        RH_VERSION_SPEC=version_spec,
        GITHUB_ENV=str(env_file),
        GITHUB_ACTOR="snuffy",
        GITHUB_ACCESS_TOKEN="abc123",
    )

    # Fake out the version and source repo responses
    mock_run = mocker.patch("release_helper.util.run")
    mock_run.return_value = version_spec

    runner(["prep-env"], env=env)
    mock_run.assert_has_calls(
        [
            call(
                'git config --global user.email "41898282+github-actions[bot]@users.noreply.github.com"'
            ),
            call('git config --global user.name "GitHub Action"'),
            call("git remote"),
            call("git remote add upstream http://snuffy:abc123@github.com/baz/bar.git"),
            call("git fetch upstream --tags"),
            call("git fetch upstream foo --tags"),
            call("git branch"),
            call("git checkout -B foo upstream/foo"),
            call("tbump --non-interactive --only-patch 1.0.1a1"),
            call("python setup.py --version"),
        ]
    )
    text = env_file.read_text(encoding="utf-8")
    assert "BRANCH=foo" in text
    assert f"VERSION={version_spec}" in text
    assert "IS_PRERELEASE=true" in text
    assert "REPOSITORY=baz/bar" in text


def test_build_changelog(py_package, mocker, runner):
    run("pre-commit run -a")

    changelog_path = py_package / "CHANGELOG.md"

    runner(["prep-env", "--version-spec", VERSION_SPEC])

    mocked_gen = mocker.patch("release_helper.changelog.generate_activity_md")
    mocked_gen.return_value = CHANGELOG_ENTRY
    runner(["build-changelog", "--changelog-path", changelog_path])
    text = changelog_path.read_text(encoding="utf-8")
    assert changelog.START_MARKER in text
    assert changelog.END_MARKER in text
    assert PR_ENTRY in text

    assert len(re.findall(changelog.START_MARKER, text)) == 1
    assert len(re.findall(changelog.END_MARKER, text)) == 1

    run("pre-commit run -a")


def test_build_changelog_existing(py_package, mocker, runner):
    changelog_path = py_package / "CHANGELOG.md"

    runner(["prep-env", "--version-spec", VERSION_SPEC])

    mocked_gen = mocker.patch("release_helper.changelog.generate_activity_md")
    mocked_gen.return_value = CHANGELOG_ENTRY
    runner(["build-changelog", "--changelog-path", changelog_path])
    text = changelog_path.read_text(encoding="utf-8")
    text = text.replace("defining contributions", "Definining contributions")
    changelog_path.write_text(text, encoding="utf-8")

    # Commit the change
    run('git commit -a -m "commit changelog"')

    mocked_gen.return_value = CHANGELOG_ENTRY
    runner(["build-changelog", "--changelog-path", changelog_path])
    text = changelog_path.read_text(encoding="utf-8")
    assert "Definining contributions" in text, text
    assert not "defining contributions" in text, text

    assert len(re.findall(changelog.START_MARKER, text)) == 1
    assert len(re.findall(changelog.END_MARKER, text)) == 1

    run("pre-commit run -a")


def test_draft_changelog_full(py_package, mocker, runner, open_mock):
    mock_changelog_entry(py_package, runner, mocker)
    runner(["draft-changelog", "--version-spec", VERSION_SPEC])
    open_mock.assert_called_once()


def test_draft_changelog_dry_run(npm_package, mocker, runner):
    mock_changelog_entry(npm_package, runner, mocker)
    runner(["draft-changelog", "--dry-run", "--version-spec", VERSION_SPEC])


def test_draft_changelog_lerna(workspace_package, mocker, runner, open_mock):
    mock_changelog_entry(workspace_package, runner, mocker)
    runner(["draft-changelog", "--version-spec", VERSION_SPEC])
    open_mock.assert_called_once()


def test_check_links(py_package, runner):
    readme = py_package / "README.md"
    text = readme.read_text(encoding="utf-8")
    text += "\nhttps://apod.nasa.gov/apod/astropix.html"
    readme.write_text(text, encoding="utf-8")

    runner(["check-links"])

    foo = py_package / "FOO.md"
    foo.write_text("http://127.0.0.1:5555")

    runner(["check-links", "--ignore-glob", "FOO.md"])


def test_check_changelog(py_package, tmp_path, mocker, runner):
    changelog_entry = mock_changelog_entry(py_package, runner, mocker)
    output = tmp_path / "output.md"

    # prep the release
    bump_version(VERSION_SPEC)

    runner(
        ["check-changelog", "--changelog-path", changelog_entry, "--output", output],
    )

    assert PR_ENTRY in output.read_text(encoding="utf-8")
    text = changelog_entry.read_text(encoding="utf-8")
    assert f"{changelog.START_MARKER}\n\n## {VERSION_SPEC}" in text
    assert changelog.END_MARKER in text


def test_build_python(py_package, runner, build_mock):
    runner(["build-python"])


def test_build_python_setup(py_package, runner):
    py_package.joinpath("pyproject.toml").unlink()
    runner(["build-python"])


def test_build_python_npm(npm_package, runner, build_mock):
    runner(["build-python"])


def test_check_python(py_package, runner, build_mock):
    runner(["build-python"])
    runner(["check-python"])


def test_handle_npm(npm_package, runner):
    runner(["build-npm"])
    runner(["check-npm"])


def test_handle_npm_lerna(workspace_package, runner):
    runner(["build-npm"])
    runner(["check-npm"])


def test_check_manifest(py_package, runner):
    runner(["check-manifest"])


def test_check_manifest_npm(npm_package, runner):
    runner(["check-manifest"])


def test_tag_release(py_package, runner, build_mock):
    # Prep the env
    runner(["prep-env", "--version-spec", VERSION_SPEC])
    # Create the dist files
    util.run("python -m build .")
    # Tag the release
    runner(["tag-release"])


def test_draft_release_dry_run(py_dist, mocker, runner, open_mock):
    # Publish the release - dry run
    runner(["draft-release", "--dry-run", "--post-version-spec", "1.1.0.dev0"])
    assert len(open_mock.call_args) == 2


def test_draft_release_final(npm_dist, runner, mocker, open_mock):
    # Publish the release
    runner(["draft-release"])
    assert len(open_mock.call_args) == 2


def test_delete_release(npm_dist, runner, mocker, open_mock):
    # Publish the release
    # Mimic being on GitHub actions so we get the magic output
    os.environ["GITHUB_ACTIONS"] = "true"
    result = runner(["draft-release", "--dry-run"])
    assert len(open_mock.call_args) == 2

    url = ""
    for line in result.output.splitlines():
        match = re.match(r"::set-output name=release_url::(.*)", line)
        if match:
            url = match.groups()[0]

    # Delete the release
    data = dict(assets=[dict(id="bar")])
    open_mock.return_value = MockHTTPResponse([data])
    runner(["delete-release", url])
    assert len(open_mock.call_args) == 2


@pytest.mark.skipif(
    os.name == "nt" and sys.version_info.major == 3 and sys.version_info.minor < 8,
    reason="See https://bugs.python.org/issue26660",
)
def test_extract_dist_py(py_package, runner, mocker, open_mock, tmp_path):
    changelog_entry = mock_changelog_entry(py_package, runner, mocker)

    # Create the dist files
    run("python -m build .")

    # Finalize the release
    runner(["tag-release"])

    os.makedirs("staging")
    shutil.move("dist", "staging")

    def helper(path, **kwargs):
        return MockRequestResponse(f"staging/dist/{path}")

    get_mock = mocker.patch("requests.get", side_effect=helper)

    tag_name = "bar"

    dist_names = [osp.basename(f) for f in glob("staging/dist/*.*")]
    releases = [
        dict(
            tag_name=tag_name,
            target_commitish="main",
            assets=[dict(name=dist_name, url=dist_name) for dist_name in dist_names],
        )
    ]
    sha = run("git rev-parse HEAD")
    tags = [dict(ref=f"refs/tags/{tag_name}", object=dict(sha=sha))]
    url = normalize_path(os.getcwd())
    open_mock.side_effect = [
        MockHTTPResponse(releases),
        MockHTTPResponse(tags),
        MockHTTPResponse(dict(html_url=url)),
    ]

    runner(["extract-release", HTML_URL])
    assert len(open_mock.mock_calls) == 3
    assert len(get_mock.mock_calls) == len(dist_names) == 2


@pytest.mark.skipif(
    os.name == "nt" and sys.version_info.major == 3 and sys.version_info.minor < 8,
    reason="See https://bugs.python.org/issue26660",
)
def test_extract_dist_npm(npm_dist, runner, mocker, open_mock, tmp_path):

    os.makedirs("staging")
    shutil.move("dist", "staging")

    def helper(path, **kwargs):
        return MockRequestResponse(f"staging/dist/{path}")

    get_mock = mocker.patch("requests.get", side_effect=helper)

    dist_names = [osp.basename(f) for f in glob("staging/dist/*.tgz")]
    url = normalize_path(os.getcwd())
    tag_name = "bar"
    releases = [
        dict(
            tag_name=tag_name,
            target_commitish="main",
            assets=[dict(name=dist_name, url=dist_name) for dist_name in dist_names],
        )
    ]
    sha = run("git rev-parse HEAD")
    tags = [dict(ref=f"refs/tags/{tag_name}", object=dict(sha=sha))]
    open_mock.side_effect = [
        MockHTTPResponse(releases),
        MockHTTPResponse(tags),
        MockHTTPResponse(dict(html_url=url)),
    ]

    runner(["extract-release", HTML_URL])
    assert len(open_mock.mock_calls) == 3
    assert len(get_mock.mock_calls) == len(dist_names) == 3


def test_publish_release_py(py_dist, runner, mocker, open_mock):
    open_mock.side_effect = [MockHTTPResponse([REPO_DATA]), MockHTTPResponse()]

    orig_run = util.run
    called = 0

    def wrapped(cmd, **kwargs):
        nonlocal called
        if cmd.startswith("twine upload"):
            called += 1
            return ""
        return orig_run(cmd, **kwargs)

    mock_run = mocker.patch("release_helper.util.run", wraps=wrapped)

    runner(["publish-release", HTML_URL])
    assert len(open_mock.call_args) == 2
    assert called == 2, called


def test_publish_release_npm(npm_dist, runner, mocker, open_mock):
    open_mock.side_effect = [MockHTTPResponse([REPO_DATA]), MockHTTPResponse()]
    runner(
        [
            "publish-release",
            HTML_URL,
            "--npm_token",
            "abc",
            "--npm_cmd",
            "npm publish --dry-run",
        ]
    )
    assert len(open_mock.call_args) == 2


def test_config_file(py_package, runner, mocker):
    config = util.RELEASE_HELPER_CONFIG
    config.write_text(TOML_CONFIG, encoding="utf-8")

    orig_run = util.run
    hooked = 0
    called = False

    def wrapped(cmd, **kwargs):
        nonlocal called, hooked
        if cmd.startswith("python -m build --outdir foo"):
            called = True
            return ""
        if cmd.startswith("python setup.py"):
            hooked += 1
            return ""
        return orig_run(cmd, **kwargs)

    mock_run = mocker.patch("release_helper.util.run", wraps=wrapped)

    runner(["build-python"])
    assert hooked == 3, hooked
    assert called


def test_config_file_env_override(py_package, runner, mocker):
    config = util.RELEASE_HELPER_CONFIG
    config.write_text(TOML_CONFIG, encoding="utf-8")

    orig_run = util.run
    called = False
    hooked = 0

    def wrapped(cmd, **kwargs):
        nonlocal called, hooked
        if cmd.startswith("python -m build --outdir bar"):
            called = True
            return ""
        if cmd.startswith("python setup.py"):
            hooked += 1
            return ""
        return orig_run(cmd, **kwargs)

    mock_run = mocker.patch("release_helper.util.run", wraps=wrapped)

    os.environ["RH_DIST_DIR"] = "bar"
    runner(["build-python"])
    assert hooked == 3, hooked
    assert called


def test_config_file_cli_override(py_package, runner, mocker):
    config = util.RELEASE_HELPER_CONFIG
    config.write_text(TOML_CONFIG, encoding="utf-8")

    orig_run = util.run
    called = False
    hooked = 0

    def wrapped(cmd, **kwargs):
        nonlocal called, hooked
        if cmd.startswith("python -m build --outdir bar"):
            called = True
            return ""
        if cmd.startswith("python setup.py"):
            hooked += 1
            return ""
        return orig_run(cmd, **kwargs)

    mock_run = mocker.patch("release_helper.util.run", wraps=wrapped)

    runner(["build-python", "--dist-dir", "bar"])
    assert hooked == 3, hooked
    assert called


def test_forwardport_changelog(npm_package, runner, mocker, open_mock):
    # Create a branch with a changelog entry
    util.run("git checkout -b backport_branch")
    util.run("git push upstream backport_branch")
    mock_changelog_entry(npm_package, runner, mocker)
    util.run('git commit -a -m "Add changelog entry"')
    util.run(f"git tag v{VERSION_SPEC}")

    # Run the forwardport workflow against default branch
    runner(["forwardport-changelog", f"v{VERSION_SPEC}"])

    open_mock.assert_called_once()
