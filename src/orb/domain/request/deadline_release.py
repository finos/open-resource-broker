"""Markers recording that a deadline outcome still owes the release of unstarted machines."""

# Request metadata key holding the release state.
DEADLINE_RELEASE_KEY = "deadline_release"

# A terminal deadline outcome left machines that may not have started; release is owed.
RELEASE_PENDING = "pending"
# A fresh provider read showed nothing left to release.
RELEASE_DONE = "done"
RELEASE_NOTHING = "nothing_to_release"
# The request's resource launched nothing and its provider cannot abort it.
RELEASE_UNSUPPORTED = "abort_unsupported"

# Request metadata key recording what an abort of the request's resources found.
DEADLINE_RELEASE_ABORT_KEY = "deadline_release_abort"
