set -eux
pip install -q release-helper

release-helper prep-env
release-helper check-changelog
# Make sure npm comes before python in case it produces
# files for the python package
release-helper build-npm
release-helper check-npm
release-helper build-python
release-helper check-python
release-helper check-manifest
release-helper check-links
release-helper tag-release
release-helper draft-release
