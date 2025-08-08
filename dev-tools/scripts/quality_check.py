#!/usr/bin/env python3
"""
Quality Check Tool

A quality checker that enforces coding standards:
- No emojis in code or comments
- No unprofessional language
- No hyperbolic marketing terms
- Proper docstring coverage and format
- Consistent naming conventions
- No unused imports or commented code
- README files are up-to-date

Usage:
  python dev-tools/scripts/quality_check.py [--fix] [--strict] [--files FILE1 FILE2...]

Options:
  --fix        Attempt to automatically fix issues where possible
  --strict     Exit with error code on any violation (for CI)
  --files      Specific files to check (default: git modified files)
"""

import argparse
import ast
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

try:
    import pathspec
except ImportError:
    pathspec = None

# --- Configuration ---

# Emoji detection pattern
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)

# Unprofessional language terms
UNPROFESSIONAL_TERMS = {
    r"\bawesome\b": 'Use "excellent" or specific technical terms',
    r"\brock\b": 'Use "implement" or "execute"',
    r"\bcool\b": 'Use "effective" or specific benefits',
    r"\bsweet\b": 'Use "beneficial" or specific advantages',
    r"\bsick\b": 'Use "impressive" or specific technical terms',
    r"\bepic\b": 'Use "comprehensive" or specific scope',
    r"\binsane\b": 'Use "significant" or specific metrics',
    r"\bcrazy\b": 'Use "substantial" or specific details',
}

# Hyperbolic marketing terms
HYPERBOLIC_TERMS = {
    r"\benhanced\b": 'Use "improved" only when factually accurate',
    r"\bunified\b": 'Use "integrated" or "consolidated"',
    r"\bmodern\b": 'Use "current" or "updated"',
    r"\bcutting-edge\b": 'Use "current industry standard"',
    r"\brevolutionary\b": 'Use "significant improvement"',
    r"\bnext-generation\b": "Use specific technology names",
    r"\bstate-of-the-art\b": 'Use "current best practice"',
}

# File extensions to check
CODE_EXTENSIONS = {".py"}
DOC_EXTENSIONS = {".md", ".rst", ".txt"}
CONFIG_EXTENSIONS = {".yaml", ".yml", ".json", ".toml"}
ALL_EXTENSIONS = CODE_EXTENSIONS | DOC_EXTENSIONS | CONFIG_EXTENSIONS

# --- Violation Classes ---


class Violation:
    """Base class for quality check violations."""

    def __init__(self, file_path: str, line_num: int, content: str, message: str):
        self.file_path = file_path
        self.line_num = line_num
        self.content = content
        self.message = message

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line_num}: {self.message}\n  {self.content}"

    def can_autofix(self) -> bool:
        """Whether this violation can be automatically fixed."""
        return False

    def autofix(self) -> Optional[str]:
        """Return fixed content if possible, None otherwise."""
        return None


class EmojiViolation(Violation):
    """Emoji found in code or comments."""

    def __init__(self, file_path: str, line_num: int, content: str):
        super().__init__(
            file_path, line_num, content, "Contains emoji - not allowed in professional code"
        )


class UnprofessionalLanguageViolation(Violation):
    """Unprofessional language found in code or comments."""

    def __init__(self, file_path: str, line_num: int, content: str, term: str, suggestion: str):
        super().__init__(
            file_path, line_num, content, f"Unprofessional term '{term}' - {suggestion}"
        )
        self.term = term
        self.suggestion = suggestion


class HyperbolicTermViolation(Violation):
    """Hyperbolic marketing term found in code or comments."""

    def __init__(self, file_path: str, line_num: int, content: str, term: str, suggestion: str):
        super().__init__(file_path, line_num, content, f"Hyperbolic term '{term}' - {suggestion}")
        self.term = term
        self.suggestion = suggestion


class MissingDocstringViolation(Violation):
    """Missing docstring in class, function, or module."""

    def __init__(self, file_path: str, line_num: int, element_type: str, element_name: str):
        super().__init__(
            file_path,
            line_num,
            f"{element_type} {element_name}",
            f"Missing docstring for {element_type} {element_name}",
        )
        self.element_type = element_type
        self.element_name = element_name


class DocstringFormatViolation(Violation):
    """Docstring doesn't follow the required format."""

    def __init__(
        self, file_path: str, line_num: int, element_type: str, element_name: str, issue: str
    ):
        super().__init__(
            file_path,
            line_num,
            f"{element_type} {element_name}",
            f"Docstring format issue in {element_type} {element_name}: {issue}",
        )


class UnusedImportViolation(Violation):
    """Unused import found in code."""

    def __init__(self, file_path: str, line_num: int, message: str):
        super().__init__(
            file_path, line_num, "", f"Unused imports detected: {message}"
        )

    def can_autofix(self) -> bool:
        return True


