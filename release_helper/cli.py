# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
import os
import os.path as osp
from glob import glob
from pathlib import Path

import click

from release_helper import changelog
from release_helper import lib
from release_helper import npm
from release_helper import python
from release_helper import util


class ReleaseHelperGroup(click.Group):
    """Click group tailored to release-helper"""

    def invoke(self, ctx):
        """Handle release-helper config while invoking a command"""
        # Get the command name and make sure it is valid
        cmd_name = ctx.protected_args[0]
        if not cmd_name in self.commands:
            super().invoke(ctx)

        # Read in the config
        config = util.read_config()
        hooks = config.get("hooks", {})
        options = config.get("options", {})

        # Group the output of the command if on GitHub Actions
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::group::{cmd_name}")

        # Handle all of the parameters
        for param in self.commands[cmd_name].get_params(ctx):
            # Defer to env var overrides
            if param.envvar and os.environ.get(param.envvar):
                continue
            name = param.name
            if name in options:
                arg = f"--{name.replace('_', '-')}"
                # Defer to cli overrides
                if arg not in ctx.args:
                    ctx.args.append(arg)
                    ctx.args.append(options[name])

        # Handle before hooks
        before = f"before-{cmd_name}"
        if before in hooks:
            before_hooks = hooks[before]
            if isinstance(before_hooks, str):
                before_hooks = [before_hooks]
            for hook in before_hooks:
                util.run(hook)

        # Run the actual command
        super().invoke(ctx)

        # Handle after hooks
        after = f"after-{cmd_name}"
        if after in hooks:
            after_hooks = hooks[after]
            if isinstance(after_hooks, str):
                after_hooks = [after_hooks]
            for hook in after_hooks:
                util.run(hook)

        if os.environ.get("GITHUB_ACTIONS"):
            print("::endgroup::")

    def list_commands(self, ctx):
        """List commands in insertion order"""
        return self.commands.keys()


@click.group(cls=ReleaseHelperGroup)
@click.pass_context
def main(ctx):
    """Release helper scripts"""
    pass


# Extracted common options
version_spec_options = [
    click.option(
        "--version-spec",
        envvar="RH_VERSION_SPEC",
        required=True,
        help="The new version specifier",
    )
]

version_cmd_options = [
    click.option(
        "--version-cmd", envvar="RH_VERSION_COMMAND", help="The version command"
    )
]

branch_options = [
    click.option("--branch", envvar="RH_BRANCH", help="The target branch"),
    click.option(
        "--remote", envvar="RH_REMOTE", default="upstream", help="The git remote name"
    ),
    click.option("--repo", envvar="GITHUB_REPOSITORY", help="The git repo"),
]

auth_options = [
    click.option("--auth", envvar="GITHUB_ACCESS_TOKEN", help="The GitHub auth token"),
]

username_options = [
    click.option("--username", envvar="GITHUB_ACTOR", help="The git username")
]

dist_dir_options = [
    click.option(
        "--dist-dir",
        envvar="RH_DIST_DIR",
        default="dist",
        help="The folder to use for dist files",
    )
]

dry_run_options = [
    click.option(
        "--dry-run", is_flag=True, envvar="RH_DRY_RUN", help="Run as a dry run"
    )
]

