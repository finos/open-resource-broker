"""Markers recording that a deadline outcome still owes the release of unstarted machines."""

# Request metadata key holding the release state.
DEADLINE_RELEASE_KEY = "deadline_release"

# A terminal deadline outcome left machines that may not have started; release is owed.
RELEASE_PENDING = "pending"
# A fresh provider read showed nothing left to release.
RELEASE_DONE = "done"
RELEASE_NOTHING = "nothing_to_release"
