# Security

SmartRAM Guardian runs as the current user, needs no admin rights and never
connects to the network. It can close processes that belong to that user, so a
bug that makes it close the wrong thing is treated as a security issue.

## Reporting

Please report privately through the repository's **Security** tab
("Report a vulnerability"). If that is not available, open an issue that says
only that you have a security report, and a maintainer will arrange a private channel.

Include the output of `smartram doctor`, what happened and how to reproduce it.

## Supported versions

Fixes are made on the latest release.
