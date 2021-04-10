# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
from jupyter_releaser.utils import run

run("python -m jupyter_releaser.actions.draft_changelog")
run("python -m jupyter_releaser.actions.draft_release")
output = run("python -m jupyter_releaser.actions.publish_changelog")
release_url = output.splitlines()[0]
# Delete Draft Release
run(f"jupyter-releaser delete-release {release_url}")