changelog_path_options = [
    click.option(
        "--changelog-path",
        envvar="RH_CHANGELOG",
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
            envvar="RH_RESOLVE_BACKPORTS",
            default=True,
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
@add_options(version_spec_options)
@add_options(version_cmd_options)
@add_options(branch_options)
@add_options(auth_options)
@add_options(dist_dir_options)
@add_options(username_options)
@click.option("--output", envvar="GITHUB_ENV", help="Output file for env variables")
def prep_env(
    version_spec, version_cmd, branch, remote, repo, auth, dist_dir, username, output
):
    """Prep git and env variables and bump version"""
    lib.prep_env(
        version_spec,
        version_cmd,
        branch,
        remote,
        repo,
        auth,
        dist_dir,
        username,
        output,
    )


@main.command()
@add_options(changelog_options)
def build_changelog(branch, remote, repo, auth, changelog_path, resolve_backports):
    """Build changelog entry"""
    changelog.build_entry(branch, remote, repo, auth, changelog_path, resolve_backports)


@main.command()
@add_options(version_spec_options)
@add_options(branch_options)
@add_options(auth_options)
@add_options(dry_run_options)
def draft_changelog(version_spec, branch, remote, repo, auth, dry_run):
    """Create a changelog entry PR"""
    lib.draft_changelog(version_spec, branch, remote, repo, auth, dry_run)


@main.command()
@add_options(changelog_options)
@click.option(
    "--output", envvar="RH_CHANGELOG_OUTPUT", help="The output file for changelog entry"
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
    if not util.PYPROJECT.exists() and not util.SETUP_PY.exists():
        print("Skipping build-python since there are no python package files")
        return
    python.build_dist(dist_dir)


@main.command()
@add_options(dist_dir_options)
@click.option(
    "--test-cmd",
    envvar="RH_PY_TEST_COMMAND",
    help="The command to run in the test venvs",
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
    envvar="RH_NPM_TEST_COMMAND",
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
    if util.PYPROJECT.exists() or util.SETUP_PY.exists():
        util.run("check-manifest -v")
    else:
        print("Skipping check-manifest since there are no python package files")


@main.command()
@click.option(
    "--ignore-glob",
    default=["CHANGELOG.md"],
    multiple=True,
    help="Ignore test file paths based on glob pattern",
)
@click.option(
    "--cache-file",
    envvar="RH_CACHE_FILE",
    default="~/.cache/pytest-link-check",
    help="The cache file to use",
)
@click.option(
    "--links-expire",
    default=604800,
    envvar="RH_LINKS_EXPIRE",
    help="Duration in seconds for links to be cached (default one week)",
)
def check_links(ignore_glob, cache_file, links_expire):
    """Check Markdown file links"""
    lib.check_links(ignore_glob, cache_file, links_expire)


@main.command()
@add_options(branch_options)
@add_options(dist_dir_options)
@click.option(
    "--no-git-tag-workspace",
    is_flag=True,
    help="Whether to skip tagging npm workspace packages",
)
def tag_release(branch, remote, repo, dist_dir, no_git_tag_workspace):
    """Create release commit and tag"""
    lib.tag_release(branch, remote, repo, dist_dir, no_git_tag_workspace)


@main.command()
@add_options(branch_options)
@add_options(auth_options)
@add_options(changelog_path_options)
@add_options(version_cmd_options)
@add_options(dist_dir_options)
@add_options(dry_run_options)
@click.option(
    "--post-version-spec",
    envvar="RH_POST_VERSION_SPEC",
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
    lib.draft_release(
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
    lib.delete_release(auth, release_url)


@main.command()
@add_options(auth_options)
@add_options(dist_dir_options)
@add_options(dry_run_options)
@click.argument("release_url", nargs=1)
def extract_release(auth, dist_dir, dry_run, release_url):
    """Download and verify assets from a draft GitHub release"""
    lib.extract_release(auth, dist_dir, dry_run, release_url)


@main.command()
@add_options(auth_options)
@add_options(dist_dir_options)
@click.option("--npm_token", help="A token for the npm release", envvar="NPM_TOKEN")
@click.option(
    "--npm_cmd",
    help="The command to run for npm release",
    envvar="RH_NPM_COMMAND",
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
    lib.publish_release(
        auth, dist_dir, npm_token, npm_cmd, twine_cmd, dry_run, release_url
    )


@main.command()
@add_options(auth_options)
@add_options(branch_options)
@add_options(username_options)
@add_options(changelog_path_options)
@add_options(dry_run_options)
@click.argument("release_url")
def forwardport_changelog(
    auth, branch, remote, repo, username, changelog_path, dry_run, release_url
):
    """Forwardport Changelog Entries to the Default Branch"""
    lib.forwardport_changelog(
        auth, branch, remote, repo, username, changelog_path, dry_run, release_url
    )


if __name__ == "__main__":  # pragma: no cover
    main()
