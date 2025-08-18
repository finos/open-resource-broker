# Security Policy

## Supported Versions

We release patches for security vulnerabilities for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| 1.x.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

We take the security of AWS Host Factory Plugin seriously. If you believe you have found a security vulnerability, please report it to us as described below.

### Where to Report

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them via email to:
- Primary: security@awshostfactory.example.com
- Secondary: aws-hostfactory-security@amazon.com

You should receive a response within 24 hours. If for some reason you do not, please follow up via email to ensure we received your original message.

### What to Include

Please include the following information in your report:

- Type of issue (e.g., buffer overflow, SQL injection, cross-site scripting, etc.)
- Full paths of source file(s) related to the manifestation of the issue
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit it

### What to Expect

After you have submitted your report:

1. We will acknowledge your report within 24 hours
2. We will provide a more detailed response within 72 hours
   - Indicating the next steps in handling your report
   - If we can reproduce the issue
   - If we need additional information
3. We will keep you informed of our progress
   - How we plan to resolve the issue
   - If we need additional information
   - If we have questions

### Protection Policy

We follow these principles:

- We will investigate all legitimate reports and do our best to quickly fix the problem
- We will keep you informed of the progress towards a fix
- We will not take legal action against you if you:
  - Follow the instructions above
  - Give us reasonable time to respond before disclosure
  - Do not exploit the vulnerability beyond necessary testing
  - Do not share information about the vulnerability until we fix it

### Safe Harbor

We consider security research conducted under this policy to be:
- Authorized in accordance with the Computer Fraud and Abuse Act (CFAA)
- Exempt from DMCA restrictions
- Exempt from restrictions in our Terms of Service that would interfere with conducting security research
- Lawful, helpful to the overall security of the Internet, and conducted in good faith

You are expected, as always, to comply with all applicable laws.

### Public Disclosure

We aim to resolve security issues as quickly as possible. We would like to ask that you do not share information about the vulnerability until we have had the opportunity to fix it and notify our users.

Once we have resolved the issue, we will:
1. Notify affected users
2. Release a security advisory
3. Credit you (if desired) for discovering and reporting the issue

### Security Best Practices

When using this plugin:

1. Always use the latest version
2. Follow AWS security best practices
3. Use the principle of least privilege for AWS credentials
4. Regularly rotate credentials
5. Monitor AWS CloudTrail logs
6. Enable AWS CloudWatch monitoring
7. Use secure network configurations
8. Implement appropriate access controls

### Scope

This security policy applies to:
- The latest release of the AWS Host Factory Plugin
- The main branch of our GitHub repository
- All official documentation and examples

### Out of Scope

The following are not in scope:
- Issues in dependencies (please report to their maintainers)
- Theoretical vulnerabilities without proof of exploitability
- Issues requiring physical access to a user's device
- Social engineering attacks
- DOS/DDOS attacks

## Security Updates

Security updates will be released as part of our regular release cycle unless a critical vulnerability requires an immediate release.

### Version Numbering

We follow Semantic Versioning:
- MAJOR version for incompatible API changes
- MINOR version for backwards-compatible functionality
- PATCH version for backwards-compatible bug fixes and security updates

### Update Process

1. Security updates are marked with a "SECURITY" tag in the changelog
2. Critical updates will be announced via:
   - GitHub Security Advisories
   - Release notes
   - Our official communication channels

### Automatic Updates

We recommend:
1. Using dependency management tools that support automatic updates
2. Regularly checking for updates
3. Setting up automated security scanning in your CI/CD pipeline

## Security-Related Configuration

For secure deployment, ensure:

1. AWS IAM roles follow least privilege
2. Network security groups are properly configured
3. Encryption is enabled for data at rest
4. Secure communication channels are used
5. Logging and monitoring are enabled
6. Access controls are implemented
7. Regular security audits are performed

## Contact

For questions about this security policy, please contact:
security@awshostfactory.example.com
