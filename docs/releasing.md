# Releasing Foreman

Foreman's PyPI distribution is `foreman-core`. It provides the `foreman` import package and
`foreman` command.

## One-time setup

1. Create a GitHub environment named `pypi` in the `thruwire/foreman` repository. Add a required
   reviewer if the repository's GitHub plan supports it.
2. In the PyPI account that will own the project, create a pending GitHub Trusted Publisher with:

   - PyPI project name: `foreman-core`
   - GitHub owner: `thruwire`
   - GitHub repository: `foreman`
   - Workflow filename: `release.yml`
   - Environment name: `pypi`

A pending publisher does not reserve the project name. The first successful publication creates
the PyPI project and converts the pending publisher into a normal trusted publisher.

## Release process

1. Update `version` in `pyproject.toml` and the release notes, then merge the change to `main`.
2. Confirm the test suite and package build pass locally.
3. Create a GitHub release from `main` with a tag matching the package version, such as `vX.Y.Z`.
4. Publish the GitHub release. The `release.yml` workflow verifies the tag, runs the tests and
   linter, builds and checks the wheel and source distribution, and publishes them through OpenID
   Connect. No PyPI token is stored in GitHub.
5. Confirm the project page and installation in a clean virtual environment:

   ```bash
   python -m venv /tmp/foreman-release-check
   /tmp/foreman-release-check/bin/python -m pip install foreman-core==X.Y.Z
   /tmp/foreman-release-check/bin/foreman --help
   ```

PyPI does not allow a published file or version to be replaced. If publication succeeds with a
bad artifact, fix the problem and publish a new version.
