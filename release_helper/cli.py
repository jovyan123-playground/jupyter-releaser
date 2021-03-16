# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
import hashlib
import json
import os
import os.path as osp
import re
import shlex
import shutil
import sys
import tarfile
import uuid
from glob import glob
from pathlib import Path
from subprocess import CalledProcessError
from subprocess import check_output
from tempfile import TemporaryDirectory

import click
import requests
from ghapi.core import GhApi
from github_activity import generate_activity_md
from pep440 import is_canonical

from release_helper import __version__
from release_helper import changelog
from release_helper import npm
from release_helper import other
from release_helper import python
from release_helper import util

HERE = osp.abspath(osp.dirname(__file__))

BUF_SIZE = 65536
TBUMP_CMD = "tbump --non-interactive --only-patch"


class NaturalOrderGroup(click.Group):
    """Click group that lists commmands in the order added"""

    def list_commands(self, ctx):
        return self.commands.keys()


@click.group(cls=NaturalOrderGroup)
def main():
    """Release helper scripts"""
    pass


# Extracted common options
version_cmd_options = [
    click.option("--version-cmd", envvar="VERSION_COMMAND", help="The version command")
]

branch_options = [
    click.option("--branch", envvar="BRANCH", help="The target branch"),
    click.option(
        "--remote", envvar="REMOTE", default="upstream", help="The git remote name"
    ),
    click.option("--repo", envvar="GITHUB_REPOSITORY", help="The git repo"),
]

auth_options = [
    click.option("--auth", envvar="GITHUB_ACCESS_TOKEN", help="The GitHub auth token"),
]

dist_dir_options = [
    click.option(
        "--dist-dir",
        envvar="DIST_DIR",
        default="dist",
        help="The folder to use for dist files",
    )
]

dry_run_options = [
    click.option("--dry-run", is_flag=True, envvar="DRY_RUN", help="Run as a dry run")
]

changelog_path_options = [
    click.option(
        "--changelog-path",
        envvar="CHANGELOG",
        default="CHANGELOG.md",
        help="The path to changelog file",
    ),
]

changelog_options = (
    branch_options
    + auth_options
    + changelog_path_options
    + [
        click.option(
            "--resolve-backports",
            envvar="RESOLVE_BACKPORTS",
            is_flag=True,
            help="Resolve backport PRs to their originals",
        ),
    ]
)


def add_options(options):
    """Add extracted common options to a click command"""
    # https://stackoverflow.com/a/40195800
    def _add_options(func):
        for option in reversed(options):
            func = option(func)
        return func

    return _add_options


@main.command()
@add_options(version_cmd_options)
@click.option(
    "--version-spec",
    envvar="VERSION_SPEC",
    required=True,
    help="The new version specifier",
)
@add_options(branch_options)
@add_options(auth_options)
@click.option("--username", envvar="GITHUB_ACTOR", help="The git username")
@click.option("--output", envvar="GITHUB_ENV", help="Output file for env variables")
def prep_env(version_spec, version_cmd, branch, remote, repo, auth, username, output):
    """Prep git and env variables and bump version"""
    util.prep_env(
        version_spec, version_cmd, branch, remote, repo, auth, username, output
    )


@main.command()
@add_options(changelog_options)
def build_changelog(branch, remote, repo, auth, changelog_path, resolve_backports):
    """Build changelog entry"""
    changelog.build_entry(branch, remote, repo, auth, changelog_path, resolve_backports)


@main.command()
@add_options(branch_options)
@add_options(auth_options)
@add_options(dry_run_options)
def draft_changelog(branch, remote, repo, auth, dry_run):
    """Create a changelog entry PR"""
    other.draft_changelog(branch, remote, repo, auth, dry_run)


@main.command()
@add_options(changelog_options)
@click.option(
    "--output", envvar="CHANGELOG_OUTPUT", help="The output file for changelog entry"
)
def check_changelog(
    branch, remote, repo, auth, changelog_path, resolve_backports, output
):
    """Check changelog entry"""
    changelog.check_entry(
        branch, remote, repo, auth, changelog_path, resolve_backports, output
    )


@main.command()
@add_options(dist_dir_options)
def build_python(dist_dir):
    """Build Python dist files"""
    if not osp.exists("pyproject.toml") and not osp.exists("setup.py"):
        print("Skipping build-python since there are no python package files")
        return
    python.build(dist_dir)


