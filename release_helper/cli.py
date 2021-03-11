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
from github import Github
from github_activity import generate_activity_md
from pep440 import is_canonical

from release_helper import __version__

HERE = osp.abspath(osp.dirname(__file__))
START_MARKER = "<!-- <START NEW CHANGELOG ENTRY> -->"
END_MARKER = "<!-- <END NEW CHANGELOG ENTRY> -->"
BUF_SIZE = 65536
TBUMP_CMD = "tbump --non-interactive --only-patch"


# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
# Helper Functions
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""


def run(cmd, **kwargs):
    """Run a command as a subprocess and get the output as a string"""
    if not kwargs.pop("quiet", False):
        print(f"+ {cmd}")

    try:
        return check_output(shlex.split(cmd), **kwargs).decode("utf-8").strip()
    except CalledProcessError as e:
        print(e.output.decode("utf-8").strip())
        raise e


def get_branch():
    """Get the appropriat git branch"""
    if os.environ.get("GITHUB_BASE_REF"):
        # GitHub Action PR Event
        branch = os.environ["GITHUB_BASE_REF"]
    elif os.environ.get("GITHUB_REF"):
        # GitHub Action Push Event
        # e.g. refs/heads/feature-branch-1
        branch = os.environ["GITHUB_REF"].split("/")[-1]
    else:
        branch = run("git branch --show-current", quiet=True)
    return branch


def get_repo(remote, auth=None):
    """Get the remote repo org and name"""
    gh_repo = os.environ.get("GITHUB_REPOSITORY")
    if gh_repo:
        g = Github(auth)
        repo = g.get_repo(gh_repo)
        # If this is the source repo, return the original target
        if repo.source:
            return repo.source.full_name
        else:
            return gh_repo

    url = run(f"git remote get-url {remote}")
    url = normalize_path(url)
    parts = url.split("/")[-2:]
    if ":" in parts[0]:
        parts[0] = parts[0].split(":")[-1]
    return "/".join(parts)


def get_version():
    """Get the current package version"""
    if osp.exists("setup.py"):
        return run("python setup.py --version", quiet=True)
    elif osp.exists("package.json"):
        return json.loads(Path("package.json").read_text(encoding="utf-8"))["version"]
    else:  # pragma: no cover
        raise ValueError("No version identifier could be found!")


def normalize_path(path):
    """Normalize a path to use backslashes"""
    return str(path).replace(os.sep, "/")


def format_pr_entry(target, number, auth=None):
    """Format a PR entry in the style used by our changelogs.

    Parameters
    ----------
    target : str
        The GitHub organization/repo
    number : int
        The PR number to resolve
    auth : str, optional
        The GitHub authorization token

    Returns
    -------
    str
        A formatted PR entry
    """
    g = Github(auth)
    repo = g.get_repo(target)
    pull = repo.get_pull(number)
    title = pull.title
    url = pull.url
    user_name = pull.user.login
    user_url = pull.user.html_url
    return f"- {title} [{number}]({url}) [@{user_name}]({user_url})"


def get_workflow_path(repo, auth=None):
    """Get the path for the current running workflow"""
    name = os.environ["GITHUB_WORKFLOW"]
    g = Github(auth)
    r = g.get_repo(repo)
    workflows = r.get_workflows()

    found = False
    for workflow in workflows:
        if workflow.name == name:
            path = workflow.path
            found = True
    if not found:  # pragma: no cover
        raise ValueError(f"Could not validate workflow {name}")
    return path


