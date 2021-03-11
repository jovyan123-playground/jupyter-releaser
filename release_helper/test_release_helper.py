# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
import json
import os
import os.path as osp
import re
import shlex
import shutil
import sys
from glob import glob
from pathlib import Path
from unittest.mock import call
from unittest.mock import MagicMock
from unittest.mock import patch
from unittest.mock import PropertyMock

from click.testing import CliRunner
from github.GitRelease import GitRelease
from github.NamedUser import NamedUser
from github.PaginatedList import PaginatedList
from github.PullRequest import PullRequest
from github.Repository import Repository
from github.Requester import Requester
from github.Workflow import Workflow
from pytest import fixture

from release_helper import cli
from release_helper.cli import bump_version
from release_helper.cli import normalize_path
from release_helper.cli import run

PR_ENTRY = "Mention the required GITHUB_ACCESS_TOKEN [#1](https://github.com/executablebooks/github-activity/pull/1) ([@consideRatio](https://github.com/consideRatio))"

CHANGELOG_ENTRY = f"""
# master@{{2019-09-01}}...master@{{2019-11-01}}

([full changelog](https://github.com/executablebooks/github-activity/compare/479cc4b2f5504945021e3c4ee84818a10fabf810...ed7f1ed78b523c6b9fe6b3ac29e834087e299296))

## Merged PRs

* defining contributions [#14](https://github.com/executablebooks/github-activity/pull/14) ([@choldgraf](https://github.com/choldgraf))
* updating CLI for new tags [#12](https://github.com/executablebooks/github-activity/pull/12) ([@choldgraf](https://github.com/choldgraf))
* fixing link to changelog with refs [#11](https://github.com/executablebooks/github-activity/pull/11) ([@choldgraf](https://github.com/choldgraf))
* adding contributors list [#10](https://github.com/executablebooks/github-activity/pull/10) ([@choldgraf](https://github.com/choldgraf))
* some improvements to `since` and opened issues list [#8](https://github.com/executablebooks/github-activity/pull/8) ([@choldgraf](https://github.com/choldgraf))
* Support git references etc. [#6](https://github.com/executablebooks/github-activity/pull/6) ([@consideRatio](https://github.com/consideRatio))
* adding authentication information [#2](https://github.com/executablebooks/github-activity/pull/2) ([@choldgraf](https://github.com/choldgraf))
* {PR_ENTRY}

## Contributors to this release

([GitHub contributors page for this release](https://github.com/executablebooks/github-activity/graphs/contributors?from=2019-09-01&to=2019-11-01&type=c))

[@betatim](https://github.com/search?q=repo%3Aexecutablebooks%2Fgithub-activity+involves%3Abetatim+updated%3A2019-09-01..2019-11-01&type=Issues) | [@choldgraf](https://github.com/search?q=repo%3Aexecutablebooks%2Fgithub-activity+involves%3Acholdgraf+updated%3A2019-09-01..2019-11-01&type=Issues) | [@consideRatio](https://github.com/search?q=repo%3Aexecutablebooks%2Fgithub-activity+involves%3AconsideRatio+updated%3A2019-09-01..2019-11-01&type=Issues)
"""

SETUP_CFG_TEMPLATE = """
[metadata]
name = foo
version = attr: foo.__version__
description = My package description
long_description = file: README.md
long_description_content_type = text/markdown
license = BSD 3-Clause License
author = foo
author_email = foo@foo.com
url = http://foo.com

[options]
zip_safe = False
include_package_data = True
py_modules = foo
"""

SETUP_PY_TEMPLATE = """__import__("setuptools").setup()\n"""


PYPROJECT_TEMPLATE = """
[build-system]
requires = ["setuptools>=40.8.0", "wheel"]
build-backend = "setuptools.build_meta"
"""

PY_MODULE_TEMPLATE = '__version__ = "0.0.1"\n'

