#!/bin/bash
# Quick start script for Open Host Factory Plugin development

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

print_status "Open Host Factory Plugin - Quick Start"
echo ""

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    print_error "Python 3 is required but not installed"
    exit 1
fi

print_status "Setting up development environment..."

# Install development dependencies
print_status "Installing development dependencies..."
make dev-install

print_success "Development environment setup complete!"
echo ""

print_status "What's available:"
echo ""
echo "Testing:"
echo "  make test              - Run tests"
echo "  make test-cov          - Run tests with coverage"
echo "  make test-html         - Generate HTML coverage report"
echo ""
echo "Documentation:"
echo "  make docs-serve        - Start documentation server (http://127.0.0.1:8000)"
echo "  make docs-build        - Build static documentation"
echo ""
echo "Code Quality:"
echo "  make lint              - Run all linting checks"
echo "  make format            - Format code with Black and isort"
echo "  make security          - Run security checks"
echo ""
echo "Development:"
echo "  make dev               - Quick development workflow (format, lint, test)"
echo "  make run-dev           - Run application in development mode"
echo ""
echo "Version Management:"
echo "  make version-bump-patch - Bump patch version"
echo "  make version-bump-minor - Bump minor version"
echo "  make version-bump-major - Bump major version"
echo ""
echo "Docker:"
echo "  make docker-build      - Build Docker image"
echo "  make docker-compose-up - Start with docker-compose"
echo ""

print_status "Quick commands to get started:"
echo ""
echo "1. Run tests:           make test"
echo "2. Start docs server:   make docs-serve"
echo "3. Development cycle:   make dev"
echo "4. Show all commands:   make help"
echo ""

print_success "Ready to develop!"
