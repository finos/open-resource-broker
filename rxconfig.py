# Re-export from the packaged copy so that ``reflex run`` from the repo root
# works during local development.  The installed wheel uses the copy at
# ``src/orb/ui/rxconfig.py`` directly (``run_embedded_foreground`` sets cwd
# to that directory before exec-ing reflex).
from orb.ui.rxconfig import config as config  # noqa: F401