TBUMP_BASE_TEMPLATE = r"""
[version]
current = "0.0.1"
regex = '''
  (?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)
  ((?P<channel>a|b|rc|.dev)(?P<release>\d+))?
'''

[git]
message_template = "Bump to {new_version}"
tag_template = "v{new_version}"
"""

TBUMP_PY_TEMPLATE = """
[[file]]
src = "foo.py"
"""

TBUMP_NPM_TEMPLATE = """
[[file]]
src = "package.json"
search = '"version": "{current_version}"'
"""

MANIFEST_TEMPLATE = """
include *.md
include *.toml
include *.yaml
"""

CHANGELOG_TEMPLATE = f"""# Changelog

{cli.START_MARKER}

{cli.END_MARKER}

## 0.0.1

Initial commit
"""


@fixture(autouse=True)
def mock_env_vars():
    """Clear any GitHub related environment variables"""
    env = os.environ.copy()
    for key in list(env.keys()):
        if key.startswith("GITHUB_"):
            del env[key]
    with patch.dict(os.environ, env, clear=True):
        yield


@fixture
def git_repo(tmp_path):
    prev_dir = os.getcwd()
    os.chdir(tmp_path)

    run("git init")
    run("git config user.name snuffy")
    run("git config user.email snuffy@sesame.com")

    run("git checkout -b foo")
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("dist/*\nbuild/*\n", encoding="utf-8")
    run("git add .")
    run('git commit -m "foo"')
    run("git tag v0.0.1")
    run(f"git remote add upstream {normalize_path(tmp_path)}")

    yield tmp_path
    os.chdir(prev_dir)


def create_python_package(git_repo):
    setuppy = git_repo / "setup.py"
    setuppy.write_text(SETUP_PY_TEMPLATE, encoding="utf-8")

    setuppy = git_repo / "setup.cfg"
    setuppy.write_text(SETUP_CFG_TEMPLATE, encoding="utf-8")

    tbump = git_repo / "tbump.toml"
    tbump.write_text(TBUMP_BASE_TEMPLATE + TBUMP_PY_TEMPLATE, encoding="utf-8")

    pyproject = git_repo / "pyproject.toml"
    pyproject.write_text(PYPROJECT_TEMPLATE, encoding="utf-8")

    readme = git_repo / "README.md"
    readme.write_text("Hello from foo project\n", encoding="utf-8")

    foopy = git_repo / "foo.py"
    foopy.write_text(PY_MODULE_TEMPLATE, encoding="utf-8")

    changelog = git_repo / "CHANGELOG.md"
    changelog.write_text(CHANGELOG_TEMPLATE, encoding="utf-8")

    manifest = git_repo / "MANIFEST.in"
    manifest.write_text(MANIFEST_TEMPLATE, encoding="utf-8")

    here = Path(__file__).parent
    text = here.parent.joinpath(".pre-commit-config.yaml").read_text(encoding="utf-8")

    pre_commit = git_repo / ".pre-commit-config.yaml"
    pre_commit.write_text(text, encoding="utf-8")

    run("git add .")
    run('git commit -m "initial python package"')
    return git_repo


def create_npm_package(git_repo):
    npm = normalize_path(shutil.which("npm"))
    run(f"{npm} init -y")
    git_repo.joinpath("index.js").write_text('console.log("hello")', encoding="utf-8")
    run("git add .")
    run('git commit -m "initial npm package"')
    return git_repo


@fixture
def py_package(git_repo):
    pkg = create_python_package(git_repo)
    run("git checkout -b bar foo")
    return pkg


@fixture
def npm_package(git_repo):
    pkg = create_npm_package(git_repo)
    run("git checkout -b bar foo")
    return pkg


def test_get_branch(git_repo):
    assert cli.get_branch() == "foo"


