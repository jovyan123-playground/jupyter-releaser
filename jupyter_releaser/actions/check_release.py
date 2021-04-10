# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
import os

from jupyter_releaser.util import run

run("python -m jupyter_releaser.actions.draft_changelog")
output = run("python -m jupyter_releaser.actions.draft_release")
os.environ["release_url"] = output.splitlines()[0]
output = run("python -m jupyter_releaser.actions.publish_release")
release_url = output.splitlines()[0]
# Delete Draft Release
run(f"jupyter-releaser delete-release {release_url}")
