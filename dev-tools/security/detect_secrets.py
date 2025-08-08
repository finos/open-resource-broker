#!/usr/bin/env python3
"""Detect potential hardcoded secrets in source code."""

import logging
import re
import sys
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def detect_secrets(source_dir: str = "src") -> bool:
    """Detect potential hardcoded secrets in Python files."""

    # Pattern to match potential secrets
    secret_pattern = re.compile(
        r'(password|secret|key|token)\s*=\s*["\'][^"\']{8,}["\']',
        re.IGNORECASE
    )

    # Exceptions - these are not real secrets
    exceptions = [
        'key="Environment"',
        'key="ManagedBy"',
        'value="default"',
        'value="HostFactory"',
        'password="test"',
        'secret="test"',
        'token="test"',
        'key="test"'
    ]

    source_path = Path(source_dir)
    if not source_path.exists():
        logger.error(f"Source directory '{source_dir}' not found")
        return False

    found_secrets = []

    for py_file in source_path.rglob("*.py"):
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()

            for line_num, line in enumerate(content.split('\n'), 1):
                matches = secret_pattern.findall(line)
                for match in matches:
                    # Check if this is an exception
                    if not any(exc in line for exc in exceptions):
                        found_secrets.append(f"{py_file}:{line_num}: {line.strip()}")

        except Exception as e:
            logger.warning(f"Could not read {py_file}: {e}")

    if found_secrets:
        logger.error("Potential hardcoded secrets found:")
        for secret in found_secrets:
            logger.error(f"  {secret}")
        return False
    else:
        logger.info("No hardcoded secrets detected")
        return True

def main():
    """Main function."""
    if not detect_secrets():
        sys.exit(1)
    sys.exit(0)

if __name__ == '__main__':
    main()