@main.command()
@add_options(dist_dir_options)
@click.option(
    "--test-cmd", envvar="PY_TEST_COMMAND", help="The command to run in the test venvs"
)
def check_python(dist_dir, test_cmd):
    """Check Python dist files"""
    for dist_file in glob(f"{dist_dir}/*"):
        if Path(dist_file).suffix not in [".gz", ".whl"]:
            print(f"Skipping non-python dist file {dist_file}")
            continue
        python.check_dist(dist_file, test_cmd=test_cmd)


@main.command()
@add_options(dist_dir_options)
@click.argument("package", default=".")
def build_npm(package, dist_dir):
    """Build npm package"""
    if not osp.exists("./package.json"):
        print("Skipping check-npm since there is no package.json file")
        return
    npm.build_dist(package, dist_dir)


@main.command()
@add_options(dist_dir_options)
@click.option(
    "--test-cmd",
    envvar="NPM_TEST_COMMAND",
    help="The command to run in isolated install.",
)
def check_npm(dist_dir, test_cmd):
    """Check npm package"""
    if not osp.exists("./package.json"):
        print("Skipping check-npm since there is no package.json file")
        return
    npm.check_dist(dist_dir, test_cmd=test_cmd)


@main.command()
def check_manifest():
    """Check the project manifest"""
    if Path("setup.py").exists() or Path("pyproject.toml").exists():
        util.run("check-manifest -v")
    else:
        print("Skipping build-python since there are no python package files")


@main.command()
@click.option(
    "--ignore-glob",
    envvar="IGNORE_MD",
    default=["CHANGELOG.md"],
    multiple=True,
    help="Ignore test file paths based on glob pattern",
)
@click.option(
    "--cache-file",
    envvar="CACHE_FILE",
    default="~/.cache/pytest-link-check",
    help="The cache file to use",
)
@click.option(
    "--links-expire",
    default=604800,
    envvar="LINKS_EXPIRE",
    help="Duration in seconds for links to be cached (default one week)",
)
def check_links(ignore_glob, cache_file, links_expire):
    """Check Markdown file links"""
    util.check_links(ignore_glob, cache_file, links_expire)


@main.command()
@add_options(branch_options)
@add_options(dist_dir_options)
@click.option(
    "--no-git-tag-workspace",
    flag=True,
    help="Whether to skip tagging npm workspace packages",
)
def tag_release(branch, remote, repo, dist_dir, no_git_tag_workspace):
    """Create release commit and tag"""
    other.tag_release(branch, remote, repo, dist_dir, no_git_tag_workspace)


@main.command()
@add_options(branch_options)
@add_options(auth_options)
@add_options(changelog_path_options)
@add_options(version_cmd_options)
@add_options(dist_dir_options)
@add_options(dry_run_options)
@click.option(
    "--post-version-spec",
    envvar="POST_VERSION_SPEC",
    help="The post release version (usually dev)",
)
@click.argument("assets", nargs=-1)
def draft_release(
    branch,
    remote,
    repo,
    auth,
    changelog_path,
    version_cmd,
    dist_dir,
    dry_run,
    post_version_spec,
    assets,
):
    """Publish Draft GitHub release and handle post version bump"""
    other.draft_release(
        branch,
        remote,
        repo,
        auth,
        changelog_path,
        version_cmd,
        dist_dir,
        dry_run,
        post_version_spec,
        assets,
    )


@main.command()
@add_options(auth_options)
@click.argument("release-url", nargs=1)
def delete_release(auth, release_url):
    """Delete a draft GitHub release by url to the release page"""
    other.delete_release(auth, release_url)


@main.command()
@add_options(auth_options)
@add_options(dist_dir_options)
@add_options(dry_run_options)
@click.argument("release_url", nargs=1)
def extract_release(auth, dist_dir, dry_run, release_url):
    """Download and verify assets from a draft GitHub release"""
    other.extract_release(auth, dist_dir, dry_run, release_url)


@main.command()
@add_options(auth_options)
@add_options(dist_dir_options)
@click.option("--npm_token", help="A token for the npm release", envvar="NPM_TOKEN")
@click.option(
    "--npm_cmd",
    help="The command to run for npm release",
    envvar="NPM_COMMAND",
    default="npm publish",
)
@click.option(
    "--twine_cmd",
    help="The twine to run for Python release",
    envvar="TWINE_COMMAND",
    default="twine upload",
)
@add_options(dry_run_options)
@click.argument("release_url", nargs=1)
def publish_release(
    auth, dist_dir, npm_token, npm_cmd, twine_cmd, dry_run, release_url
):
    """Publish release asset(s) and finalize GitHub release"""
    other.publish_release(
        auth, dist_dir, npm_token, npm_cmd, twine_cmd, dry_run, release_url
    )


if __name__ == "__main__":  # pragma: no cover
    main()
