#!/usr/bin/env python3
"""Check a license-scan report against the FINOS runtime license allowlist.

Accepts the JSON produced by either `pip-licenses --format=json --from=mixed`
(a JSON array of `{"Name", "Version", "License"}` objects) or
`license-checker-rseidelsohn --json` (a JSON object keyed by `name@version`
with a `licenses` field). The format is auto-detected from the JSON's
top-level shape (list vs. object).

Allowlist: FINOS license categories A + B, plus MIT-0.
https://community.finos.org/docs/governance/software-projects/license-categories/
This is the single source of truth for the runtime license gate. The
dependency-review `allow-licenses` input in
`.github/workflows/security-code.yml` cannot call out to this script, so it
must be kept in sync with the allowlist below by hand.

Before either stage, known non-SPDX spellings of the same license (e.g. the
several "...BSD..." classifier strings packaging tools emit instead of
"BSD-3-Clause"/"BSD-2-Clause") are normalised to their SPDX id; see
`ALIAS_TO_SPDX`.

Each package's license string is checked in two stages:

1. Flat check: split on "; " and require every piece to be an exact
   (case-insensitive) match in the allowlist. This handles bare SPDX
   identifiers, pip-licenses classifier strings — including the ones that
   happen to contain parentheses, e.g. "ISC License (ISCL)" — and
   classifier sets joined with "; ".
2. If the flat check fails, the string is parsed as an SPDX license
   expression (AND / OR / WITH / parentheses) and evaluated against the
   same allowlist: AND requires every operand to be allowed, OR requires
   any operand to be allowed, and a `WITH` exception is allowed only if
   the full "<license> WITH <exception>" string is itself a literal
   allowlist entry. Any string that fails to parse as an expression is
   treated as not allowed (fail closed).

UNKNOWN licenses, and any license that fails both stages, are reported as
violations.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# FINOS license categories: Category A (always allowed) + Category B
# (allowed as a package-managed dependency) + MIT-0 (ASF Category A; needed
# because cffi, a runtime dependency via cryptography, is MIT-0).
# https://community.finos.org/docs/governance/software-projects/license-categories/
# ---------------------------------------------------------------------------
SPDX_ALLOW = {
    # Category A
    "Apache-1.1",
    "Apache-2.0",
    "0BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "MIT",
    "MS-PL",
    "PHP-3.01",
    "PostgreSQL",
    "Python-2.0",
    "PSF-2.0",
    "Unlicense",
    "X11",
    "Zlib",
    "zlib-acknowledgement",
    "BSL-1.0",
    "ICU",
    "NCSA",
    "ZPL-2.0",
    "W3C",
    "WTFPL",
    "Unicode-DFS-2016",
    "DOC",
    "APAFML",
    "Bitstream-Vera",
    "OGL-UK-3.0",
    "Xnet",
    # Category B
    "MPL-1.0",
    "MPL-1.1",
    "MPL-2.0",
    "EPL-1.0",
    "CDDL-1.0",
    "CDDL-1.1",
    "CPL-1.0",
    "IPL-1.0",
    "ErlPL-1.1",
    "OFL-1.1",
    "Ruby",
    "SPL-1.0",
    "IPA",
    "Ubuntu-font-1.0",
    "CC0-1.0",
    # MIT-0
    "MIT-0",
}

# pip-licenses falls back to classifier-derived display strings when a
# package has no machine-readable License-Expression metadata. These are
# the classifier spellings seen in practice for packages whose actual
# license is one of the SPDX ids above.
CLASSIFIER_ALLOW = {
    "MIT License",
    "Apache Software License",
    "Apache License 2.0",
    "BSD License",
    "Python Software Foundation License",
    "ISC License (ISCL)",
    "Mozilla Public License 2.0 (MPL 2.0)",
    "MIT No Attribution License (MIT-0)",
    "MIT No Attribution",
    "The Unlicense (Unlicense)",
}

ALLOW = {name.lower() for name in SPDX_ALLOW | CLASSIFIER_ALLOW}

# Scanners (pip-licenses, license-checker-rseidelsohn) surface a package's
# license under whatever display name its own packaging metadata uses, and
# BSD in particular has no single canonical spelling in the wild. These are
# known non-SPDX spellings that are the *same* license as the SPDX id they
# map to, normalised (case-insensitively) to the SPDX id before evaluation
# so they resolve through the one allowlist above instead of needing a
# second, duplicate allowlist.
ALIAS_TO_SPDX = {
    "3-clause bsd license": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "bsd-3-clause license": "BSD-3-Clause",
    "bsd 3-clause license": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "modified bsd license": "BSD-3-Clause",
    "revised bsd license": "BSD-3-Clause",
    "2-clause bsd license": "BSD-2-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "simplified bsd license": "BSD-2-Clause",
    "freebsd license": "BSD-2-Clause",
}

_TOKEN_RE = re.compile(r"\(|\)|\bAND\b|\bOR\b|\bWITH\b|[^\s()]+")


def _normalise_aliases(license_str: str) -> str:
    """Rewrite known non-SPDX BSD spellings to their SPDX id, case-insensitively.

    Applied once, up front, before either evaluation stage runs. Splits on
    "; " the same way stage 1 does, so a bare alias (e.g. "3-Clause BSD
    License") and one embedded in a "; "-joined classifier set (e.g.
    "Apache Software License; 3-Clause BSD License") are both normalised
    the same way. Pieces that aren't a known alias (including SPDX
    expressions, which never match an alias key) are passed through
    unchanged for stage 2 to parse.
    """
    pieces = license_str.split("; ")
    normalised = [ALIAS_TO_SPDX.get(piece.strip().lower(), piece) for piece in pieces]
    return "; ".join(normalised)


class LicenseExpressionError(ValueError):
    """Raised when a license string cannot be parsed as an SPDX expression."""


def _tokenize(expression: str) -> list[str]:
    return _TOKEN_RE.findall(expression)


class _Parser:
    """Minimal recursive-descent parser for SPDX license expressions.

    Grammar (standard SPDX precedence: WITH > AND > OR):
        or_expr   := and_expr ("OR" and_expr)*
        and_expr  := with_expr ("AND" with_expr)*
        with_expr := atom ("WITH" IDENT)?
        atom      := "(" or_expr ")" | IDENT
    """

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _advance(self) -> str:
        token = self._peek()
        if token is None:
            raise LicenseExpressionError("unexpected end of expression")
        self.pos += 1
        return token

    def parse(self) -> tuple:
        node = self._parse_or()
        if self._peek() is not None:
            raise LicenseExpressionError(f"unexpected trailing token {self._peek()!r}")
        return node

    def _parse_or(self) -> tuple:
        left = self._parse_and()
        while self._peek() == "OR":
            self._advance()
            left = ("OR", left, self._parse_and())
        return left

    def _parse_and(self) -> tuple:
        left = self._parse_with()
        while self._peek() == "AND":
            self._advance()
            left = ("AND", left, self._parse_with())
        return left

    def _parse_with(self) -> tuple:
        left = self._parse_atom()
        if self._peek() == "WITH":
            self._advance()
            exception = self._advance()
            if exception in ("(", ")", "AND", "OR", "WITH"):
                raise LicenseExpressionError("expected an exception identifier after WITH")
            return ("WITH", left, exception)
        return left

    def _parse_atom(self) -> tuple:
        token = self._advance()
        if token == "(":
            node = self._parse_or()
            if self._advance() != ")":
                raise LicenseExpressionError("expected a closing parenthesis")
            return node
        if token in ("AND", "OR", "WITH", ")"):
            raise LicenseExpressionError(f"unexpected token {token!r}")
        return ("ID", token)


def _evaluate(node: tuple) -> bool:
    kind = node[0]
    if kind == "ID":
        return node[1].lower() in ALLOW
    if kind == "AND":
        return _evaluate(node[1]) and _evaluate(node[2])
    if kind == "OR":
        return _evaluate(node[1]) or _evaluate(node[2])
    if kind == "WITH":
        license_node, exception_id = node[1], node[2]
        if license_node[0] != "ID":
            return False
        combined = f"{license_node[1]} WITH {exception_id}"
        return combined.lower() in ALLOW
    raise LicenseExpressionError(f"unknown expression node {kind!r}")  # pragma: no cover


def _evaluate_as_expression(license_str: str) -> bool:
    node = _Parser(_tokenize(license_str)).parse()
    return _evaluate(node)


def is_allowed(license_str: str | None) -> bool:
    """Return True if every part of a license string is on the allowlist."""
    if not license_str:
        return False

    license_str = _normalise_aliases(license_str)

    # Stage 1: flat classifier-set check. Handles bare SPDX ids, classifier
    # strings (even the ones that contain parentheses), and "; "-joined
    # classifier sets, without needing to parse them as an expression.
    pieces = [piece.strip() for piece in license_str.split("; ") if piece.strip()]
    if pieces and all(piece.lower() in ALLOW for piece in pieces):
        return True

    # Stage 2: SPDX expression evaluation (AND / OR / WITH / parentheses).
    # Any parse failure is treated as not allowed (fail closed) rather than
    # raised, since plenty of non-SPDX classifier strings reach this stage.
    try:
        return _evaluate_as_expression(license_str)
    except LicenseExpressionError:
        return False


# ---------------------------------------------------------------------------
# Input parsing: pip-licenses (JSON array) vs. license-checker-rseidelsohn
# (JSON object keyed by "name@version", or "@scope/name@version").
# ---------------------------------------------------------------------------


def _npm_package_name(key: str) -> str:
    """Strip the trailing @version from a license-checker-rseidelsohn key."""
    index = key.rfind("@")
    return key[:index] if index > 0 else key


def iter_packages(data: object) -> list[tuple[str, str | None]]:
    """Return (package_name, license_string) pairs, auto-detecting the format."""
    if isinstance(data, list):
        return [(entry.get("Name", "unknown"), entry.get("License")) for entry in data]
    if isinstance(data, dict):
        return [(_npm_package_name(key), info.get("licenses")) for key, info in data.items()]
    raise ValueError(f"unrecognised license report format: {type(data).__name__}")


def check(data: object, *, ignore_packages: set[str]) -> tuple[int, list[tuple[str, str | None]]]:
    """Return (checked_count, violations) for the given license report."""
    checked = 0
    violations = []
    for name, license_str in iter_packages(data):
        if name in ignore_packages:
            continue
        checked += 1
        if not is_allowed(license_str):
            violations.append((name, license_str))
    return checked, violations


_SELF_TEST_CASES = (
    ("MIT AND PSF-2.0", True),
    ("MIT AND GPL-3.0-or-later", False),
    ("(MIT OR CC0-1.0)", True),
    ("GPL-2.0-only OR MIT", True),
    ("GNU Lesser General Public License v2 or later (LGPLv2+)", False),
    ("UNKNOWN", False),
    ("Apache Software License; BSD License", True),
    ("3-Clause BSD License", True),
    ("New BSD License", True),
    ("Simplified BSD License", True),
    ("Apache Software License; 3-Clause BSD License", True),
    ("LGPL-2.1-or-later", False),
)


def run_self_test() -> int:
    """Run the built-in self-test suite; return 0 if every case passes."""
    failures = []
    for license_str, expected in _SELF_TEST_CASES:
        actual = is_allowed(license_str)
        status = "ok" if actual == expected else "FAIL"
        print(f"[{status}] is_allowed({license_str!r}) == {actual} (expected {expected})")
        if actual != expected:
            failures.append(license_str)

    if failures:
        print(f"{len(failures)} self-test case(s) failed.", file=sys.stderr)
        return 1
    print("All self-test cases passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "report",
        nargs="?",
        type=Path,
        help="Path to the license JSON report (default: read from stdin)",
    )
    parser.add_argument(
        "--ignore-package",
        action="append",
        default=[],
        dest="ignore_packages",
        help="Package name to exclude from the check (repeatable)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run the built-in self-test suite and exit",
    )
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    raw = args.report.read_text() if args.report else sys.stdin.read()
    data = json.loads(raw)

    checked, violations = check(data, ignore_packages=set(args.ignore_packages))
    if violations:
        for name, license_str in violations:
            print(
                f"license {license_str!r} not in the FINOS runtime allowlist for package {name}",
                file=sys.stderr,
            )
        return 1

    print(f"All {checked} package(s) have an allowed license.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
