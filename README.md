# Release Helper

## Motivation

A set of helper scripts and example GitHub Action workflows to aid in automated releases of Python and npm packages.

- Enforces best practices:

  - Has automated changelog for every release (optional)
  - Is published to test server and verified with install and import of dist asset(s)
  - Has commit message with hashes of dist file(s)
  - Has annotated git tag in standard format
  - Has GitHub release with changelog entry
  - Checks url links in markdown files
  - Verifies integrity of Python manifest
  - Reverts to Dev version after release (optional)
  - Ensures packages are publishable on every commit

- Prerequisites (see [checklist](#Checklist-for-Adoption) below for details):

  - Markdown changelog (optional)
  - Bump version configuration (if using Python)
  - Write access to GitHub repo to run GitHub Actions
  - Access token for the test [PyPI registry](https://packaging.python.org/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/#saving-credentials-on-github) stored as `TEST_PYPI_TOKEN`

- Typical workflow:
  - When ready to make a release, go to the source repository and go to the Actions panel
  - Select the Draft Changelog workflow
  - Run the Workflow with the version spec (usually the new version number), and make sure the target branch is correct

<p align="center">
<img src="media/create_changelog_workflow.png" alt="Create Changelog Workflow"
	title="Create Changelog Workflow" width="50%"/>
</p>

- When the run completes, review the changelog PR that was opened, making any desired edits

<p align="center">
<img src="media/changelog_pr.png" alt="Changelog Pull Request"
	title="Changelog Pull Request" width=80% />
</p>

- Merge the PR
- Return to the Actions panel
- Select the Draft Release workflow

<p align="center">
<img src="media/create_release_workflow.png" alt="Create Release Workflow"
	title="Create Release Workflow" width="50%" />
</p>

- Run the Workflow with the same version spec as before, and an optional post version spec if you want to go back to a dev version in the target branch.
- When the workflow completes, go to the releases page in the main repository and verify that the new draft release is there with the correct changelog and dist files.

<!-- TODO: Add Github release image here -->

- Run the Publish Release workflow from a fork or manually download an publish the dist file(s).

## Installation

To install the latest release locally, make sure you have
[pip installed](https://pip.readthedocs.io/en/stable/installing/) and run:

```
    pip install git+https://github.com/jupyter-server/release-helper
```

## Usage

```
    release-helper --help
    release-helper build-python --help
```

## Checklist for Adoption

**Note**: The automated changelog handling is optional. If it is not desired, you can use the
`check_release` and `draft_release` workflows only and leave the changelog calls out of them. You will need to generate your own text for the GitHub release.

- [ ] Switch to Markdown Changelog
  - We recommend [MyST](https://myst-parser.readthedocs.io/en/latest/?badge=latest), especially if some of your docs are in reStructuredText
  - Can use `pandoc -s changelog.rst -o changelog.md` and some hand edits as needed
  - Note that [directives](https://myst-parser.readthedocs.io/en/latest/using/syntax.html#syntax-directives) can still be used
- [ ] Add HTML start and end comment markers to Changelog file - see example in [CHANGELOG.md](./CHANGELOG.md) (view in raw mode)
- [ ] Add [tbump](https://github.com/tankerhq/tbump) support if using Python - see example metadata in [pyproject.toml](./pyproject.toml)

  - We recommend using `setup.cfg` and using `version attr: <package_name>.__version__`, see example [`setup.cfg`](./setup.cfg)
  - See documentation on `setup.cfg` [metadata](https://setuptools.readthedocs.io/en/latest/userguide/declarative_config.html)

- [ ] Add workflows for `check_release`, `draft_changelog`, and `draft_release` - see the workflows in this [repo](./.github/workflows)
- [ ] Change the action calls from the local `./.github/actions/<foo>` to `jupyter-server/release-helper.github/actions/<foo>/@<version_or_branch>`
- [ ] Optionally add workflow for `cancel` to cancel previous workflow runs when a new one is started - see [cancel.yml](./.github/workflows/cancel.yml)
- [ ] Optionally make a new branch or repository on your personal account that has a `publish-release` [workflow](./.github/workflows/publish-release.yml)
  - [ ] You will need to add access tokens for [PyPI](https://packaging.python.org/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/#saving-credentials-on-github) and/or [npm](https://docs.npmjs.com/creating-and-viewing-access-tokens)
  - [ ] Start with the test PyPI server in `publish-release`, then switch to the production server once it is fully working
  - [ ] ⚠ Warning - It is not recommended that you run this workflow or store PyPI/npm credentials on the source repository. Anyone with write access can run a workflow, and access tokens belong to an individual.
- [ ] If desired, add workflows, changelog, and `tbump` support to other active release branches

## Draft ChangeLog Workflow Details

- Manual Github workflow
- Input is the version spec
- Targets the branch selected when starting the workflow
- Bumps the version
  - By default, uses [tbump](https://github.com/tankerhq/tbump) or [bump2version](https://github.com/c4urself/bump2version) to bump the version based on presence of config files
    - We recommend `tbump` instead of `bump2version` for most cases because it does not handle patch releases well when using [prereleases](https://github.com/c4urself/bump2version/issues/190).
- Prepares the environment
  - Sets up git config and branch
  - Exports environment variables to [`GITHUB_ENV`](https://docs.github.com/en/actions/reference/environment-variables) so they can be used in further steps
- Generates a changelog (using [github-activity](https://github.com/executablebooks/github-activity)) using the PRs since the last tag on this branch.
  - Gets the current version and then does a git checkout to clear state
  - Adds a new version entry using a HTML comment markers in the changelog file
  - Optionally resolves [meeseeks](https://github.com/MeeseeksBox/MeeseeksDev) backport PRs to their original PR
- Creates a PR with the changelog changes
- Notes:
  - This can be run on the repo by anyone with write access, since it only needs the built in `secrets.GITHUB_ACCESS_TOKEN`
  - The automated PR does not start workflows (a limitation of GitHub Actions). If you close and open the PR or make edits from within the
    GitHub UI it will trigger the workflows.
  - Can be re-run using the same version spec. It will add new entries but preserve existing ones (in case they have been hand modified).

## Draft Release Workflow Details

- Manual Github workflow
- Takes a version spec and optional post version spec
- Bumps version using the same method as the changelog action
- Prepares the environment using the same method as the changelog action
- Checks the changelog entry
  - Looks for the current entry using the HTML comment markers
  - Gets the expected changelog values using `github-activity`
  - Ensures that all PRs are the same between the two
  - Writes the changelog entry out to a file to be used as the GitHub Release text
- For Python packages:
  - Builds the wheel and source distributions if applicable
  - Makes sure Python dists can be installed and imported in a virtual environment
- For npm/lerna packages:
  - Builds tarball(s) using `npm pack`
  - Make sure tarball(s) can be installed and imported in a new npm package
- Checks the package manifest using [`check-manifest`](https://github.com/mgedmin/check-manifest)
- Checks the links in Markdown files
- Adds a commit that includes the hashes of the dist files
- Creates an annotated version tag in standard format
- If given, bumps the version using the post version spec
- Pushes the commits and tag to the target `branch`
- Publishes a draft GitHub release for the tag with the changelog entry as the text
- Notes:
  - This can be run on the repo by anyone with write access, since it only needs the built in `secrets.GITHUB_ACCESS_TOKEN`

## Publish Release Workflow Details

- Manual Github workflow
- Takes a url for the draft release as an input
- Downloads the dist assets from the release
- Verifies shas and integrity of release assets
- Publishes assets to appropriate registries
- Publishes the draft GitHub release
- ⚠ Warning - It is not recommended that you run this workflow or store PyPI/npm credentials on the source repository. Anyone with write access can run a workflow, and access tokens belong to an individual.

## Check Release Workflow Details

- Runs on pull requests to the default branch and on push
- Runs the Draft Changelog, Draft Release, and Publish Release Steps
- Publishes to the Test PyPI server
- Deletes the Release
- Does not make PRs or push git changes

## TODO

- Use https://raw.githubusercontent.com for README images

- Add support for `.release-helper.toml` config

  - Allow declaritve `pre-`, `post-`, and `skip-` hooks to be run for the different steps so we can support more complex packages
  - Handle the config at the main cli level

- jupyter/notebook migration:

  - Can be done immediately using the checklist
  - Add `tbump` config to replace [`jsversion`](https://github.com/jupyter/notebook/blob/4b2e849e83fcf9ffbe0755d38d635e34e56a4fea/setupbase.py#L583) step
  - Add `babel` and `npm` dependencies in the install step of the new workflows

- lerna support

  - Add to [@jupyterlab/buildutils](https://github.com/jupyterlab/jupyterlab/tree/833cd34de5f7b246208744662c2d4bd62cc3bb35/buildutils/src)
  - Add ability to start/stop a [verdaccio server](https://github.com/facebook/create-react-app/blob/7e4949a20fc828577fb7626a3262832422f3ae3b/tasks/verdaccio.yaml)
  - Use the [publish script](https://github.com/jupyterlab/jupyterlab/blob/532eb4161c01bc7e93e86c4ecb8cd1728e498458/buildutils/src/publish.ts) so we pick up `dist-tag` handling. Add option to pass `--yes` for CLI
  - Create a temporary npm package and install/require the new packages

- jupyterlab/lumino migration:

  - We need to manually update the JS versions since we are in lerna independent mode. We will push the commit and resulting tags directly to the branch.
  - Use the top level `package.json` as the versioned file (but still keep it private)
  - After creating the changelog, use the date instead of the version and add the JS packages

- jupyterlab/jupyterlab migration:
  - Pass a `--yes` flag to lerna `version` and `publish` when releasing on CI
  - Keep using `bump2version` since we need to use them for the JS packages, but collapse patch release into `jlpm bumpversion patch`
  - Since we're using verdaccio, we don't need to wait for packages to be available to run [`update-core-mode`](https://github.com/jupyterlab/jupyterlab/blob/9f50c45b39e289072d4c18519ca29c974c226f69/buildutils/src/update-core-mode.ts), so we can just run that directly and remove `prepare-python-release`
  - Start verdaccio, publish all packages, update core mode, do release test, all before actually publishing to npm
  - We may have to update the `jupyterlab/staging/yarn.lock` file to replace the verdaccio registry with the public one.
  - Add `lab-prod` endpoint in [binder](https://github.com/jupyterlab/jupyterlab/blob/9f50c45b39e289072d4c18519ca29c974c226f69/binder/jupyter_notebook_config.py#L17) so we can actually test with "released" packages for a given commit
