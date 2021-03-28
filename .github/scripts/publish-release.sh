
set -eux
pip install -q release-helper

local release_url=$1

export TWINE_USERNAME=${TWINE_USERNAME:-"__token__"}

if [ ${RH_DRY_RUN} == 'true' ]; then
    export TWINE_COMMAND=${TWINE_COMMAND:-"twine upload --skip-existing"}
    export TWINE_REPOSITORY_URL=${TWINE_REPOSITORY_URL:-"https://test.pypi.org/legacy/"}
    export RH_NPM_COMMAND=${NPM_COMMAND:-"npm publish --dry-run"}
fi

release-helper extract-release ${release_url}
release-helper forwardport-changelog ${release_url}
release-helper publish-release ${release_url}

if [ ${RH_DRY_RUN} == 'true']; then
    release-helper delete-release ${release_url}
