#!/usr/bin/env python3
"""Validate GitHub Actions workflow YAML files."""

import sys
import yaml
from pathlib import Path

def validate_workflow(file_path: Path):
    """Validate a single workflow file."""
    try:
        with open(file_path, 'r') as f:
            yaml.safe_load(f)
        print(f"VALID: {file_path.name}")
        return True
    except yaml.YAMLError as e:
        print(f"INVALID: {file_path.name} - {e}")
        return False
    except Exception as e:
        print(f"ERROR: {file_path.name} - {e}")
        return False

def main():
    """Main validation function."""
    workflows_dir = Path('.github/workflows')
    
    if not workflows_dir.exists():
        print("ERROR: .github/workflows directory not found")
        sys.exit(1)
    
    workflow_files = list(workflows_dir.glob('*.yml')) + list(workflows_dir.glob('*.yaml'))
    
    if not workflow_files:
        print("ERROR: No workflow files found")
        sys.exit(1)
    
    all_valid = True
    
    for workflow_file in sorted(workflow_files):
        if not validate_workflow(workflow_file):
            all_valid = False
    
    if all_valid:
        print(f"SUCCESS: All {len(workflow_files)} workflow files are valid")
        sys.exit(0)
    else:
        print("FAILED: Found invalid workflow files")
        sys.exit(1)

if __name__ == '__main__':
    main()
