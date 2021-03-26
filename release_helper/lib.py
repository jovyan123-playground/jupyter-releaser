# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
import os
import os.path as osp
import re
import shutil
import uuid
from datetime import datetime
from glob import glob
from pathlib import Path
from tempfile import TemporaryDirectory

import requests
from ghapi.core import GhApi
from pep440 import is_canonical

from release_helper import changelog
from release_helper import npm
from release_helper import python
from release_helper import util


def prep_env(
    version_spec, version_cmd, branch, remote, repo, auth, dist_dir, username, output
):
    """Prep git and env variables and bump version"""
    # Clear the dist directory
    shutil.rmtree(dist_dir, ignore_errors=True)

    # Get the branch
    branch = branch or util.get_branch()
    print(f"branch={branch}")

    # Get the repo
    repo = repo or util.get_repo(remote, auth=auth)
    print(f"repository={repo}")

    # Set up git
    prep_git(branch, remote, repo, username, auth)

    # Bump the version
    util.bump_version(version_spec, version_cmd=version_cmd)

    version = util.get_version()

    if util.SETUP_PY.exists() and not is_canonical(version):
        raise ValueError(f"Invalid version {version}")

    # Bail if tag already exists
    tag_name = f"v{version}"
    if tag_name in util.run("git --no-pager tag").splitlines():
        msg = f"Tag {tag_name} already exists!"
        msg += " To delete run: `git push --delete upstream {tag_name}`"
        raise ValueError(msg)

    print(f"version={version}")
    is_prerelease_str = str(util.is_prerelease(version)).lower()
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


def check_links(ignore_glob, cache_file, links_expire):
    cache_dir = osp.expanduser(cache_file).replace(os.sep, "/")
    os.makedirs(cache_dir, exist_ok=True)
    cmd = "pytest --check-links --check-links-cache "
    cmd += f"--check-links-cache-expire-after {links_expire} "
    cmd += f"--check-links-cache-name {cache_dir}/check-release-links "
    cmd += " -k .md "

    for spec in ignore_glob:
        cmd += f"--ignore-glob {spec}"

    try:
        util.run(cmd)
    except Exception:
        util.run(cmd + " --lf")


def draft_changelog(version_spec, branch, remote, repo, auth, dry_run):
    """Create a changelog entry PR"""
    repo = repo or util.get_repo(remote, auth=auth)
    branch = branch or util.get_branch()
    version = util.get_version()

    tags = util.run("git --no-pager tag")
    if f"v{version}" in tags.splitlines():
        raise ValueError(f"Tag v{version} already exists")

    # Check out any unstaged files from version bump
    util.run("git checkout -- .")

    commit_message = f'git commit -a -m "Generate changelog for {version}"'

    title = f"{changelog.PR_PREFIX} for {version} on {branch}"
    body = title

    # Check for multiple versions
    if util.PACKAGE_JSON.exists():
        body += npm.get_package_versions(version)

    body += '\n\nAfter merging this PR run the "Draft Release" Workflow'
    body += f"\non Branch: {branch}"
    body += f"\nwith Version Spec: {version_spec}"

    make_changelog_pr(
        auth, branch, remote, repo, title, commit_message, body, dry_run=dry_run
    )


def make_changelog_pr(
    auth, branch, remote, repo, title, commit_message, body, dry_run=False
):
    repo = repo or util.get_repo(remote, auth=auth)

    # Make a new branch with a uuid suffix
    pr_branch = f"changelog-{uuid.uuid1().hex}"

    if not dry_run:
        util.run("git --no-pager diff")
        util.run("git stash")
        util.run(f"git fetch {remote} {branch}")
        util.run(f"git checkout -b {pr_branch} {remote}/{branch}")
        util.run("git stash apply")

    # Add a commit with the message
    util.run(commit_message)

    # Create the pull
    owner, repo_name = repo.split("/")
    gh = GhApi(owner=owner, repo=repo_name, token=auth)

    base = branch
    head = pr_branch
    maintainer_can_modify = True

    if dry_run:
        print("Skipping pull request due to dry run")
        return

    util.run(f"git push {remote} {pr_branch}")

    #  title, head, base, body, maintainer_can_modify, draft, issue
    pull = gh.pulls.create(title, head, base, body, maintainer_can_modify, False, None)

    util.actions_output("pr_url", pull.html_url)


