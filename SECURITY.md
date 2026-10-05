# Security Policy

Open Resource Broker (ORB) supports responsible disclosure of security vulnerabilities and adheres to the [FINOS Security Vulnerabilities Policy](https://community.finos.org/docs/governance/Software-Projects/cve-responsible-disclosure). If you believe you have found a security vulnerability in ORB, please report it privately using one of the methods below rather than opening a public issue or discussion.

## Reporting a Vulnerability

- **GitHub Security Advisories (preferred):** Use the ["Report a vulnerability"](https://github.com/finos/open-resource-broker/security/advisories/new) button under the "Security" tab of this repository. This opens a private channel between you and the maintainers and lets GitHub coordinate a CVE if one is warranted.
- **Email:** If you cannot use GitHub Security Advisories, email the maintainers at [open-resource-broker-maintainers@lists.finos.org](mailto:open-resource-broker-maintainers@lists.finos.org) and cc [security@finos.org](mailto:security@finos.org) with a description of the vulnerability.

Please include as much of the following as you can:

- The affected version(s) and component (CLI, REST API, MCP server, a specific provider or scheduler integration, an SDK)
- Steps to reproduce, or a proof of concept
- The potential impact as you understand it

## Supported Versions

Security fixes are made against the latest released minor version line (currently `1.8.x`). We recommend always running the latest release.

## What to Expect

- We aim to acknowledge new reports within a few business days.
- We will work with you to confirm the issue, assess its impact, and agree on a disclosure timeline.
- Once a fix is available, we will publish a release and a GitHub security advisory; CVE assignment, where applicable, goes through that advisory.
- Please do not disclose the vulnerability publicly until a fix has been released and the advisory is published.

## Security Hardening

For a description of the security controls implemented in ORB (authentication, token revocation, input validation, security headers, and related configuration), see the [security hardening reference](docs/root/security/hardening.md) in the documentation.
