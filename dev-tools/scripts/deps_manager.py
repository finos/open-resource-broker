#!/usr/bin/env python3
"""Dependency management script."""

import sys
import subprocess
from typing import List, Optional


def run_command(cmd: List[str]) -> int:
    """Run command and return exit code."""
    try:
        result = subprocess.run(cmd, check=True)
        return result.returncode
    except subprocess.CalledProcessError as e:
        return e.returncode
    except FileNotFoundError:
        print(f"Error: Command not found: {cmd[0]}")
        return 1


def add_dependency(package: str, dev: bool = False) -> int:
    """Add a dependency using uv."""
    if not package:
        print("Error: Package name is required")
        print(f"Usage: {sys.argv[0]} add [--dev] PACKAGE_NAME")
        return 1
    
    cmd = ["uv", "add"]
    if dev:
        cmd.append("--dev")
    cmd.append(package)
    
    print(f"Adding {'dev ' if dev else ''}dependency: {package}")
    return run_command(cmd)


def main() -> int:
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Manage project dependencies")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Add command
    add_parser = subparsers.add_parser("add", help="Add a dependency")
    add_parser.add_argument("package", help="Package name to add")
    add_parser.add_argument("--dev", action="store_true", help="Add as dev dependency")
    
    args = parser.parse_args()
    
    if args.command == "add":
        return add_dependency(args.package, args.dev)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