def tag_release(branch, remote, repo, dist_dir, no_git_tag_workspace):
    """Create release commit and tag"""
    # Get the new version
    version = util.get_version()

    # Get the branch
    branch = branch or util.get_branch()

    # Create the release commit
    util.create_release_commit(version, dist_dir)

    # Create the annotated release tag
    tag_name = f"v{version}"
    util.run(f'git tag {tag_name} -a -m "Release {tag_name}"')

    # Create annotated release tags for workspace packages if given
    if not no_git_tag_workspace:
        npm.tag_workspace_packages()


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
    branch = branch or util.get_branch()
    repo = repo or util.get_repo(remote, auth=auth)

    assets = assets or glob(f"{dist_dir}/*")

    version = util.get_version()

    body = changelog.extract_current(changelog_path)

    owner, repo_name = repo.split("/")
    gh = GhApi(owner=owner, repo=repo_name, token=auth)

    # Remove draft releases over a day old
    for release in gh.repos.list_releases():
        if release.draft == "false":
            continue
        created = release.created_at
        d_created = datetime.strptime(created, r"%Y-%m-%dT%H:%M:%SZ")
        delta = d_created - datetime.utcnow()
        if delta.days > 0:
            gh.repos.delete_release(release.id)

    # Create a draft release
    prerelease = util.is_prerelease(version)

    # Bump to post version if given
    if post_version_spec:
        util.bump_version(post_version_spec, version_cmd)
        post_version = util.get_version()
        if util.SETUP_PY.exists() and not is_canonical(version):  # pragma: no cover
            raise ValueError(f"\n\nInvalid post version {version}")

        print(f"Bumped version to {post_version}")
        util.run(f'git commit -a -m "Bump to {post_version}"')

    if not dry_run:
        util.run(f"git push {remote} HEAD:{branch} --follow-tags --tags")

    print(f"Creating release for {version}")
    print(f"With assets: {assets}")
    release = gh.create_release(
        f"v{version}",
        branch,
        f"Release v{version}",
        body,
        True,
        prerelease,
        files=assets,
    )

    # Set the GitHub action output
    util.actions_output("release_url", release.html_url)


def delete_release(auth, release_url):
    """Delete a draft GitHub release by url to the release page"""
    match = re.match(util.RELEASE_HTML_PATTERN, release_url)
    match = match or re.match(util.RELEASE_API_PATTERN, release_url)
    if not match:
        raise ValueError(f"Release url is not valid: {release_url}")

    gh = GhApi(owner=match["owner"], repo=match["repo"], token=auth)
    release = util.release_for_url(gh, release_url)
    for asset in release.assets:
        gh.repos.delete_release_asset(asset.id)

    gh.repos.delete_release(release.id)


def extract_release(auth, dist_dir, dry_run, release_url):
    """Download and verify assets from a draft GitHub release"""
    match = parse_release_url(release_url)
    owner, repo = match["owner"], match["repo"]
    gh = GhApi(owner=owner, repo=repo, token=auth)
    release = util.release_for_url(gh, release_url)
    assets = release.assets

    # Clean the dist folder
    dist = Path(dist_dir)
    if dist.exists():
        shutil.rmtree(dist, ignore_errors=True)
    os.makedirs(dist)

    # Fetch, validate, and publish assets
    for asset in assets:
        print(f"Fetching {asset.name}...")
        url = asset.url
        headers = dict(Authorization=f"token {auth}", Accept="application/octet-stream")
        path = dist / asset.name
        with requests.get(url, headers=headers, stream=True) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            suffix = Path(asset.name).suffix
            if suffix in [".gz", ".whl"]:
                python.check_dist(path)
            elif suffix == ".tgz":
                npm.check_dist(path)
            else:
                print(f"Nothing to check for {asset.name}")

    # Skip sha validation for dry runs since the remote tag will not exist
    if dry_run:
        return

    branch = release.target_commitish
    tag_name = release.tag_name

    sha = None
    for tag in gh.list_tags():
        if tag.ref == f"refs/tags/{tag_name}":
            sha = tag.object.sha
    if sha is None:
        raise ValueError("Could not find tag")

    # Run a git checkout
    # Fetch the branch
    # Get the commmit message for the branch
    commit_message = ""
    with TemporaryDirectory() as td:
        url = gh.repos.get().html_url
        util.run(f"git clone {url} local", cwd=td)
        checkout = osp.join(td, "local")
        if not osp.exists(url):
            util.run(f"git fetch origin {branch}", cwd=checkout)
        commit_message = util.run(f"git log --format=%B -n 1 {sha}", cwd=checkout)

    for asset in assets:
        # Check the sha against the published sha
        valid = False
        path = dist / asset.name
        sha = util.compute_sha256(path)

        for line in commit_message.splitlines():
            if asset.name in line:
                if sha in line:
                    valid = True
                else:
                    print("Mismatched sha!")

        if not valid:  # pragma: no cover
            raise ValueError(f"Invalid file {asset.name}")


def parse_release_url(release_url):
    """Parse a release url into a regex match"""
    match = re.match(util.RELEASE_HTML_PATTERN, release_url)
    match = match or re.match(util.RELEASE_API_PATTERN, release_url)
    if not match:
        raise ValueError(f"Release url is not valid: {release_url}")
    return match