def test_get_repo(git_repo):
    repo = f"{git_repo.parent.name}/{git_repo.name}"
    assert cli.get_repo("upstream") == repo

    gh_repo_name = "foo/bar"
    gh_repo = Repository(None, dict(), dict(), True)

    with patch.dict(os.environ, {"GITHUB_REPOSITORY": gh_repo_name}), patch.object(
        cli.Github, "get_repo", return_value=gh_repo
    ) as mock_method:
        resp = cli.get_repo(gh_repo_name, auth="baz")
        mock_method.assert_called_with(gh_repo_name)


def test_get_version_python(py_package):
    assert cli.get_version() == "0.0.1"
    bump_version("0.0.2a0")
    assert cli.get_version() == "0.0.2a0"


def test_get_version_npm(npm_package):
    assert cli.get_version() == "1.0.0"
    npm = normalize_path(shutil.which("npm"))
    run(f"{npm} version patch")
    assert cli.get_version() == "1.0.1"


def test_format_pr_entry():
    gh_repo = Repository(None, dict(), dict(), True)
    pull = PullRequest(None, dict(), dict(), True)
    user = NamedUser(None, dict(), dict(), True)
    gh_repo.get_pull = mock = MagicMock(return_value=pull)
    with patch.object(
        cli.Github, "get_repo", return_value=gh_repo
    ) as mock_method, patch(
        "github.PullRequest.PullRequest.user", new_callable=PropertyMock
    ) as mock_user:
        mock_user.return_value = user
        resp = cli.format_pr_entry("foo", 121, auth="baz")
        mock_method.assert_called_with("foo")
        mock.assert_called_once()

    assert resp.startswith("- ")


def test_get_workflow_path():
    gh_repo = Repository(None, dict(), dict(), True)
    requester = MagicMock()
    name = "Foo Bar"
    path = ".github/workflows/foo_bar.yml"

    def requestJsonAndCheck(*args, **kwargs):
        return dict(), [dict(name=name, path=path)]

    requester.requestJsonAndCheck = requestJsonAndCheck
    workflows = PaginatedList(Workflow, requester, "", dict(), dict())
    gh_repo.get_workflows = mock = MagicMock(return_value=workflows)
    repo = "foo/bar"
    with patch.object(
        cli.Github, "get_repo", return_value=gh_repo
    ) as mock_method, patch.dict(
        os.environ, {"GITHUB_WORKFLOW": name, "GITHUB_REPOSITORY": repo}
    ):
        assert cli.get_workflow_path() == path
        mock.assert_called_once()
        mock_method.assert_called_with(repo)


def test_get_changelog_entry(py_package):
    version = cli.get_version()

    with patch("release_helper.cli.generate_activity_md") as mocked_gen:
        mocked_gen.return_value = CHANGELOG_ENTRY
        resp = cli.get_changelog_entry("foo", "bar/baz", version)
        mocked_gen.assert_called_with("bar/baz", since="v0.0.1", kind="pr", auth=None)

    assert f"## {version}" in resp
    assert PR_ENTRY in resp

    with patch("release_helper.cli.generate_activity_md") as mocked_gen:
        mocked_gen.return_value = CHANGELOG_ENTRY
        resp = cli.get_changelog_entry(
            "foo", "bar/baz", version, resolve_backports=True, auth="bizz"
        )
        mocked_gen.assert_called_with("bar/baz", since="v0.0.1", kind="pr", auth="bizz")

    assert f"## {version}" in resp
    assert PR_ENTRY in resp


def test_compute_sha256(py_package):
    assert len(cli.compute_sha256(py_package / "CHANGELOG.md")) == 64


def test_create_release_commit(py_package):
    bump_version("0.0.2a0")
    version = cli.get_version()
    run("python -m build .")
    shas = cli.create_release_commit(version)
    assert normalize_path("dist/foo-0.0.2a0.tar.gz") in shas
    assert normalize_path("dist/foo-0.0.2a0-py3-none-any.whl") in shas
    shutil.rmtree(py_package / "dist")

    # Add an npm package and test with that
    create_npm_package(py_package)
    with open(py_package / "package.json") as fid:
        data = json.load(fid)
    data["version"] = version
    with open(py_package / "package.json", "w") as fid:
        json.dump(data, fid, indent=4)
    txt = (py_package / "tbump.toml").read_text(encoding="utf-8")
    txt += TBUMP_NPM_TEMPLATE
    (py_package / "tbump.toml").write_text(txt, encoding="utf-8")
    bump_version("0.0.2a1")
    version = cli.get_version()
    run("python -m build .")
    shas = cli.create_release_commit(version)
    assert len(shas) == 3
    assert normalize_path("dist/foo-0.0.2a1.tar.gz") in shas