def get_changelog_entry(branch, repo, version, *, auth=None, resolve_backports=False):
    """Get a changelog for the changes since the last tag on the given branch.

    Parameters
    ----------
    branch : str
        The target branch
    respo : str
        The GitHub organization/repo
    version : str
        The new version
    auth : str, optional
        The GitHub authorization token
    resolve_backports: bool, optional
        Whether to resolve backports to the original PR

    Returns
    -------
    str
        A formatted changelog entry with markers
    """
    since = run(f"git tag --merged {branch}")
    if not since:  # pragma: no cover
        raise ValueError(f"No tags found on branch {branch}")

    since = since.splitlines()[-1]
    print(f"Getting changes to {repo} since {since}...")

    md = generate_activity_md(repo, since=since, kind="pr", auth=auth)

    if not md:
        print("No PRs found")
        return f"## {version}\nNo merged PRs"

    md = md.splitlines()

    start = -1
    full_changelog = ""
    for (ind, line) in enumerate(md):
        if "[full changelog]" in line:
            full_changelog = line.replace("full changelog", "Full Changelog")
        elif line.strip().startswith("## Merged PRs"):
            start = ind + 1

    prs = md[start:]

    if resolve_backports:
        for (ind, line) in enumerate(prs):
            if re.search(r"\[@meeseeksmachine\]", line) is not None:
                match = re.search(r"Backport PR #(\d+)", line)
                if match:
                    prs[ind] = format_pr_entry(match.groups()[0])

    prs = "\n".join(prs).strip()

    # Move the contributor list to a heading level 3
    prs = prs.replace("## Contributors", "### Contributors")

    # Replace "*" unordered list marker with "-" since this is what
    # Prettier uses
    prs = re.sub(r"^\* ", "- ", prs)
    prs = re.sub(r"\n\* ", "\n- ", prs)

    output = f"""
## {version}

{full_changelog}

{prs}
""".strip()

    return output


def compute_sha256(path):
    """Compute the sha256 of a file"""
    sha256 = hashlib.sha256()

    with open(path, "rb") as f:
        while True:
            data = f.read(BUF_SIZE)
            if not data:
                break
            sha256.update(data)

    return sha256.hexdigest()


def create_release_commit(version):
    """Generate a release commit that has the sha256 digests for the release files"""
    cmd = f'git commit -am "Publish {version}" -m "SHA256 hashes:"'

    shas = dict()

    if osp.exists("setup.py"):
        files = glob("dist/*")
        if not len(files) == 2:  # pragma: no cover
            raise ValueError("Missing distribution files")

        for path in files:
            path = normalize_path(path)
            sha256 = compute_sha256(path)
            shas[path] = sha256
            cmd += f' -m "{path}: {sha256}"'

    if osp.exists("package.json"):
        data = json.loads(Path("package.json").read_text(encoding="utf-8"))
        if not data.get("private", False):
            npm = normalize_path(shutil.which("npm"))
            filename = normalize_path(run(f"{npm} pack"))
            sha256 = compute_sha256(filename)
            shas[filename] = sha256
            os.remove(filename)
            cmd += f' -m "{filename}: {sha256}"'

    run(cmd)

    return shas


def bump_version(version_spec, version_cmd=""):
    """Bump the version"""
    # Look for config files to determine version command if not given
    if not version_cmd:
        for name in "bumpversion", ".bumpversion", "bump2version", ".bump2version":
            if osp.exists(name + ".cfg"):
                version_cmd = "bump2version"

        if osp.exists("tbump.toml"):
            version_cmd = version_cmd or TBUMP_CMD

        if osp.exists("pyproject.toml"):
            if "tbump" in Path("pyproject.toml").read_text(encoding="utf-8"):
                version_cmd = version_cmd or TBUMP_CMD

        if osp.exists("setup.cfg"):
            if "bumpversion" in Path("setup.cfg").read_text(encoding="utf-8"):
                version_cmd = version_cmd or "bump2version"

    if not version_cmd:  # pragma: no cover
        raise ValueError("Please specify a version bump command to run")

    # Bump the version
    run(f"{version_cmd} {version_spec}")


def is_prerelease(version):
    """Test whether a version is a prerelease version"""
    final_version = re.match("([0-9]+.[0-9]+.[0-9]+)", version).groups()[0]
    return final_version != version


# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
# Start CLI
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""


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
    click.option("--version-cmd", envvar="VERSION_CMD", help="The version command")
]

branch_options = [
    click.option("--branch", envvar="BRANCH", help="The target branch"),
    click.option(
        "--remote", envvar="REMOTE", default="upstream", help="The git remote name"
    ),
    click.option("--repo", envvar="REPOSITORY", help="The git repo"),
]

auth_options = [
    click.option("--auth", envvar="GITHUB_ACCESS_TOKEN", help="The GitHub auth token"),
]

