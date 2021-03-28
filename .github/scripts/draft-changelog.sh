set -eux
pip install -q release-helper

release-helper prep-env
release-helper build-changelog
release-helper draft-changelog
cat ${RH_CHANGELOG}