def test_bump_version(py_package):
    runner = CliRunner()
    for spec in ["1.0.1", "1.0.1.dev1", "1.0.3a4"]:
        bump_version(spec)
        assert cli.get_version() == spec


def test_prep_env_simple(py_package):
    """Standard local run with no env variables."""
    runner = CliRunner()
    result = runner.invoke(
        cli.main, ["prep-env", "--version-spec", "1.0.1"], env=dict(GITHUB_ACTION="")
    )
    assert result.exit_code == 0, result.output
    assert "branch=bar" in result.output
    assert "version=1.0.1" in result.output
    assert "is_prerelease=false" in result.output


def test_prep_env_pr(py_package):
    """With GITHUB_BASE_REF (Pull Request)"""
    runner = CliRunner()
    env = dict(GITHUB_BASE_REF="foo", VERSION_SPEC="1.0.1", GITHUB_ACTION="")
    result = runner.invoke(cli.main, ["prep-env"], env=env)
    assert result.exit_code == 0, result.output
    assert "branch=foo" in result.output


def test_prep_env_full(py_package, tmp_path):
    """Full GitHub Actions simulation (Push)"""
    runner = CliRunner()
    version_spec = "1.0.1a1"

    workflow_path = ".github/workflows/check-release.yml"
    workflow = Path(f"{cli.HERE}/../{workflow_path}")
    workflow = workflow.resolve()
    os.makedirs(py_package / ".github/workflows")
    shutil.copy(workflow, py_package / ".github/workflows")

    env_file = tmp_path / "github.env"

    env = dict(
        GITHUB_REF="refs/heads/foo",
        GITHUB_WORKFLOW="check-release",
        GITHUB_ACTIONS="true",
        GITHUB_REPOSITORY="baz/bar",
        VERSION_SPEC=version_spec,
        GITHUB_ENV=str(env_file),
        GITHUB_ACTOR="snuffy",
        GITHUB_ACCESS_TOKEN="abc123",
    )
    with patch("release_helper.cli.run") as mock_run, patch(
        "release_helper.cli.get_repo"
    ) as mocked_get_repo, patch(
        "release_helper.cli.get_workflow_path"
    ) as mocked_get_workflow_path:
        # Fake out the version and source repo responses
        mock_run.return_value = version_spec
        mocked_get_workflow_path.return_value = workflow_path
        mocked_get_repo.return_value = "foo/bar"
        result = runner.invoke(cli.main, ["prep-env"], env=env)
        mock_run.assert_has_calls(
            [
                call(
                    'git config --global user.email "41898282+github-actions[bot]@users.noreply.github.com"'
                ),
                call('git config --global user.name "GitHub Action"'),
                call("git remote"),
                call(
                    "git remote add upstream http://snuffy:abc123@github.com/foo/bar.git"
                ),
                call("git fetch upstream foo --tags"),
                call("git checkout -B foo upstream/foo"),
            ]
        )

    assert result.exit_code == 0, result.output
    text = env_file.read_text(encoding="utf-8")
    assert "BRANCH=foo" in text
    assert f"VERSION={version_spec}" in text
    assert "IS_PRERELEASE=true" in text
    assert "REPOSITORY=foo/bar" in text


