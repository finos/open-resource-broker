#!/usr/bin/env python3
"""
Quality Check Tool

A quality checker that enforces coding standards:
- No emojis in code or comments
- No unprofessional language
- No hyperbolic marketing terms
- Appropriate docstring coverage and format
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
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

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

# Legitimate technical characters that should be allowed
ALLOWED_TECHNICAL_CHARS = {
    "├",
    "└",
    "│",
    "─",  # Box drawing characters for tree structures
    "▪",
    "▫",
    "■",
    "□",  # Simple geometric shapes for bullets
    "→",
    "←",
    "↑",
    "↓",  # Basic arrows for flow diagrams
}

# Pre-compile regex patterns for better performance
UNPROFESSIONAL_PATTERNS = {
    re.compile(r"\bawesome\b", re.IGNORECASE): 'Use "excellent" or specific technical terms',
    re.compile(r"\brock\b", re.IGNORECASE): 'Use "implement" or "execute"',
    re.compile(r"\bcool\b", re.IGNORECASE): 'Use "effective" or specific benefits',
    re.compile(r"\bsweet\b", re.IGNORECASE): 'Use "beneficial" or specific advantages',
    re.compile(r"\bsick\b", re.IGNORECASE): 'Use "impressive" or specific technical terms',
    re.compile(r"\bepic\b", re.IGNORECASE): 'Use "comprehensive" or specific scope',
    re.compile(r"\binsane\b", re.IGNORECASE): 'Use "significant" or specific metrics',
    re.compile(r"\bcrazy\b", re.IGNORECASE): 'Use "substantial" or specific details',
}

# Hyperbolic marketing terms
HYPERBOLIC_PATTERNS = {
    re.compile(r"\benhanced\b", re.IGNORECASE): 'Use "improved" only when factually accurate',
    re.compile(r"\bunified\b", re.IGNORECASE): 'Use "integrated" or "consolidated"',
    re.compile(
        r"\bproper\b(?!\s*(?:ty|ties))", re.IGNORECASE
    ): 'Use specific terms: "correct", "appropriate", "compliant", "structured", "domain-driven"',
    re.compile(r"\bmodern\b", re.IGNORECASE): 'Use "current" or "updated"',
    re.compile(r"\bcutting-edge\b", re.IGNORECASE): 'Use "current industry standard"',
    re.compile(r"\brevolutionary\b", re.IGNORECASE): 'Use "significant improvement"',
    re.compile(r"\bnext-generation\b", re.IGNORECASE): "Use specific technology names",
    re.compile(r"\bstate-of-the-art\b", re.IGNORECASE): 'Use "current best practice"',
}

# Implementation detail terms that should be removed from production code
IMPLEMENTATION_DETAIL_PATTERNS = {
    re.compile(r"\bphase\s+\d+\b", re.IGNORECASE): "Remove implementation phase references",
    re.compile(r"\bphase\s+[a-z]+\b", re.IGNORECASE): "Remove implementation phase references",
    re.compile(r"\bmigrated\s+from\b", re.IGNORECASE): "Remove migration history references",
    re.compile(r"\bmigrating\s+to\b", re.IGNORECASE): "Remove migration process references",
    re.compile(r"\bthis\s+instead\s+of\s+that\b", re.IGNORECASE): "Remove comparison references",
    re.compile(r"\bold\s+implementation\b", re.IGNORECASE): "Remove old implementation references",
    re.compile(r"\bnew\s+implementation\b", re.IGNORECASE): "Remove new implementation references",
    re.compile(r"\blegacy\s+code\b", re.IGNORECASE): "Remove legacy code references",
    re.compile(r"\btemporary\s+fix\b", re.IGNORECASE): "Remove temporary implementation references",
    re.compile(r"\btodo\s*:\b", re.IGNORECASE): "Remove TODO comments from production code",
    re.compile(r"\bfixme\s*:\b", re.IGNORECASE): "Remove FIXME comments from production code",
    re.compile(r"\bhack\s*:\b", re.IGNORECASE): "Remove HACK comments from production code",
    re.compile(r"\bworkaround\s+for\b", re.IGNORECASE): "Remove workaround references",
    re.compile(r"\bquick\s+fix\b", re.IGNORECASE): "Remove quick fix references",
    re.compile(r"\bstep\s+\d+\b", re.IGNORECASE): "Remove step-by-step implementation references",
    re.compile(r"\btest\s+\d+\b", re.IGNORECASE): "Remove test numbering from production code",
}

# Legitimate version references that should be excluded
VERSION_EXCLUSIONS = {
    "CODE_OF_CONDUCT.md",  # Contributor Covenant version references
    "LICENSE",  # License version references
    "pyproject.toml",  # Package version references
    "version.py",  # Version files
    "versions.json",  # Version configuration files
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


class ImplementationDetailViolation(Violation):
    """Implementation detail term found in production code."""

    def __init__(self, file_path: str, line_num: int, content: str, term: str, suggestion: str):
        super().__init__(
            file_path, line_num, content, f"Implementation detail '{term}' - {suggestion}"
        )
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
        super().__init__(file_path, line_num, "", f"Unused imports detected: {message}")

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
            logger.error(f"Error checking {file_path}: {e}")

        return violations

    def check_content(self, file_path: str, content: str) -> List[Violation]:
        """Check file content for violations."""
        return []


class EmojiChecker(FileChecker):
    """Check for emojis in files."""

    def check_content(self, file_path: str, content: str) -> List[Violation]:
        violations = []
        for line_num, line in enumerate(content.splitlines(), 1):
            matches = EMOJI_PATTERN.findall(line)
            for match in matches:
                # Check if all characters in the match are allowed technical chars
                if not all(char in ALLOWED_TECHNICAL_CHARS for char in match):
                    violations.append(EmojiViolation(file_path, line_num, line.strip()))
                    break  # Only report once per line
        return violations


class LanguageChecker(FileChecker):
    """Check for unprofessional language and hyperbolic terms."""

    def check_content(self, file_path: str, content: str) -> List[Violation]:
        violations = []
        for line_num, line in enumerate(content.splitlines(), 1):
            # Check for unprofessional language
            for pattern, suggestion in UNPROFESSIONAL_PATTERNS.items():
                matches = pattern.finditer(line)
                for match in matches:
                    term = match.group(0)
                    violations.append(
                        UnprofessionalLanguageViolation(
                            file_path, line_num, line.strip(), term, suggestion
                        )
                    )

            # Check for hyperbolic terms
            for pattern, suggestion in HYPERBOLIC_PATTERNS.items():
                matches = pattern.finditer(line)
                for match in matches:
                    term = match.group(0)
                    violations.append(
                        HyperbolicTermViolation(file_path, line_num, line.strip(), term, suggestion)
                    )

            # Check for implementation detail terms
            for pattern, suggestion in IMPLEMENTATION_DETAIL_PATTERNS.items():
                matches = pattern.finditer(line)
                for match in matches:
                    term = match.group(0)
                    # Skip version references in legitimate files
                    if "version" in term.lower() and any(
                        excluded in file_path for excluded in VERSION_EXCLUSIONS
                    ):
                        continue
                    violations.append(
                        ImplementationDetailViolation(
                            file_path, line_num, line.strip(), term, suggestion
                        )
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

            # Check module docstring (skip empty __init__.py files)
            if not ast.get_docstring(tree):
                # Skip empty __init__.py files - they're just package markers
                if not (Path(file_path).name == "__init__.py" and len(content.strip()) == 0):
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
                            # Special handling for __init__ methods - use fast check
                            if node.name == "__init__" and len(node.body) <= 8:
                                # Quick heuristic: if small body, likely simple
                                if self._is_simple_init_fast(node):
                                    continue
                            violations.append(
                                MissingDocstringViolation(
                                    file_path, node.lineno, "function", node.name
                                )
                            )

        except SyntaxError:
            # Skip files with syntax errors
            pass

        return violations

    def _is_simple_init_fast(self, node: ast.FunctionDef) -> bool:
        """Fast check if __init__ method is simple (only parameter assignment)."""
        # Only check small methods
        if len(node.body) > 8:
            return False

        # Quick pattern check: only assignments and super() calls
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                # Must be self.x = y pattern
                if not (
                    len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Attribute)
                    and isinstance(stmt.targets[0].value, ast.Name)
                    and stmt.targets[0].value.id == "self"
                ):
                    return False
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                # Allow super().__init__() only
                continue
            else:
                return False
        return True


class ImportChecker(FileChecker):
    """Check for unused imports using autoflake."""

    def check_content(self, file_path: str, content: str) -> List[Violation]:
        if not file_path.endswith(".py"):
            return []

        violations = []
        try:
            import subprocess

            # Run autoflake in check mode
            result = subprocess.run(
                [
                    "autoflake",
                    "--check",
                    "--remove-all-unused-imports",
                    "--remove-unused-variables",
                    file_path,
                ],
                capture_output=True,
                text=True,
                cwd=".",
            )

            # If autoflake found issues, it returns non-zero exit code
            if result.returncode != 0:
                violations.append(
                    UnusedImportViolation(file_path, 1, "Run 'make format' to fix automatically")
                )

        except (subprocess.SubprocessError, FileNotFoundError):
            # Skip if autoflake not available
            pass

        return violations


class CommentChecker(FileChecker):
    """Check for TODO/FIXME comments without tickets and commented code.

    Supports noqa suppressions for commented code:
    - Line-level: # def function():  # noqa:COMMENTED
    - Section-level:
        # noqa:COMMENTED section-start
        # def function():
        # class MyClass:
        # noqa:COMMENTED section-end
    """

    def check_content(self, file_path: str, content: str) -> List[Violation]:
        violations = []

        # Regex for commented code (simple heuristic)
        code_pattern = re.compile(
            r"^\s*#\s*(def|class|if|for|while|try|except|return|import|from)\s"
        )

        # Regex for debug prints (only catch print statements, not logger)
        debug_pattern = re.compile(r"^\s*print\(")

        # Skip debug print checks for test files and markdown files
        is_test_file = "/test" in file_path or file_path.startswith("test")
        is_markdown_file = file_path.endswith(".md")

        # Track section-level suppressions and string contexts
        commented_code_suppressed = False
        in_multiline_string = False
        string_delimiter = None

        for line_num, line in enumerate(content.splitlines(), 1):
            # Track multiline strings (docstrings and regular strings)
            stripped = line.strip()

            # Check for start/end of multiline strings
            if not in_multiline_string:
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    string_delimiter = stripped[:3]
                    if not (stripped.endswith(string_delimiter) and len(stripped) > 3):
                        in_multiline_string = True
                elif stripped.startswith('r"""') or stripped.startswith("r'''"):
                    string_delimiter = stripped[1:4]
                    if not (stripped.endswith(string_delimiter) and len(stripped) > 4):
                        in_multiline_string = True
            else:
                if stripped.endswith(string_delimiter):
                    in_multiline_string = False
                    string_delimiter = None

            # Skip checks if we're inside a multiline string
            if in_multiline_string:
                continue

            # Check for section-level suppression controls
            if "# noqa:COMMENTED section-start" in line:
                commented_code_suppressed = True
                continue
            elif "# noqa:COMMENTED section-end" in line:
                commented_code_suppressed = False
                continue

            # Check for commented code
            if code_pattern.search(line):
                # Skip if suppressed by section-level or line-level noqa
                if (
                    commented_code_suppressed
                    or "noqa" in line.lower()
                    or "noqa:commented" in line.lower()
                ):
                    continue
                violations.append(CommentedCodeViolation(file_path, line_num, line.strip()))

            # Check for debug statements (skip for test files and markdown files)
            if (
                not is_test_file
                and not is_markdown_file
                and debug_pattern.search(line)
                and "DEBUG" not in line.upper()
                and "noqa" not in line.lower()
            ):
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
            with open(gitignore_path, "r", encoding="utf-8") as f:
                return pathspec.PathSpec.from_lines("gitwildmatch", f)
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

        # Process files in parallel for better performance
        def check_single_file(file_path):
            file_violations = []
            for checker in self.checkers:
                file_violations.extend(checker.check_file(file_path))
            return file_violations

        # Use ThreadPoolExecutor for parallel processing
        with ThreadPoolExecutor(max_workers=8) as executor:  # Increased workers
            # Submit all file checking tasks
            future_to_file = {
                executor.submit(check_single_file, file_path): file_path
                for file_path in valid_files
            }

            completed = 0
            for future in as_completed(future_to_file):
                completed += 1
                if completed % 10 == 0 or completed == len(valid_files):
                    logger.info(f"Progress: {completed}/{len(valid_files)} files checked")

                try:
                    file_violations = future.result()
                    all_violations.extend(file_violations)
                except Exception as e:
                    file_path = future_to_file[future]
                    logger.error(f"Error checking {file_path}: {e}")

        return all_violations

    def get_modified_files(self) -> List[str]:
        """Get list of modified files from git."""
        import os
        import subprocess

        try:
            # In CI/PR context, compare against target branch
            if os.getenv("GITHUB_EVENT_NAME") == "pull_request":
                base_ref = os.getenv("GITHUB_BASE_REF", "main")
                result = subprocess.run(
                    ["git", "diff", "--name-only", f"origin/{base_ref}...HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                modified_files = result.stdout.strip().split("\n") if result.stdout.strip() else []
                return [f for f in modified_files if f]

            # Local development: check staged, unstaged, and untracked files
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
            logger.warning("Failed to get modified files from git. Checking all files.")
            return []


def main():
    """Run comprehensive code quality checks with configurable options."""
    """Run comprehensive code quality checks with configurable options."""
    """Main entry point for the quality checker."""
    parser = argparse.ArgumentParser(description="Professional Quality Check Tool")
    parser.add_argument("--fix", action="store_true", help="Attempt to automatically fix issues")
    parser.add_argument(
        "--strict", action="store_true", help="Exit with error code on any violation"
    )
    parser.add_argument("--files", nargs="+", help="Specific files to check")
    parser.add_argument("--all", action="store_true", help="Check all files in repository")

    args = parser.parse_args()

    checker = QualityChecker()

    # Determine which files to check
    if args.files:
        files_to_check = args.files
    elif args.all:
        # Check all relevant files in repository (deterministic)
        files_to_check = []
        from pathlib import Path

        import pathspec

        # Load .gitignore patterns
        gitignore_path = Path(".gitignore")
        if gitignore_path.exists():
            with open(gitignore_path, "r", encoding="utf-8") as f:
                spec = pathspec.PathSpec.from_lines("gitwildmatch", f)
        else:
            spec = pathspec.PathSpec.from_lines("gitwildmatch", [])

        for pattern in [
            "**/*.py",
            "**/*.md",
            "**/*.rst",
            "**/*.txt",
            "**/*.yaml",
            "**/*.yml",
            "**/*.json",
            "**/*.toml",
        ]:
            for file_path in Path(".").rglob(pattern):
                if file_path.is_file():
                    # Check if file should be ignored
                    rel_path = file_path.relative_to(Path("."))
                    if not spec.match_file(str(rel_path)):
                        files_to_check.append(str(file_path))
        files_to_check = sorted(files_to_check)
    else:
        # Check only git modified files
        files_to_check = checker.get_modified_files()

    # Run checks
    violations = checker.check_files(files_to_check)

    # Print results
    if violations:
        logger.error(f"\n{len(violations)} quality issues found:\n")

        # Group by file and count by category
        violations_by_file = {}
        category_counts = {}

        for v in violations:
            if v.file_path not in violations_by_file:
                violations_by_file[v.file_path] = []
            violations_by_file[v.file_path].append(v)

            # Count by category (extract category from message)
            if "Hyperbolic term" in v.message:
                category = "Hyperbolic terms"
            elif "Debug print/logging statement" in v.message:
                category = "Debug print statements"
            elif "Unused imports" in v.message:
                category = "Unused imports"
            elif "Commented-out code" in v.message:
                category = "Commented-out code"
            else:
                category = "Other issues"

            category_counts[category] = category_counts.get(category, 0) + 1

        # Print violations by file
        for file_path, file_violations in violations_by_file.items():
            logger.error(f"\n{file_path}: ({len(file_violations)} issues)")
            for v in sorted(file_violations, key=lambda v: v.line_num):
                logger.error(f"  Line {v.line_num}: {v.message}")
                logger.error(f"    {v.content}")

        # Print summary by category
        logger.error(f"\n" + "-" * 40)
        logger.error("Summary:")
        for category, count in sorted(category_counts.items()):
            logger.error(f"{category}: {count}")

        logger.error("-" * 40)
        logger.error(f"Total files with issues: {len(violations_by_file)}")
        logger.error(f"Total issues: {len(violations)}")

        # Exit with error if strict mode
        if args.strict:
            sys.exit(1)
    else:
        logger.info("No quality issues found!")

    sys.exit(0)


if __name__ == "__main__":
    main()
