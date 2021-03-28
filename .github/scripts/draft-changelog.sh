set -eux
pip install -q release-helper

release-helper prep-git
release-helper bump-version
release-helper build-changelog
release-helper draft-changelog
cat ${RH_CHANGELOG}
