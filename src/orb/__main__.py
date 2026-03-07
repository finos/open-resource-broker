"""Allow running ORB as ``python -m orb``."""

import sys

sys.argv[0] = __package__ or sys.argv[0]

from orb.run import cli_main

cli_main()
