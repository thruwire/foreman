# Security Policy

## Supported versions

Foreman is experimental and does not currently have a long-term support schedule. Security fixes
target the latest release and the `main` branch.

## Reporting a vulnerability

Please do not report security vulnerabilities in a public issue.

Use GitHub's private **Report a vulnerability** option in the repository's Security tab. If that
option is unavailable, contact the repository owner privately using the contact information on
[their GitHub profile](https://github.com/thruwire).

Include the affected version or commit, reproduction steps, impact, and any suggested mitigation.
You should receive an acknowledgment when the report has been reviewed; response and fix timing
will depend on severity and maintainer availability.

Remember that Foreman launches coding workers with the permissions of the local environment. It is
not a security sandbox, and untrusted repositories or jobs should not be run without isolation.
