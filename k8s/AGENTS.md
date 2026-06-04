# Agent Guidelines for Open Resource Broker

## Build & Test Commands
```bash
pip install -e ".[testing]"      # Install package with test dependencies
pytest                            # Run all tests
pytest src/open_resource_broker/tests/unit/test_api.py::test_name  # Single test
pytest -k "test_pattern"          # Run tests matching pattern
ruff check .                      # Lint
ruff check --fix .                # Auto-fix lint issues
ruff format .                     # Format code
mypy src/                         # Type checking
```

## Architecture
- **CLI**: `src/open_resource_broker/cli/` - Click-based commands (hf.py, hfadmin.py, hfutils.py)
- **API**: `src/open_resource_broker/api.py` - Core request/return machine APIs
- **Watchers**: `src/open_resource_broker/impl/watchers/` - Kubernetes pod/node event watchers
- **Events**: SQLite-based event storage with Alembic migrations
- **Tests**: `src/open_resource_broker/tests/unit/` (unit), `tests/regression/` (integration)

## Code Style
- Python 3.12+, strict ruff linting (see ruff.toml)
- Single-line imports (`isort.force-single-line = true`), alphabetically sorted
- Use named loggers: `logger = logging.getLogger(__name__)` (no root-level logging)
- Docstrings required for modules, classes, and public functions
- Use `pathlib.Path` over `os.path`; avoid `sys.path` manipulation