def test_prep_changelog(py_package):
    runner = CliRunner()

    run("pre-commit run -a")

    changelog = py_package / "CHANGELOG.md"

    result = runner.invoke(cli.main, ["prep-env", "--version-spec", "1.0.1"])
    assert result.exit_code == 0, result.output

    with patch("release_helper.cli.generate_activity_md") as mocked_gen:
        mocked_gen.return_value = CHANGELOG_ENTRY
        result = runner.invoke(
            cli.main, ["prep-changelog", "--changelog-path", changelog]
        )
    assert result.exit_code == 0, result.output
    text = changelog.read_text(encoding="utf-8")
    assert cli.START_MARKER in text
    assert cli.END_MARKER in text
    assert PR_ENTRY in text

    assert len(re.findall(cli.START_MARKER, text)) == 1
    assert len(re.findall(cli.END_MARKER, text)) == 1

    run("pre-commit run -a")


def test_prep_changelog_existing(py_package):
    runner = CliRunner()
    changelog = py_package / "CHANGELOG.md"

    result = runner.invoke(cli.main, ["prep-env", "--version-spec", "1.0.1"])
    assert result.exit_code == 0, result.output

    with patch("release_helper.cli.generate_activity_md") as mocked_gen:
        mocked_gen.return_value = CHANGELOG_ENTRY
        result = runner.invoke(
            cli.main, ["prep-changelog", "--changelog-path", changelog]
        )
    assert result.exit_code == 0, result.output
    text = changelog.read_text(encoding="utf-8")
    text = text.replace("defining contributions", "Definining contributions")
    changelog.write_text(text, encoding="utf-8")

    # Commit the change
    run('git commit -a -m "commit changelog"')

    with patch("release_helper.cli.generate_activity_md") as mocked_gen:
        mocked_gen.return_value = CHANGELOG_ENTRY
        result = runner.invoke(
            cli.main, ["prep-changelog", "--changelog-path", changelog]
        )
    assert result.exit_code == 0, result.output
    text = changelog.read_text(encoding="utf-8")
    assert "Definining contributions" in text, text
    assert not "defining contributions" in text, text

    assert len(re.findall(cli.START_MARKER, text)) == 1
    assert len(re.findall(cli.END_MARKER, text)) == 1

    run("pre-commit run -a")


def test_check_md_links(py_package):
    runner = CliRunner()
    readme = py_package / "README.md"
    text = readme.read_text(encoding="utf-8")
    text += "\nhttps://apod.nasa.gov/apod/astropix.html"
    readme.write_text(text, encoding="utf-8")

    result = runner.invoke(cli.main, ["check-md-links"])
    assert result.exit_code == 0, result.output

    foo = py_package / "FOO.md"
    foo.write_text("http://127.0.0.1:5555")

    result = runner.invoke(cli.main, ["check-md-links"])
    assert result.exit_code == 1, result.output

    result = runner.invoke(cli.main, ["check-md-links", "--ignore", "FOO.md"])
    assert result.exit_code == 0, result.output


def test_check_changelog(py_package, tmp_path):
    runner = CliRunner()
    changelog = py_package / "CHANGELOG.md"
    output = tmp_path / "output.md"

    # prep the changelog first
    version_spec = "1.5.1"
    result = runner.invoke(cli.main, ["prep-env", "--version-spec", version_spec])
    assert result.exit_code == 0, result.output

    with patch("release_helper.cli.generate_activity_md") as mocked_gen:
        mocked_gen.return_value = CHANGELOG_ENTRY
        result = runner.invoke(
            cli.main, ["prep-changelog", "--changelog-path", changelog]
        )
    assert result.exit_code == 0, result.output

    # then prep the release
    bump_version(version_spec)
    with patch("release_helper.cli.generate_activity_md") as mocked_gen:
        mocked_gen.return_value = CHANGELOG_ENTRY
        result = runner.invoke(
            cli.main,
            ["check-changelog", "--changelog-path", changelog, "--output", output],
        )
    assert result.exit_code == 0, result.output

    assert PR_ENTRY in output.read_text(encoding="utf-8")
    text = changelog.read_text(encoding="utf-8")
    assert f"{cli.START_MARKER}\n\n## {version_spec}" in text
    assert cli.END_MARKER in text