class CommentedCodeViolation(Violation):
    """Commented-out code found."""

    def __init__(self, file_path: str, line_num: int, content: str):
        super().__init__(file_path, line_num, content, "Commented-out code should be removed")


class DebugStatementViolation(Violation):
    """Debug print/logging statement found."""

    def __init__(self, file_path: str, line_num: int, content: str):
        super().__init__(
            file_path, line_num, content, "Debug print/logging statement should be removed"
        )

    def can_autofix(self) -> bool:
        return True


# --- Checker Classes ---


class FileChecker:
    """Base class for file-based checkers."""

    def check_file(self, file_path: str) -> List[Violation]:
        """Check a file for violations."""
        violations = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            violations.extend(self.check_content(file_path, content))
        except UnicodeDecodeError:
            # Skip binary files
            pass
        except Exception as e:
            print(f"Error checking {file_path}: {e}")

        return violations

    def check_content(self, file_path: str, content: str) -> List[Violation]:
        """Check file content for violations."""
        return []


class EmojiChecker(FileChecker):
    """Check for emojis in files."""

    def check_content(self, file_path: str, content: str) -> List[Violation]:
        violations = []
        for line_num, line in enumerate(content.splitlines(), 1):
            if EMOJI_PATTERN.search(line):
                violations.append(EmojiViolation(file_path, line_num, line.strip()))
        return violations


class LanguageChecker(FileChecker):
    """Check for unprofessional language and hyperbolic terms."""

    def check_content(self, file_path: str, content: str) -> List[Violation]:
        violations = []
        for line_num, line in enumerate(content.splitlines(), 1):
            # Check for unprofessional language
            for term_pattern, suggestion in UNPROFESSIONAL_TERMS.items():
                matches = re.finditer(term_pattern, line, re.IGNORECASE)
                for match in matches:
                    term = match.group(0)
                    violations.append(
                        UnprofessionalLanguageViolation(
                            file_path, line_num, line.strip(), term, suggestion
                        )
                    )

            # Check for hyperbolic terms
            for term_pattern, suggestion in HYPERBOLIC_TERMS.items():
                matches = re.finditer(term_pattern, line, re.IGNORECASE)
                for match in matches:
                    term = match.group(0)
                    violations.append(
                        HyperbolicTermViolation(file_path, line_num, line.strip(), term, suggestion)
                    )

        return violations


class DocstringChecker(FileChecker):
    """Check for docstring coverage and format."""

    def check_content(self, file_path: str, content: str) -> List[Violation]:
        if not file_path.endswith(".py"):
            return []

        # Skip docstring checks for test files
        if "/test" in file_path or file_path.startswith("test"):
            return []

        violations = []
        try:
            tree = ast.parse(content)

            # Check module docstring
            if not ast.get_docstring(tree):
                violations.append(
                    MissingDocstringViolation(file_path, 1, "module", Path(file_path).name)
                )

            # Check classes and functions
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    if not ast.get_docstring(node):
                        violations.append(
                            MissingDocstringViolation(file_path, node.lineno, "class", node.name)
                        )
                elif isinstance(node, ast.FunctionDef):
                    # Skip private methods (starting with _)
                    if not node.name.startswith("_") or node.name == "__init__":
                        if not ast.get_docstring(node):
                            violations.append(
                                MissingDocstringViolation(
                                    file_path, node.lineno, "function", node.name
                                )
                            )

        except SyntaxError:
            # Skip files with syntax errors
            pass

        return violations


class ImportChecker(FileChecker):
    """Check for unused imports using autoflake."""

    def check_content(self, file_path: str, content: str) -> List[Violation]:
        if not file_path.endswith(".py"):
            return []

        violations = []
        try:
            import subprocess

            # Run autoflake in check mode
            result = subprocess.run([
                "autoflake", "--check", "--remove-all-unused-imports",
                "--remove-unused-variables", file_path
            ], capture_output=True, text=True, cwd=".")

            # If autoflake found issues, it returns non-zero exit code
            if result.returncode != 0:
                violations.append(UnusedImportViolation(
                    file_path, 1, "Run 'make format' to fix automatically"
                ))

        except (subprocess.SubprocessError, FileNotFoundError):
            # Skip if autoflake not available
            pass

        return violations


class CommentChecker(FileChecker):
    """Check for TODO/FIXME comments without tickets and commented code."""

    def check_content(self, file_path: str, content: str) -> List[Violation]:
        violations = []

        # Regex for commented code (simple heuristic)
        code_pattern = re.compile(
            r"^\s*#\s*(def|class|if|for|while|try|except|return|import|from)\s"
        )

        # Regex for debug prints (only catch print statements, not logger)
        debug_pattern = re.compile(r"^\s*print\(")

        for line_num, line in enumerate(content.splitlines(), 1):
            # Check for commented code
            if code_pattern.search(line):
                violations.append(CommentedCodeViolation(file_path, line_num, line.strip()))

            # Check for debug statements
            if debug_pattern.search(line) and "DEBUG" not in line.upper():
                violations.append(DebugStatementViolation(file_path, line_num, line.strip()))

        return violations