username_options = [
    click.option("--username", envvar="GITHUB_ACTOR", help="The git username")
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
@add_options(username_options)
@click.option("--output", envvar="GITHUB_ENV", help="Output file for env variables")
def prep_env(version_spec, version_cmd, branch, remote, repo, auth, username, output):
    """Prep git and env variables and bump version"""

    # Get the branch
    branch = branch or get_branch()
    print(f"branch={branch}")

    # Get the repo
    repo = repo or get_repo(remote, auth=auth)
    print(f"repository={repo}")

    # Set up git config if on GitHub Actions
    is_action = "GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"]
    if is_action:
        # Use email address for the GitHub Actions bot
        # https://github.community/t/github-actions-bot-email-address/17204/6
        run(
            'git config --global user.email "41898282+github-actions[bot]@users.noreply.github.com"'
        )
        run('git config --global user.name "GitHub Action"')

        remotes = run("git remote").splitlines()
        if remote not in remotes:
            if auth:
                url = f"http://{username}:{auth}@github.com/{repo}.git"
            else:
                url = f"http://github.com/{repo}.git"
            run(f"git remote add {remote} {url}")

    # Check out the remote branch so we can push to it
    run(f"git fetch {remote} {branch} --tags")
    run(f"git checkout -B {branch} {remote}/{branch}")

    # Make sure the local workflow file is the same as the target one
    if "GITHUB_WORKFLOW" in os.environ:
        path = get_workflow_path(repo)
        assert osp.exists(path), f"Could not find workflow {path}"
        diff = run(f"git --no-pager diff HEAD {remote}/{branch} -- {path}")
        msg = f"Workflow file {path} differs from {remote} repo {repo}:\n{diff}"
        if path in diff:  # pragma: no cover
            raise ValueError(msg)

    # Bump the version
    bump_version(version_spec, version_cmd=version_cmd)

    version = get_version()

    if "setup.py" in os.listdir(".") and not is_canonical(version):  # pragma: no cover
        raise ValueError(f"Invalid version {version}")

    print(f"version={version}")
    is_prerelease_str = str(is_prerelease(version)).lower()
    print(f"is_prerelease={is_prerelease_str}")

    if output:
        print(f"Writing env variables to {output} file")
        Path(output).write_text(
            f"""
BRANCH={branch}
VERSION={version}
REPOSITORY={repo}
IS_PRERELEASE={is_prerelease_str}
""".strip(),
            encoding="utf-8",
        )


@main.command()
@add_options(changelog_options)
def prep_changelog(branch, remote, repo, auth, changelog_path, resolve_backports):
    """Prep changelog entry"""
    branch = branch or get_branch()

    # Get the new version
    version = get_version()

    # Get the existing changelog and run some validation
    changelog = Path(changelog_path).read_text(encoding="utf-8")

    if START_MARKER not in changelog or END_MARKER not in changelog:
        raise ValueError("Missing insert marker for changelog")

    if changelog.find(START_MARKER) != changelog.rfind(START_MARKER):
        raise ValueError("Insert marker appears more than once in changelog")

    # Get changelog entry
    repo = repo or get_repo(remote, auth=auth)
    entry = get_changelog_entry(
        f"{remote}/{branch}",
        repo,
        version,
        auth=auth,
        resolve_backports=resolve_backports,
    )

    # Insert the entry into the file
    # Test if we are augmenting an existing changelog entry (for new PRs)
    # Preserve existing PR entries since we may have formatted them
    new_entry = f"{START_MARKER}\n\n{entry}\n\n{END_MARKER}"
    prev_entry = changelog[
        changelog.index(START_MARKER) : changelog.index(END_MARKER) + len(END_MARKER)
    ]

    if f"# {version}" in prev_entry:
        lines = new_entry.splitlines()
        old_lines = prev_entry.splitlines()
        for ind, line in enumerate(lines):
            pr = re.search(r"\[#\d+\]", line)
            if not pr:
                continue
            for old_line in prev_entry.splitlines():
                if pr.group() in old_line:
                    lines[ind] = old_line
        changelog = changelog.replace(prev_entry, "\n".join(lines))
    else:
        changelog = changelog.replace(END_MARKER + "\n\n", "")
        changelog = changelog.replace(END_MARKER + "\n", "")
        changelog = changelog.replace(START_MARKER, new_entry)

    Path(changelog_path).write_text(changelog, encoding="utf-8")

    # Stage changelog
    run(f"git add {normalize_path(changelog_path)}")


