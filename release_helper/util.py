# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
# Of the form:
# https://github.com/{owner}/{repo}/releases/tag/{tag}
import hashlib
import json
import os
import os.path as osp
import re
import shlex
import shutil
from glob import glob
from pathlib import Path
from subprocess import CalledProcessError
from subprocess import check_output


BUF_SIZE = 65536
TBUMP_CMD = "tbump --non-interactive --only-patch"

RELEASE_HTML_PATTERN = (
    "https://github.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases/tag/(?P<tag>.*)"
)

# Of the form:
# https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}
RELEASE_API_PATTERN = "https://api.github.com/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases/tags/(?P<tag>.*)"


def run(cmd, **kwargs):
    """Run a command as a subprocess and get the output as a string"""
    if not kwargs.pop("quiet", False):
        print(f"+ {cmd}")

    parts = shlex.split(cmd)
    if "/" not in parts[0]:
        parts[0] = normalize_path(shutil.which(parts[0]))

    try:
        return check_output(parts, **kwargs).decode("utf-8").strip()
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
        branch = run("git branch --show-current")
    return branch


def get_repo(remote, auth=None):
    """Get the remote repo owner and name"""
    url = run(f"git remote get-url {remote}")
    url = normalize_path(url)
    parts = url.split("/")[-2:]
    if ":" in parts[0]:
        parts[0] = parts[0].split(":")[-1]
    parts[1] = parts[1].replace(".git", "")
    return "/".join(parts)


def get_version():
    """Get the current package version"""
    if osp.exists("setup.py"):
        return run("python setup.py --version")
    elif osp.exists("package.json"):
        return json.loads(Path("package.json").read_text(encoding="utf-8"))["version"]
    else:  # pragma: no cover
        raise ValueError("No version identifier could be found!")


def normalize_path(path):
    """Normalize a path to use backslashes"""
    return str(path).replace(os.sep, "/")


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


def create_release_commit(version, dist_dir="dist"):
    """Generate a release commit that has the sha256 digests for the release files"""
    cmd = f'git commit -am "Publish {version}" -m "SHA256 hashes:"'

    shas = dict()

    files = glob(f"{dist_dir}/*")
    if not files:  # pragma: no cover
        raise ValueError("Missing distribution files")

    for path in sorted(files):
        path = normalize_path(path)
        sha256 = compute_sha256(path)
        shas[path] = sha256
        cmd += f' -m "{path}: {sha256}"'

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

    if not version_cmd and osp.exists("package.json"):
        version_cmd = "npm version --git-tag-version false"

    if not version_cmd:  # pragma: no cover
        raise ValueError("Please specify a version bump command to run")

    # Bump the version
    run(f"{version_cmd} {version_spec}")


def is_prerelease(version):
    """Test whether a version is a prerelease version"""
    final_version = re.match("([0-9]+.[0-9]+.[0-9]+)", version).groups()[0]
    return final_version != version


def release_for_url(gh, url):
    """Get release response data given a release url"""
    release = None
    for rel in gh.repos.list_releases():
        if rel.html_url == url or rel.url == url:
            release = rel
    if not release:
        raise ValueError(f"No release found for url {url}")
    return release


def actions_output(name, value):
    "Print the special GitHub Actions `::set-output` line for `name::value`"
    print(f"::set-output name={name}::{value}")