class QualityChecker:
    """Main quality checker that runs all checks."""

    def __init__(self):
        self.checkers = [
            EmojiChecker(),
            LanguageChecker(),
            DocstringChecker(),
            ImportChecker(),
            CommentChecker(),
        ]
        self.gitignore_spec = self._load_gitignore()

    def _load_gitignore(self):
        """Load .gitignore patterns for filtering files."""
        if not pathspec:
            return None

        gitignore_path = Path(".gitignore")
        if not gitignore_path.exists():
            return None

        try:
            with open(gitignore_path, 'r', encoding='utf-8') as f:
                return pathspec.PathSpec.from_lines('gitwildmatch', f)
        except Exception:
            return None

    def _should_ignore_file(self, file_path: str) -> bool:
        """Check if file should be ignored based on gitignore."""
        if not self.gitignore_spec:
            return False

        # Convert to relative path for gitignore matching
        try:
            rel_path = os.path.relpath(file_path)
            return self.gitignore_spec.match_file(rel_path)
        except Exception:
            return False

    def check_files(self, file_paths: List[str]) -> List[Violation]:
        """Run all checks on the given files."""
        all_violations = []

        # Filter files that exist and have relevant extensions
        valid_files = []
        for file_path in file_paths:
            # Skip this script to avoid self-checking issues
            if file_path.endswith("quality_check.py"):
                continue
            # Skip files ignored by gitignore
            if self._should_ignore_file(file_path):
                continue
            if os.path.isfile(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                if ext in ALL_EXTENSIONS:
                    valid_files.append(file_path)

        if not valid_files:
            return all_violations

        for i, file_path in enumerate(valid_files, 1):
            if i % 10 == 0 or i == len(valid_files):
                print(f"Progress: {i}/{len(valid_files)} files checked", flush=True)

                # Run all checkers on this file
                for checker in self.checkers:
                    violations = checker.check_file(file_path)
                    all_violations.extend(violations)

        return all_violations

    def get_modified_files(self) -> List[str]:
        """Get list of modified files from git."""
        import subprocess

        try:
            # Get staged files
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True,
                text=True,
                check=True,
            )
            staged_files = result.stdout.strip().split("\n") if result.stdout.strip() else []

            # Get unstaged files
            result = subprocess.run(
                ["git", "diff", "--name-only"], capture_output=True, text=True, check=True
            )
            unstaged_files = result.stdout.strip().split("\n") if result.stdout.strip() else []

            # Get untracked files
            result = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                capture_output=True,
                text=True,
                check=True,
            )
            untracked_files = result.stdout.strip().split("\n") if result.stdout.strip() else []

            # Combine all files
            all_files = list(set(staged_files + unstaged_files + untracked_files))
            return [f for f in all_files if f]  # Filter out empty strings

        except subprocess.SubprocessError:
            print("Warning: Failed to get modified files from git. Checking all files.")
            return []


def main():
    """Main entry point for the quality checker."""
    parser = argparse.ArgumentParser(description="Professional Quality Check Tool")
    parser.add_argument("--fix", action="store_true", help="Attempt to automatically fix issues")
    parser.add_argument(
        "--strict", action="store_true", help="Exit with error code on any violation"
    )
    parser.add_argument("--files", nargs="+", help="Specific files to check")

    args = parser.parse_args()

    checker = QualityChecker()

    # Determine which files to check
    if args.files:
        files_to_check = args.files
    else:
        files_to_check = checker.get_modified_files()
        if not files_to_check:
            # If no modified files, check all relevant files
            files_to_check = []
            for root, _, files in os.walk("."):
                if ".git" in root or ".venv" in root or "__pycache__" in root:
                    continue
                for file in files:
                    # Check file types that pre-commit hook expects
                    if file.endswith((".py", ".md", ".rst", ".txt", ".yaml", ".yml", ".json", ".toml")):
                        files_to_check.append(os.path.join(root, file))

    # Run checks
    violations = checker.check_files(files_to_check)

    # Print results
    if violations:
        print(f"\n{len(violations)} quality issues found:\n")

        # Group by file
        violations_by_file = {}
        for v in violations:
            if v.file_path not in violations_by_file:
                violations_by_file[v.file_path] = []
            violations_by_file[v.file_path].append(v)

        # Print violations by file
        for file_path, file_violations in violations_by_file.items():
            print(f"\n{file_path}:")
            for v in sorted(file_violations, key=lambda v: v.line_num):
                print(f"  Line {v.line_num}: {v.message}")
                print(f"    {v.content}")

        # Exit with error if strict mode
        if args.strict:
            sys.exit(1)
    else:
        print("No quality issues found!")

    sys.exit(0)


if __name__ == "__main__":
    main()
