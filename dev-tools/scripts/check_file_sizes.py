#!/usr/bin/env python3
"""
File size checker for maintaining code quality.

This script checks for files that are getting too large and warns about them.
Large files often indicate violations of Single Responsibility Principle.
"""
import sys
import argparse
from pathlib import Path
from typing import List, Tuple


def check_large_files(warn_only: bool = False, threshold: int = 600) -> None:
    """
    Check for files that are getting too large.
    
    Args:
        warn_only: If True, only warn without failing
        threshold: Line count threshold for warnings
    """
    large_files = []
    
    # Check Python files in src directory
    for file_path in Path("src").rglob("*.py"):
        try:
            line_count = len(file_path.read_text(encoding='utf-8').splitlines())
            if line_count > threshold:
                large_files.append((file_path, line_count))
        except Exception as e:
            print(f"Warning: Could not analyze {file_path}: {e}")
    
    if large_files:
        print("WARNING: Large files detected:")
        print("=" * 50)
        
        # Sort by line count (largest first)
        large_files.sort(key=lambda x: x[1], reverse=True)
        
        for file_path, lines in large_files:
            print(f"  {file_path}: {lines} lines")
        
        print("=" * 50)
        print(f"Consider splitting files larger than {threshold} lines for better maintainability.")
        print("Large files often indicate Single Responsibility Principle violations.")
        
        if not warn_only:
            print("FAILURE: Build failed due to large files.")
            sys.exit(1)
        else:
            print("Build continues with warnings.")
    else:
        print(f"SUCCESS: All files are appropriately sized (< {threshold} lines).")


def get_file_size_report() -> List[Tuple[str, int]]:
    """Get a report of all Python file sizes."""
    file_sizes = []
    
    for file_path in Path("src").rglob("*.py"):
        try:
            line_count = len(file_path.read_text(encoding='utf-8').splitlines())
            file_sizes.append((str(file_path), line_count))
        except Exception:
            continue
    
    return sorted(file_sizes, key=lambda x: x[1], reverse=True)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Check for large files that may violate Single Responsibility Principle"
    )
    parser.add_argument(
        "--warn-only", 
        action="store_true",
        help="Only warn, don't fail the build"
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=600,
        help="Line count threshold for warnings (default: 600)"
    )
    parser.add_argument(
        "--report",
        action="store_true", 
        help="Generate a full file size report"
    )
    
    args = parser.parse_args()
    
    if args.report:
        print("FILE SIZE REPORT:")
        print("=" * 50)
        for file_path, lines in get_file_size_report()[:20]:  # Top 20
            print(f"  {file_path}: {lines} lines")
        print("=" * 50)
    else:
        check_large_files(args.warn_only, args.threshold)


if __name__ == "__main__":
    main()