@main.command()
@add_options(branch_options)
@add_options(auth_options)
@add_options(username_options)
@add_options(dry_run_options)
@click.option("--body", help="The Pull Request body text")
def publish_changelog(branch, remote, repo, auth, username, dry_run, body):
    """Publish a changelog entry PR"""
    repo = repo or get_repo(remote, auth=auth)
    branch = branch or get_branch()
    version = get_version()

    # Make a new branch with a uuid suffix
    pr_branch = f"{branch}-{uuid.uuid1().hex})"
    run(f"git checkout -b {pr_branch} {remote}/{branch}")

    # Add a commit with the message
    run(f'git commit -a -m "Generate changelog for {version}"')

    # Create the pull
    g = Github(auth)
    r = g.get_repo(repo)
    title = f"Automated Changelog for {version} on {branch}"
    body = body or title
    base = branch
    head = f"{username}:{pr_branch}"
    maintainer_can_modify = True

    if dry_run:
        return

    r.create_pull(title, body, base, head, maintainer_can_modify)


@main.command()
@add_options(changelog_options)
@click.option(
    "--output", envvar="CHANGELOG_OUTPUT", help="The output file for changelog entry"
)
def check_changelog(
    branch, remote, repo, auth, changelog_path, resolve_backports, output
):
    """Check changelog entry"""
    branch = branch or get_branch()

    # Get the new version
    version = get_version()

    # Finalize changelog
    changelog = Path(changelog_path).read_text(encoding="utf-8")

    start = changelog.find(START_MARKER)
    end = changelog.find(END_MARKER)

    if start == -1 or end == -1:  # pragma: no cover
        raise ValueError("Missing new changelog entry delimiter(s)")

    if start != changelog.rfind(START_MARKER):  # pragma: no cover
        raise ValueError("Insert marker appears more than once in changelog")

    final_entry = changelog[start + len(START_MARKER) : end]

    repo = repo or get_repo(remote, auth=auth)
    raw_entry = get_changelog_entry(
        f"{remote}/{branch}",
        repo,
        version,
        auth=auth,
        resolve_backports=resolve_backports,
    )

    if f"# {version}" not in final_entry:  # pragma: no cover
        print(final_entry)
        raise ValueError(f"Did not find entry for {version}")

    final_prs = re.findall(r"\[#(\d+)\]", final_entry)
    raw_prs = re.findall(r"\[#(\d+)\]", raw_entry)

    for pr in raw_prs:
        # Allow for changelog PR to not be in changelog itself
        skip = False
        for line in raw_entry.splitlines():
            if f"[#{pr}]" in line and "changelog" in line.lower():
                skip = True
                break
        if skip:
            continue
        if not f"[#{pr}]" in final_entry:  # pragma: no cover
            raise ValueError(f"Missing PR #{pr} in changelog")
    for pr in final_prs:
        if not f"[#{pr}]" in raw_entry:  # pragma: no cover
            raise ValueError(f"PR #{pr} does not belong in changelog for {version}")

    if output:
        Path(output).write_text(final_entry, encoding="utf-8")


@main.command()
def build_python():
    """Build Python dist files"""
    shutil.rmtree("./dist", ignore_errors=True)

    if osp.exists("./pyproject.toml"):
        run("python -m build .")
    else:
        run("python setup.py sdist")
        run("python setup.py bdist_wheel")


@main.command()
@click.argument("dist-files", nargs=-1)
@click.option(
    "--test-cmd", envvar="PY_TEST_CMD", help="The command to run in the test venvs"
)
def check_python(dist_files, test_cmd):
    """Check Python dist files"""
    for dist_file in dist_files:
        dist_file = normalize_path(dist_file)
        run(f"twine check {dist_file}")

        if not test_cmd:
            # Get the package name from the dist file name
            name = re.match(r"(\S+)-\d", osp.basename(dist_file)).groups()[0]
            name = name.replace("-", "_")
            test_cmd = f'python -c "import {name}"'

        # Create venvs to install dist file
        # run the test command in the venv
        with TemporaryDirectory() as td:
            env_path = normalize_path(osp.abspath(td))
            if os.name == "nt":  # pragma: no cover
                bin_path = f"{env_path}/Scripts/"
            else:
                bin_path = f"{env_path}/bin"

            # Create the virtual env, upgrade pip,
            # install, and run test command
            run(f"python -m venv {env_path}")
            run(f"{bin_path}/python -m pip install -U pip")
            run(f"{bin_path}/pip install -q {dist_file}")
            run(f"{bin_path}/{test_cmd}")


