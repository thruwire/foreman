# Releasing Foreman

Foreman's PyPI distribution is `foreman-core`. It provides the `foreman` import package and
`foreman` command.

Prepared release notes: [Foreman 0.4.4](releases/0.4.4.md).

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

## Pi bridge npm release

The separate npm package is `@thruwire/foreman-pi`, maintained in `integrations/pi`.
Its version is independent of the Python core. The first bridge release is 0.1.0
and requires `foreman-core >= 0.4.4`; publish the core first. PyPI 0.4.3 does not
contain the Pi or Pi Durable adapters.

Create a personal npm account, enable two-factor authentication, and create or
join the `thruwire` npm organization with publishing access. The personal
username does not need to be `thruwire`. Public npm packages do not require a
paid private-package plan.

For the first release, publish interactively from the verified checkout:

```bash
# Install the current core into the Python environment used by bridge tests.
python -m pip install .
cd integrations/pi
npm ci
npm test
npm pack --dry-run
npm login --registry=https://registry.npmjs.org/
npm publish --access public
```

If using a virtual environment, set `FOREMAN_TEST_PYTHON` to its absolute Python
executable for `npm test`. Packing automatically builds `dist` and includes the
bridge's MIT license. Check the packed artifact's Pi extension loading and
Durable import in a separate installation before publishing.

After publication, confirm `npm view @thruwire/foreman-pi@0.1.0 version` and
`pi install npm:@thruwire/foreman-pi@0.1.0` in an isolated Pi configuration. Remove
outdated installation language only after verifying the releases. The existing
`release.yml` workflow publishes
the Python core only; an npm release still needs the manual steps above.
