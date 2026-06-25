# Agent Guidelines for Open Resource Broker

## Build & Test Commands
```bash
pip install -e ".[testing]"      # Install package with test dependencies
pytest                            # Run all tests
pytest src/orb_k8s_legacy/tests/unit/test_api.py::test_name  # Single test
pytest -k "test_pattern"          # Run tests matching pattern
ruff check .                      # Lint
ruff check --fix .                # Auto-fix lint issues
ruff format .                     # Format code
mypy src/                         # Type checking
```

## Architecture
- **CLI**: `src/orb_k8s_legacy/cli/` - Click-based commands (hf.py, hfadmin.py, hfutils.py)
- **API**: `src/orb_k8s_legacy/api.py` - Core request/return machine APIs
- **Watchers**: `src/orb_k8s_legacy/impl/watchers/` - Kubernetes pod/node event watchers
- **Events**: SQLite-based event storage with Alembic migrations
- **Tests**: `src/orb_k8s_legacy/tests/unit/` (unit), `tests/regression/` (integration)

## Code Style
- Python 3.12+, strict ruff linting (see ruff.toml)
- Single-line imports (`isort.force-single-line = true`), alphabetically sorted
- Use named loggers: `logger = logging.getLogger(__name__)` (no root-level logging)
- Docstrings required for modules, classes, and public functions
- Use `pathlib.Path` over `os.path`; avoid `sys.path` manipulation