def publish_release(
    auth, dist_dir, npm_token, npm_cmd, twine_cmd, dry_run, release_url
):
    """Publish release asset(s) and finalize GitHub release"""
    match = parse_release_url(release_url)

    if npm_token:
        npm.handle_auth_token(npm_token)

    found = False
    for path in glob(f"{dist_dir}/*.*"):
        name = Path(path).name
        suffix = Path(path).suffix
        if suffix in [".gz", ".whl"]:
            util.run(f"{twine_cmd} {name}", cwd=dist_dir)
            found = True
        elif suffix == ".tgz":
            util.run(f"{npm_cmd} {name}", cwd=dist_dir)
            found = True
        else:
            print(f"Nothing to upload for {name}")

    if not found:  # pragma: no cover
        raise ValueError("No assets published, refusing to finalize release")

    # Take the release out of draft
    gh = GhApi(owner=match["owner"], repo=match["repo"], token=auth)
    release = util.release_for_url(gh, release_url)

    release = gh.repos.update_release(
        release.id,
        release.tag_name,
        release.target_commitish,
        release.name,
        release.body,
        dry_run,
        release.prerelease,
    )

    # Set the GitHub action output
    util.actions_output("release_url", release.html_url)


def prep_git(branch, remote, repo, username, auth):
    """Set up git"""
    is_action = bool(os.environ.get("GITHUB_ACTIONS"))
    if is_action:
        # Use email address for the GitHub Actions bot
        # https://github.community/t/github-actions-bot-email-address/17204/6
        util.run(
            'git config --global user.email "41898282+github-actions[bot]@users.noreply.github.com"'
        )
        util.run('git config --global user.name "GitHub Action"')

        remotes = util.run("git remote").splitlines()
        if remote not in remotes:
            if auth:
                url = f"http://{username}:{auth}@github.com/{repo}.git"
            else:
                url = f"http://github.com/{repo}.git"
            util.run(f"git remote add {remote} {url}")

    # Make sure we have *all* tags
    util.run(f"git fetch {remote} --tags")

    if branch is None:
        return

    # Check out the remote branch so we can push to it
    util.run(f"git fetch {remote} {branch} --tags")

    branches = util.run("git branch").replace("* ", "").splitlines()
    if branch in branches:
        util.run(f"git checkout {branch}")
    else:
        util.run(f"git checkout -B {branch} {remote}/{branch}")


def forwardport_changelog(
    auth, branch, remote, repo, username, changelog_path, dry_run, release_url
):
    """Forwardport Changelog Entries to the Default Branch"""
    # Set up the git repo
    prep_git(branch, remote, repo, username, auth)

    match = parse_release_url(release_url)
    gh = GhApi(owner=match["owner"], repo=match["repo"], token=auth)
    release = util.release_for_url(gh, release_url)
    tag = release.tag_name

    # Find the default branch
    default_branch = ""
    for line in util.run(f"git remote show {remote}").splitlines():
        if "HEAD branch" in line:
            default_branch = line.strip().split()[-1]
            default_branch = default_branch.split("/")[-1]
            break

    # Check out the default branch
    util.run(f"git checkout -B {default_branch} {remote}/{default_branch}")

    # Bail if the tag has been merged to the default branch
    tags = util.run(f"git --no-pager tag --merged {default_branch}")
    if tag in tags.splitlines():
        print(f"Skipping since tag is already merged into {default_branch}")
        return

    # Get the entry for the tag
    util.run(f"git checkout {tag}")
    entry = changelog.extract_current(changelog_path)

    # Get the previous header for the branch
    full_log = Path(changelog_path).read_text(encoding="utf-8")
    start = full_log.index(changelog.END_MARKER)

    prev_header = ""
    for line in full_log[start:].splitlines():
        if line.strip().startswith("#"):
            prev_header = line
            break

    if not prev_header:
        raise ValueError("No anchor for previous entry")

    # Check out the default branch again
    util.run(f"git checkout -B {default_branch} {remote}/{default_branch}")

    default_entry = changelog.extract_current(changelog_path)

    # Look for the previous header
    default_log = Path(changelog_path).read_text(encoding="utf-8")
    if not prev_header in default_log:
        raise ValueError(
            f'Could not find previous header "{prev_header}" in {changelog_path} on branch {default_branch}'
        )

    # If the previous header is the current entry in the default branch, we need to move the change markers
    if prev_header in default_entry:
        default_log = changelog.insert_entry(default_log, entry)

    # Otherwise insert the new entry ahead of the previous header
    else:
        insertion_point = default_log.index(prev_header)
        default_log = (
            default_log[:insertion_point]
            + entry.lstrip()
            + default_log[insertion_point:]
        )

    Path(changelog_path).write_text(default_log, encoding="utf-8")

    # Create a forward port PR
    commit_message = f'git commit -a -m "Forward port changelog entry from {tag}"'
    title = f"{changelog.PR_PREFIX} Forward Ported from {tag}"
    body = title

    pr = make_changelog_pr(
        auth, default_branch, remote, repo, title, commit_message, body, dry_run=dry_run
    )
