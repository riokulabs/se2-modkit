<!-- managed by riokulabs/.github - edit there (templates/), not here -->
# Security policy

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting instead:

1. Go to the **Security** tab of this repository.
2. Choose **Report a vulnerability**.
3. Describe what you found and how to reproduce it.

Only the maintainer can see the report. You will get a first reply within about seven days. Fixes are made in private and credited to you in the release notes if you want that.

## Supported versions

Only the latest release gets fixes.

## Scope

- Token handling: the tool never asks for, reads, stores or logs Steam credentials or API tokens. A way to make it do so is a vulnerability.
- Anything that uploads, publishes or contacts a remote service without explicit user approval.
- Path handling: anything that lets a project config or mod repo make the tool read or write files outside that repo.