@main.command()
@click.argument("package", default=".")
@click.option(
    "--test-cmd", envvar="NPM_TEST_CMD", help="The command to run in isolated install."
)
def check_npm(package, test_cmd):
    """Check npm package"""
    npm = normalize_path(shutil.which("npm"))
    node = normalize_path(shutil.which("node"))

    if osp.isdir(package):
        should_remove = True
        tarball = osp.join(os.getcwd(), run(f"{npm} pack"))
    else:
        should_remove = True
        tarball = package

    tarball = normalize_path(tarball)

    # Get the package json info from the tarball
    fid = tarfile.open(tarball)
    data = fid.extractfile("package/package.json").read()
    data = json.loads(data.decode("utf-8"))
    fid.close()

    # Bail if it is a private package or monorepo
    if data.get("private", False):  # pragma: no cover
        raise ValueError("No need to prep a private package")

    # Bail if it is a monorepo
    if "workspaces" in data:  # pragma: no cover
        print("Do not handle monorepos here")
        return

    if not test_cmd:
        name = data["name"]
        test_cmd = f"{node} -e \"require('{name}')\""

    # Install in a temporary directory and verify import
    with TemporaryDirectory() as tempdir:
        run(f"{npm} init -y", cwd=tempdir)
        run(f"{npm} install {tarball}", cwd=tempdir)
        run(test_cmd, cwd=tempdir)

    # Remove the tarball
    if should_remove:
        os.remove(tarball)


@main.command()
def check_manifest():
    """Check the project manifest"""
    run("check-manifest -v")


@main.command()
@click.option(
    "--ignore",
    default="CHANGELOG.md",
    help="Comma separated list of glob patterns to ignore",
)
@click.option(
    "--cache-file", default="~/.cache/pytest-link-check", help="The cache file to use"
)
@click.option(
    "--links-expire",
    default=604800,
    help="Duration in seconds for links to be cached (default one week)",
)
def check_md_links(ignore, cache_file, links_expire):
    """Check Markdown file links"""
    cache_dir = osp.expanduser(cache_file).replace(os.sep, "/")
    os.makedirs(cache_dir, exist_ok=True)
    cmd = "pytest --check-links --check-links-cache "
    cmd += f"--check-links-cache-expire-after {links_expire} "
    cmd += f"--check-links-cache-name {cache_dir}/check-md-links "
    cmd += " -k .md "

    for spec in ignore.split(","):
        cmd += f"--ignore-glob {spec}"

    try:
        run(cmd)
    except Exception:
        run(cmd + " --lf")


@main.command()
@add_options(branch_options)
def tag_release(branch, remote, repo):
    """Create release commit and tag"""
    # Get the new version
    version = get_version()

    # Get the branch
    branch = branch or get_branch()

    # Create the release commit
    create_release_commit(version)

    # Create the annotated release tag
    tag_name = f"v{version}"
    run(f'git tag {tag_name} -a -m "Release {tag_name}"')


@main.command()
@add_options(branch_options)
@add_options(auth_options)
@add_options(changelog_path_options)
@add_options(version_cmd_options)
@add_options(dry_run_options)
@click.option(
    "--post-version-spec",
    envvar="POST_VERSION_SPEC",
    help="The post release version (usually dev)",
)
def publish_release(
    branch, remote, repo, auth, changelog_path, version_cmd, dry_run, post_version_spec
):
    """Publish GitHub release and handle post version bump"""
    branch = branch or get_branch()
    repo = repo or get_repo(remote, auth=auth)

    if not dry_run:
        run(f"git push {remote} HEAD:{branch} --follow-tags --tags")

    version = get_version()

    g = Github(auth)
    r = g.get_repo(repo)

    changelog = Path(changelog_path).read_text(encoding="utf-8")

    start = changelog.find(START_MARKER)
    end = changelog.find(END_MARKER)
    message = changelog[start + len(START_MARKER) : end]

    prerelease = is_prerelease(version)
    release = r.create_git_release(
        f"v{version}",
        f"Release v{version}",
        message,
        draft=dry_run,
        prerelease=prerelease,
    )
    if dry_run:
        release.delete_release()

    # Bump to post version if given
    if post_version_spec:
        bump_version(post_version_spec, version_cmd)
        post_version = get_version()
        if "setup.py" in os.listdir(".") and not is_canonical(
            version
        ):  # pragma: no cover
            raise ValueError(f"\n\nInvalid post version {version}")

        print(f"Bumped version to {post_version}")
        run(f'git commit -a -m "Bump to {post_version}"')

        if not dry_run:
            run(f"git push {remote} {branch}")


if __name__ == "__main__":  # pragma: no cover
    main()
