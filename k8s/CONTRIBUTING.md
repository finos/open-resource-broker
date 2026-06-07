Open Resource Broker Contribution and Governance Policies

This document describes the contribution process and governance policies of the FINOS {project name} project. The project is also governed by the Linux Foundation Antitrust Policy, and the FINOS IP Policy, Code of Conduct, Collaborative Principles, and Meeting Procedures.

# Contributing

When contributing to this repository, please first discuss the change you wish 
to make via issue, email, or any other method with the maintainers of this repository
before making a change.

Please note we have a code of conduct, please follow it in all your interactions
with the project.

## Before your first pull request

All contributors must have a contributor license agreement (CLA) on file with FINOS before their pull requests will be merged. Please review the FINOS [contribution requirements](https://community.finos.org/docs/governance/Software-Projects/contribution-compliance-requirements) and submit (or have your employer submit) the required CLA before submitting a pull request.

## Pull request process 

When you create a pull request, follow these steps:

1. Ensure all contributors listed on the PR are covered by a CLA. Follow instructions from EasyCLA bot if needed.
2. Your commit must include a change to the `NOTICE` file that contains complete
details of any applicable copyright notice for your submission and including any
applicable third party license(s) or other restrictions associated with any part
of your contribution, and of all matters required to be disclosed under such third
party license(s) (such as any applicable copyright, patent, trademark, and attribution
notices, and any notices relating to modifications made to open source software).
Note your contribution must retain all applicable copyright, patent, trademark and
attribution notices.

## Pull request guidelines

* Update the README.md/docs with details of changes to the interface.
* Update an existing or add a new testcase for your change.
* Ensure any install or build artifacts are removed from the pull request.
* We generally prefer squashed commits, unless multi-commits add clarity or are required for mixed copyright commits.
* You may merge the Pull Request in once the build has passed and you have the
   sign-off of one other developer, or if you do not have permission to do that,
   you may request the reviewer to merge it for you.

## Governance

### Roles

The project community consists of Contributors and Maintainers:
* A **Contributor** is anyone who submits a contribution to the project. (Contributions may include code, issues, comments, documentation, media, or any combination of the above.)
* A **Maintainer** is a Contributor who, by virtue of their contribution history, has been given write access to project repositories and may merge approved contributions.
* The **Lead Maintainer** is the project's interface with the FINOS team and Board. They are responsible for approving [quarterly project reports](https://community.finos.org/docs/governance/#project-governing-board-reporting) and communicating on behalf of the project. The Lead Maintainer is elected by a vote of the Maintainers. 

### Contribution Rules

Anyone is welcome to submit a contribution to the project. The rules below apply to all contributions. (The key words "MUST", "SHALL", "SHOULD", "MAY", etc. in this document are to be interpreted as described in [IETF RFC 2119](https://www.ietf.org/rfc/rfc2119.txt).)

* All contributions MUST be submitted as pull requests, including contributions by Maintainers.
* All pull requests SHOULD be reviewed by a Maintainer (other than the Contributor) before being merged.
* Pull requests for non-trivial contributions SHOULD remain open for a review period sufficient to give all Maintainers a sufficient opportunity to review and comment on them.
* After the review period, if no Maintainer has an objection to the pull request, any Maintainer MAY merge it.
* If any Maintainer objects to a pull request, the Maintainers SHOULD try to come to consensus through discussion. If not consensus can be reached, any Maintainer MAY call for a vote on the contribution.

### Maintainer Voting

The Maintainers MAY hold votes only when they are unable to reach consensus on an issue. Any Maintainer MAY call a vote on a contested issue, after which Maintainers SHALL have 36 hours to register their votes. Votes SHALL take the form of "+1" (agree), "-1" (disagree), "+0" (abstain). Issues SHALL be decided by the majority of votes cast. If there is only one Maintainer, they SHALL decide any issue otherwise requiring a Maintainer vote. If a vote is tied, the Lead Maintainer MAY cast an additional tie-breaker vote.

The Maintainers SHALL decide the following matters by consensus or, if necessary, a vote:
* Contested pull requests
* Election and removal of the Lead Maintainer
* Election and removal of Maintainers

All Maintainer votes MUST be carried out transparently, with all discussion and voting occurring in public, either:
* in comments associated with the relevant issue or pull request, if applicable;
* on the project mailing list or other official public communication channel; or
* during a regular, minuted project meeting.

### Maintainer Qualifications

Any Contributor who has made a substantial contribution to the project MAY apply (or be nominated) to become a Maintainer. The existing Maintainers SHALL decide whether to approve the nomination according to the Maintainer Voting process above.

### Changes to this Document

This document MAY be amended by a vote of the Maintainers according to the Maintainer Voting process above.