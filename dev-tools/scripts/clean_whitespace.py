#!/usr/bin/env python3
"""Clean whitespace in blank lines from files."""

import argparse
import re
import sys
from pathlib import Path

def clean_whitespace_in_file(file_path):
    """Clean whitespace in blank lines from a single file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Replace lines with only whitespace with empty lines
        cleaned_content = re.sub(r'^[ \t]+$', '', content, flags=re.MULTILINE)

        if content != cleaned_content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(cleaned_content)
            return True
        return False
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Clean whitespace in blank lines from files")
    parser.add_argument('patterns', nargs='*', default=['*.py', '*.md', '*.yml', '*.yaml', '*.json', '*.txt', '*.sh'],
                       help='File patterns to process (default: common file types)')
    args = parser.parse_args()

    files = []
    for pattern in args.patterns:
        if Path(pattern).is_file():
            files.append(Path(pattern))
        else:
            files.extend(Path('.').rglob(pattern))

    if not files:
        print("No files found to process")
        return 0

    print(f"Processing {len(files)} files...", end="", flush=True)

    changed_files = []
    for i, file_path in enumerate(files):
        if i % 10 == 0:  # Progress every 10 files
            print(".", end="", flush=True)

        if clean_whitespace_in_file(file_path):
            changed_files.append(file_path)

    print(f" done!")

    if changed_files:
        print(f"Cleaned whitespace in {len(changed_files)} files")
    else:
        print("No files needed whitespace cleaning")

    return 0

if __name__ == '__main__':
    sys.exit(main())