def test_build_python(py_package):
    runner = CliRunner()
    result = runner.invoke(cli.main, ["build-python"])
    assert result.exit_code == 0, result.output


def test_check_python(py_package):
    runner = CliRunner()
    result = runner.invoke(cli.main, ["build-python"])
    assert result.exit_code == 0, result.output
    dist_files = glob(str(py_package / "dist" / "*"))
    result = runner.invoke(cli.main, ["check-python"] + dist_files)
    assert result.exit_code == 0, result.output


def test_check_npm(npm_package):
    runner = CliRunner()
    result = runner.invoke(cli.main, ["check-npm"])
    assert result.exit_code == 0, result.output


def test_check_manifest(py_package):
    runner = CliRunner()
    result = runner.invoke(cli.main, ["check-manifest"])
    assert result.exit_code == 0, result.output


def test_tag_release(py_package):
    runner = CliRunner()
    version_spec = "1.5.1"
    # Prep the env
    result = runner.invoke(cli.main, ["prep-env", "--version-spec", version_spec])
    assert result.exit_code == 0, result.output
    # Create the dist files
    run("python -m build .")
    # Tag the release
    result = runner.invoke(cli.main, ["tag-release"])
    assert result.exit_code == 0, result.output


def test_publish_release_draft(py_package):
    runner = CliRunner()
    version_spec = "1.5.1"
    changelog = py_package / "CHANGELOG.md"

    # Prep the env
    result = runner.invoke(cli.main, ["prep-env", "--version-spec", version_spec])
    assert result.exit_code == 0, result.output

    # Prep the changelog
    with patch("release_helper.cli.generate_activity_md") as mocked_gen:
        mocked_gen.return_value = CHANGELOG_ENTRY
        result = runner.invoke(
            cli.main, ["prep-changelog", "--changelog-path", changelog]
        )
    assert result.exit_code == 0, result.output

    # Create the dist files
    run("python -m build .")

    # Finalize the release
    result = runner.invoke(cli.main, ["prep-release"])

    # Publish the release - dry run
    repo = Repository(None, dict(), dict(), True)
    release = GitRelease(None, dict(), dict(), True)

    repo.create_git_release = release_mock = MagicMock(return_value=release)
    release.delete_release = delete_mock = MagicMock()

    with patch.object(cli.Github, "get_repo", return_value=repo) as mock_method:
        result = runner.invoke(cli.main, ["publish-release", "--dry-run"])
    assert result.exit_code == 0, result.output
    release_mock.assert_called_once()
    delete_mock.assert_called_once()


def test_publish_release_final(py_package):
    runner = CliRunner()
    version_spec = "1.5.1rc0"
    changelog = py_package / "CHANGELOG.md"

    # Prep the env
    result = runner.invoke(cli.main, ["prep-env", "--version-spec", version_spec])
    assert result.exit_code == 0, result.output

    # Prep the changelog
    with patch("release_helper.cli.generate_activity_md") as mocked_gen:
        mocked_gen.return_value = CHANGELOG_ENTRY
        result = runner.invoke(
            cli.main, ["prep-changelog", "--changelog-path", changelog]
        )
    assert result.exit_code == 0, result.output

    # Create the dist files
    run("python -m build .")

    # Finalize the release
    result = runner.invoke(cli.main, ["prep-release"])

    # Publish the release
    repo = Repository(None, dict(), dict(), True)
    release = GitRelease(None, dict(), dict(), True)

    repo.create_git_release = release_mock = MagicMock(return_value=release)
    release.delete_release = delete_mock = MagicMock()

    with patch.object(cli.Github, "get_repo", return_value=repo) as mock_method:
        result = runner.invoke(
            cli.main, ["publish-release", "--post-version-spec", "1.5.2.dev0"]
        )
    assert result.exit_code == 0, result.output
    release_mock.assert_called_once()
    delete_mock.assert_not_called()
