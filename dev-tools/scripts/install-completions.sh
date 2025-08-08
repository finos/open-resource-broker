#!/bin/bash
# Install shell completions for ohfp command

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

# Check if ohfp command is available
if ! command -v python &> /dev/null; then
    print_error "Python is required but not installed"
    exit 1
fi

if [ ! -f "src/run.py" ]; then
    print_error "src/run.py not found. Please run from project root."
    exit 1
fi

print_status "Installing shell completions for ohfp command"
echo ""

# Detect shell
SHELL_NAME=$(basename "$SHELL")
INSTALL_BASH=false
INSTALL_ZSH=false

case "$1" in
    bash)
        INSTALL_BASH=true
        ;;
    zsh)
        INSTALL_ZSH=true
        ;;
    --uninstall)
        print_status "Uninstalling completions..."
        rm -f ~/.local/share/bash-completion/completions/ohfp
        rm -f ~/.local/share/zsh/site-functions/_ohfp
        print_success "Completions uninstalled"
        exit 0
        ;;
    *)
        # Auto-detect shell
        if [[ "$SHELL_NAME" == "bash" ]]; then
            INSTALL_BASH=true
        elif [[ "$SHELL_NAME" == "zsh" ]]; then
            INSTALL_ZSH=true
        else
            print_warning "Could not detect shell type. Installing both bash and zsh completions."
            INSTALL_BASH=true
            INSTALL_ZSH=true
        fi
        ;;
esac

# Install bash completions
if [ "$INSTALL_BASH" = true ]; then
    print_status "Installing bash completions..."

    # Create directory
    mkdir -p ~/.local/share/bash-completion/completions

    # Generate and install completion
    python src/run.py --completion bash > ~/.local/share/bash-completion/completions/ohfp

    print_success "Bash completions installed to ~/.local/share/bash-completion/completions/ohfp"

    # Check if bash-completion is sourced
    if ! grep -q "bash-completion" ~/.bashrc 2>/dev/null; then
        print_warning "You may need to add this to your ~/.bashrc:"
        echo "  # Enable bash completion"
        echo "  if [ -f ~/.local/share/bash-completion/completions/ohfp ]; then"
        echo "      source ~/.local/share/bash-completion/completions/ohfp"
        echo "  fi"
        echo ""
    fi
fi

# Install zsh completions
if [ "$INSTALL_ZSH" = true ]; then
    print_status "Installing zsh completions..."

    # Create directory
    mkdir -p ~/.local/share/zsh/site-functions

    # Generate and install completion
    python src/run.py --completion zsh > ~/.local/share/zsh/site-functions/_ohfp

    print_success "Zsh completions installed to ~/.local/share/zsh/site-functions/_ohfp"

    # Check if fpath is configured
    if ! grep -q "~/.local/share/zsh/site-functions" ~/.zshrc 2>/dev/null; then
        print_warning "You may need to add this to your ~/.zshrc:"
        echo "  # Enable zsh completion"
        echo "  fpath=(~/.local/share/zsh/site-functions \$fpath)"
        echo "  autoload -U compinit && compinit"
        echo ""
    fi
fi

print_success "Installation complete!"
echo ""
print_status "To activate completions:"
if [ "$INSTALL_BASH" = true ]; then
    echo "  Bash: source ~/.bashrc (or restart terminal)"
fi
if [ "$INSTALL_ZSH" = true ]; then
    echo "  Zsh:  source ~/.zshrc (or restart terminal)"
fi
echo ""
print_status "🎯 Test completions:"
echo "  ohfp <TAB>                    # Show available resources"
echo "  ohfp templates <TAB>          # Show template actions"
echo "  ohfp --format <TAB>           # Show format options"
echo ""
print_status "Manual installation commands:"
if [ "$INSTALL_BASH" = true ]; then
    echo "  Bash: python src/run.py --completion bash > ~/.local/share/bash-completion/completions/ohfp"
fi
if [ "$INSTALL_ZSH" = true ]; then
    echo "  Zsh:  python src/run.py --completion zsh > ~/.local/share/zsh/site-functions/_ohfp"
fi
