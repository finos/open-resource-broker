#!/usr/bin/env python3
"""Clean whitespace from Python files."""

import os
import sys
from pathlib import Path
import pathspec

def clean_file(file_path: Path) -> bool:
    """Clean whitespace from a single file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        original_content = content

        # Remove trailing whitespace from each line
        lines = content.splitlines()
        cleaned_lines = [line.rstrip() for line in lines]

        # Ensure file ends with single newline
        if cleaned_lines and cleaned_lines[-1]:
            cleaned_lines.append('')

        cleaned_content = '\n'.join(cleaned_lines)

        if cleaned_content != original_content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(cleaned_content)
            return True

        return False
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return False

def load_gitignore_spec(root_dir: Path) -> pathspec.PathSpec:
    """Load .gitignore patterns."""
    gitignore_path = root_dir / '.gitignore'
    if gitignore_path.exists():
        with open(gitignore_path, 'r', encoding='utf-8') as f:
            return pathspec.PathSpec.from_lines('gitwildmatch', f)
    return pathspec.PathSpec.from_lines('gitwildmatch', [])

def find_python_files(root_dir: Path) -> list[Path]:
    """Find all Python files in the directory, respecting .gitignore."""
    spec = load_gitignore_spec(root_dir)
    python_files = []

    for pattern in ['**/*.py', '**/*.pyi']:
        for file_path in root_dir.glob(pattern):
            if file_path.is_file():
                # Get relative path for gitignore matching
                rel_path = file_path.relative_to(root_dir)
                if not spec.match_file(str(rel_path)):
                    python_files.append(file_path)

    return python_files

def main():
    """Main function."""
    # Navigate to project root from dev-tools/scripts/
    root_dir = Path(__file__).parent.parent.parent
    python_files = find_python_files(root_dir)

    if not python_files:
        print("No Python files found.")
        return

    modified_count = 0
    total_files = len(python_files)

    print(f"Processing {total_files} Python files...")

    for i, file_path in enumerate(python_files, 1):
        if clean_file(file_path):
            modified_count += 1

        # Simple progress without tqdm to avoid context overflow
        if i % 50 == 0 or i == total_files:
            print(f"Progress: {i}/{total_files} files processed")

    print(f"Completed: {total_files} files processed, {modified_count} files modified.")

if __name__ == "__main__":
    main()
