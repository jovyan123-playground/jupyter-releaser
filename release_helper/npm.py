# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
import json
import os
import os.path as osp
import shutil
import tarfile
from glob import glob
from pathlib import Path
from tempfile import TemporaryDirectory

from release_helper import util


def extract_tarball(path):
    """Get the package json info from the tarball"""
    fid = tarfile.open(path)
    data = fid.extractfile("package/package.json").read()
    data = json.loads(data.decode("utf-8"))
    fid.close()
    return data


def build_dist(package, dist_dir):
    """Build npm dist file(s) from a package"""
    # Clean the dist folder of existing npm tarballs
    os.makedirs(dist_dir, exist_ok=True)
    dest = Path(dist_dir)
    for pkg in glob(f"{dist_dir}/*.tgz"):
        os.remove(pkg)

    if osp.isdir(package):
        tarball = osp.join(os.getcwd(), util.run("npm pack"))
    else:
        tarball = package

    data = extract_tarball(tarball)

    # Move the tarball into the dist folder if public
    if not data.get("private", False) == True:
        shutil.move(tarball, dest)
    elif osp.isdir(package):
        os.remove(tarball)

    if "workspaces" in data:
        packages = data["workspaces"].get("packages", [])
        for pattern in packages:
            for path in glob(pattern, recursive=True):
                path = Path(path)
                tarball = path / util.run("npm pack", cwd=path)
                data = extract_tarball(tarball)
                if not data.get("private", False) == True:
                    shutil.move(str(tarball), str(dest))
                else:
                    os.remove(tarball)


def check_dist(dist_dir, test_cmd=None):
    """Check npm dist file(s) in a dist dir"""
    packages = glob(f"{dist_dir}/*.tgz")

    if not test_cmd:
        test_cmd = "node index.js"

    tmp_dir = Path(TemporaryDirectory().name)
    os.makedirs(tmp_dir)

    util.run("npm init -y", cwd=tmp_dir)
    names = []
    staging = tmp_dir / "staging"

    deps = []

    for package in packages:
        path = Path(package)
        if path.suffix != ".tgz":
            print(f"Skipping non-npm package {path.name}")
            continue

        data = extract_tarball(path)
        name = data["name"]

        # Skip if it is a private package
        if data.get("private", False):  # pragma: no cover
            print(f"Skipping private package {name}")
            continue

        names.append(name)

        pkg_dir = staging / name
        if not pkg_dir.parent.exists():
            os.makedirs(pkg_dir.parent)

        tar = tarfile.open(path)
        tar.extractall(staging)
        tar.close()

        shutil.move(staging / "package", pkg_dir)

    install_str = " ".join(f"./staging/{name}" for name in names)

    util.run(f"npm install {install_str}", cwd=tmp_dir)

    text = "\n".join([f'require("{name}")' for name in names])
    tmp_dir.joinpath("index.js").write_text(text, encoding="utf-8")

    util.run(test_cmd, cwd=tmp_dir)

    shutil.rmtree(tmp_dir, ignore_errors=True)


def handle_auth_token(npm_token):
    npmrc = Path(".npmrc")
    text = "//registry.npmjs.org/:_authToken={npm_token}"
    if npmrc.exists():
        text = npmrc.read_text(encoding="utf-8") + text
    npmrc.write_text(text, encoding="utf-8")


def get_package_versions(version):
    """Get the formatted list of npm package names and versions"""
    message = ""
    data = json.loads(Path("package.json").read_text(encoding="utf-8"))
    if data["version"] != version:
        message += f"\nPython version: {version}"
        message += f'\nnpm version: {data["name"]}: {data["version"]}'
    if "workspaces" in data:
        message += "\nnpm workspace versions:"
        packages = data["workspaces"].get("packages", [])
        for pattern in packages:
            for path in glob(pattern, recursive=True):
                text = Path(path / "package.json").read_text()
                data = json.loads(text)
                message += f'\n{data["name"]}: {data["version"]}'
    return message


def tag_workspace_packages():
    """Generate tags for npm workspace packages"""
    package_json = Path("package.json")
    if not package_json.exists():
        return

    data = json.loads(package_json.read_text(encoding="utf-8"))
    tags = util.run("git tag").splitlines()
    if not "workspaces" in data:
        return

    packages = data["workspaces"].get("packages", [])
    for pattern in packages:
        for path in glob(pattern, recursive=True):
            sub_package_json = Path(path) / "package.json"
            sub_data = json.loads(sub_package_json.read_text(encoding="utf-8"))
            tag_name = f"{sub_data['name']}@{sub_data['version']}"
            if tag_name in tags:
                print(f"Skipping existing tag {tag_name}")
            else:
                util.run(f"git tag {tag_name}")
