# Contributing to AWS Host Factory Plugin

First off, thank you for considering contributing to Open Host Factory Plugin! It's people like you in our community that make this project great.

## Code of Conduct

This project and everyone participating in it is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check [the issue list](https://github.com/awslabs/open-hostfactory-plugin/issues) as you might find out that you don't need to create one. When you are creating a bug report, please include as many details as possible:

* Use a clear and descriptive title
* Describe the exact steps which reproduce the problem
* Provide specific examples to demonstrate the steps
* Describe the behavior you observed after following the steps
* Explain which behavior you expected to see instead and why
* Include screenshots and animated GIFs if possible
* Include your environment details

### Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues. When creating an enhancement suggestion, please provide:

* A clear and descriptive title
* A detailed description of the proposed functionality
* Explain why this enhancement would be useful
* List any alternative solutions or features you've considered
* Include screenshots or diagrams if applicable

### Pull Requests

* Fill in the required template
* Follow the Python style guides
* Include appropriate tests
* Update documentation as needed
* End all files with a newline

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/your-username/awsome-hostfactory-plugin.git
   cd awsome-hostfactory-plugin
   ```

3. Set up your development environment:
   ```bash
   # Create and activate virtual environment
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate

   # Install dependencies
   pip install -e ".[dev]"

   # Set up pre-commit hooks
   pre-commit install
   ```

4. Create a branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Process

1. Make your changes:
   * Write your code
   * Write/update tests
   * Update documentation

2. Run the test suite:
   ```bash
   # Run all tests
   python dev-tools/testing/run_tests.py

   # Run with coverage
   python dev-tools/testing/run_tests.py --coverage

   # Run specific test types
   python dev-tools/testing/run_tests.py --unit
   python dev-tools/testing/run_tests.py --integration
   python dev-tools/testing/run_tests.py --e2e

   # Run specific tests
   python dev-tools/testing/run_tests.py --path tests/test_specific.py
   ```

3. Run linting:
   ```bash
   # Run all linting
   make lint

   # Run specific linters
   black src tests
   isort src tests
   flake8 src tests
   mypy src tests
   ```

4. Build and serve documentation:
   ```bash
   # Build documentation
   make docs-build

   # Serve documentation locally with live reload
   make docs-serve

   # Deploy to GitLab Pages (pushes to main branch)
   make docs-deploy-gitlab

   # Check GitLab Pages status
   make docs-check-gitlab

   # Clean documentation build files
   make docs-clean
   ```

5. Use development tools:
   ```bash
   # Version management
   ./dev-tools/package/version-bump.sh patch

   # Build package
   ./dev-tools/package/build.sh

   # Install in development mode
   ./dev-tools/package/install-dev.sh
   ```

5. Build documentation:
   ```bash
   make docs
   ```

## Style Guides

### Git Commit Messages

* Use the present tense ("Add feature" not "Added feature")
* Use the imperative mood ("Move cursor to..." not "Moves cursor to...")
* Limit the first line to 72 characters or less
* Reference issues and pull requests liberally after the first line

### Python Style Guide

* Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/)
* Use [Black](https://github.com/psf/black) for code formatting
* Use [isort](https://github.com/PyCQA/isort) for import sorting
* Use [mypy](http://mypy-lang.org/) for type checking
* Write docstrings in [Google style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)

### Documentation Style Guide

* Use [Markdown](https://www.markdownguide.org/) for documentation
* Follow [MkDocs](https://www.mkdocs.org/) conventions
* Include code examples when relevant
* Keep language clear and concise

## Project Structure

```
open-hostfactory-plugin/
├── src/                    # Source code
│   ├── api/                # API handlers
│   ├── application/        # Application services
│   ├── domain/             # Domain model
│   ├── infrastructure/     # Infrastructure components
│   ├── providers/          # Cloud provider integrations
│   └── interface/          # CLI interface
├── tests/                  # Test files
├── docs/                   # Documentation
├── dev-tools/              # Development tools
├── scripts/                # Host Factory integration scripts
├── config/                 # Configuration files
└── memory-bank/            # Development notes and plans
```

## Documentation

* Write clear, concise documentation
* Update documentation when adding features
* Test documentation locally before submitting
* Use MkDocs for documentation building

### Documentation Workflow

1. **Local Development**:
   ```bash
   # Start documentation server with live reload
   make docs-serve
   # Visit http://127.0.0.1:8000 to view docs
   ```

2. **Building Documentation**:
   ```bash
   # Build static documentation
   make docs-build
   # Output will be in docs/site/
   ```

3. **GitLab Pages Deployment**:
   ```bash
   # Deploy to GitLab Pages production (main branch)
   make docs-deploy-gitlab

   # Deploy to GitLab Pages staging (develop branch)
   make docs-deploy-staging

   # Check deployment status
   make docs-check-gitlab
   ```

4. **Documentation Structure**:
   - `docs/user-guide.md` - User-facing documentation
   - `docs/configuration/` - Configuration examples and guides
   - `docs/development/` - Development and testing guides
   - `docs/api/` - API reference documentation

### Completion Development

The plugin includes shell completions for improved developer experience:

```bash
# Test completion generation
python src/run.py --completion bash
python src/run.py --completion zsh

# Generate completion files
make generate-completions

# Test completion functionality
make test-completions

# Install for testing
make install-completions
```

**Completion Features:**
- Complete resource names (templates, machines, requests, etc.)
- Complete action names based on selected resource
- Complete global options (--config, --log-level, etc.)
- Complete option values (--format json|yaml|table)
- Complete file paths for --config, --output

## Testing

* Write tests for all new features
* Maintain or improve test coverage
* Use pytest fixtures appropriately
* Mock external services
* Test edge cases

## Documentation

* Update README.md with any needed changes
* Update API documentation when changing interfaces
* Add docstrings to all public methods
* Include examples in documentation
* Keep the wiki up to date

## Community

* Join our [Slack channel](#)
* Follow our [Twitter](#)
* Read our [blog](#)
* Subscribe to our [newsletter](#)

## Additional Notes

### Issue Labels

* `bug` - Something isn't working
* `enhancement` - New feature or request
* `documentation` - Documentation only changes
* `good first issue` - Good for newcomers
* `help wanted` - Extra attention is needed

### Support

If you need help with anything:
* Check our [FAQ](docs/FAQ.md)
* Ask in our [Discussions](https://github.com/awslabs/open-hostfactory-plugin/discussions)
* Contact the maintainers

## Recognition

Contributors will be recognized in:
* The [CONTRIBUTORS.md](CONTRIBUTORS.md) file
* Release notes
* Project documentation

Thank you for contributing to AWS Host Factory Plugin!
