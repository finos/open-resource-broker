"""Parity checks between the Windows (.bat) and POSIX (.sh) scheduler scripts.

HostFactory and SLURM each ship a `.bat` sibling for every `.sh` hook script.
These scripts are not executed on this platform, so correctness is verified
by parsing the ORB CLI invocation each script makes and checking it (a)
matches its sibling's invocation and (b) is actually accepted by the real
`orb` argument parser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from orb.cli.args import (
    ParentParsers,
    add_machine_actions,
    add_request_actions,
    add_template_actions,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
HOSTFACTORY_SCRIPTS = (
    REPO_ROOT / "src" / "orb" / "infrastructure" / "scheduler" / "hostfactory" / "scripts"
)
SLURM_SCRIPTS = REPO_ROOT / "src" / "orb" / "infrastructure" / "scheduler" / "slurm" / "scripts"


def _make_action_subparsers():
    """Build the real `machines`/`requests` action parsers in isolation."""
    import argparse

    machines_parser = argparse.ArgumentParser()
    machines_sub = machines_parser.add_subparsers(dest="action")
    pp = ParentParsers()
    add_machine_actions(machines_sub, pp)

    requests_parser = argparse.ArgumentParser()
    requests_sub = requests_parser.add_subparsers(dest="action")
    add_request_actions(requests_sub, pp)

    templates_parser = argparse.ArgumentParser()
    templates_sub = templates_parser.add_subparsers(dest="action")
    add_template_actions(templates_sub, pp)

    return machines_parser, requests_parser, templates_parser


MACHINES_PARSER, REQUESTS_PARSER, TEMPLATES_PARSER = _make_action_subparsers()
RESOURCE_PARSERS = {
    "machines": MACHINES_PARSER,
    "requests": REQUESTS_PARSER,
    "templates": TEMPLATES_PARSER,
}


# ---------------------------------------------------------------------------
# HostFactory scripts: each caller script forwards straight through to
# invoke_provider.{bat,sh} with a fixed "<resource> <action>" prefix, then
# "%*" / "$@" for whatever HostFactory passes at runtime.
# ---------------------------------------------------------------------------

_INVOKE_CALL_RE = re.compile(r'invoke_provider\.(?:bat|sh)"?\s+(.*?)\s+(?:%\*|"\$@")')


def _extract_invoke_provider_subcommand(path: Path) -> list[str]:
    text = path.read_text()
    match = _INVOKE_CALL_RE.search(text)
    assert match, f"could not find an invoke_provider call in {path}"
    return match.group(1).split()


# (script stem, is covered by the CLI parser today)
# templateWizard is intentionally excluded from parser validation: both
# scripts agree on calling "templateWizard", but no such CLI command exists
# yet (tracked separately) — this test only checks the HF scripts that are
# supposed to be live today.
_HOSTFACTORY_SCRIPT_STEMS = [
    "requestMachines",
    "getRequestStatus",
    "getReturnRequests",
    "requestReturnMachines",
    "getAvailableTemplates",
    "templateWizard",
]


@pytest.mark.parametrize("stem", _HOSTFACTORY_SCRIPT_STEMS)
def test_hostfactory_bat_and_sh_invoke_same_subcommand(stem: str) -> None:
    bat_subcommand = _extract_invoke_provider_subcommand(HOSTFACTORY_SCRIPTS / f"{stem}.bat")
    sh_subcommand = _extract_invoke_provider_subcommand(HOSTFACTORY_SCRIPTS / f"{stem}.sh")
    assert bat_subcommand == sh_subcommand


@pytest.mark.parametrize(
    "stem",
    [s for s in _HOSTFACTORY_SCRIPT_STEMS if s != "templateWizard"],
)
def test_hostfactory_subcommand_is_a_real_cli_action(stem: str) -> None:
    """Both scripts' ORB resource/action must be a command the CLI parser knows."""
    subcommand = _extract_invoke_provider_subcommand(HOSTFACTORY_SCRIPTS / f"{stem}.sh")
    resource, action = subcommand[0], subcommand[1]

    assert resource in RESOURCE_PARSERS, f"unknown ORB resource {resource!r} for {stem}"
    parser = RESOURCE_PARSERS[resource]
    namespace = parser.parse_args([action])
    assert namespace.action == action


@pytest.mark.parametrize(
    "stem",
    [s for s in _HOSTFACTORY_SCRIPT_STEMS if s != "templateWizard"],
)
@pytest.mark.parametrize(
    "flag,value,dest",
    [("-f", "input.json", "hf_file"), ("-d", '{"template": {}}', "hf_data")],
)
def test_hostfactory_scripts_accept_hostfactory_input_flags(
    stem: str, flag: str, value: str, dest: str
) -> None:
    """HostFactory appends -f/-d after the script's fixed arguments (`%*`/`$@`).

    Both flags must be accepted by the parser for every action the scripts call.
    """
    subcommand = _extract_invoke_provider_subcommand(HOSTFACTORY_SCRIPTS / f"{stem}.bat")
    resource, action = subcommand[0], subcommand[1]
    namespace = RESOURCE_PARSERS[resource].parse_args([action, *subcommand[2:], flag, value])
    assert getattr(namespace, dest) == value


# ---------------------------------------------------------------------------
# invoke_provider.bat: the shared entry point for every HostFactory script.
# ---------------------------------------------------------------------------

INVOKE_PROVIDER_BAT = HOSTFACTORY_SCRIPTS / "invoke_provider.bat"
_ALL_BATCH_SCRIPTS = sorted(HOSTFACTORY_SCRIPTS.glob("*.bat")) + sorted(SLURM_SCRIPTS.glob("*.bat"))


def _batch_lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def _is_comment(line: str) -> bool:
    return line.strip().lower().startswith(("rem ", "::"))


def test_invoke_provider_bat_targets_existing_entry_point() -> None:
    assert "src\\orb\\run.py" in INVOKE_PROVIDER_BAT.read_text()
    assert (REPO_ROOT / "src" / "orb" / "run.py").is_file()


def test_invoke_provider_bat_forwards_arguments_in_both_modes() -> None:
    forwarding = [
        line for line in _batch_lines(INVOKE_PROVIDER_BAT) if line.rstrip().endswith("%*")
    ]
    assert len(forwarding) == 2, forwarding


def test_invoke_provider_bat_does_not_enable_delayed_expansion() -> None:
    """With delayed expansion on, a `!` inside `%*` is consumed before the CLI sees it."""
    for line in _batch_lines(INVOKE_PROVIDER_BAT):
        if _is_comment(line):
            continue
        assert "enabledelayedexpansion" not in line.lower()
        assert "!" not in line


def _parenthesised_block_lines(path: Path) -> list[str]:
    """Lines that sit inside a multi-line `( ... )` block."""
    inside: list[str] = []
    depth = 0
    for line in _batch_lines(path):
        if _is_comment(line):
            continue
        stripped = line.strip()
        if stripped.startswith(")"):
            depth -= 1
        if depth > 0:
            inside.append(line)
        if stripped.endswith("("):
            depth += 1
    return inside


@pytest.mark.parametrize("path", _ALL_BATCH_SCRIPTS, ids=lambda p: p.name)
def test_bat_blocks_do_not_expand_variables(path: Path) -> None:
    """A `)` in an expanded path (e.g. `C:\\Program Files (x86)`) would end a block early."""
    offenders = [
        line for line in _parenthesised_block_lines(path) if re.search(r"%[A-Za-z_~]", line)
    ]
    assert not offenders, f"{path.name}: variable expansion inside a ( ) block: {offenders}"


# ---------------------------------------------------------------------------
# Line endings: cmd.exe needs CRLF to resolve goto labels reliably.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _ALL_BATCH_SCRIPTS, ids=lambda p: p.name)
def test_bat_files_use_crlf_line_endings(path: Path) -> None:
    data = path.read_bytes()
    assert b"\n" in data
    assert data.count(b"\n") == data.count(b"\r\n"), f"{path.name} contains LF-only line endings"


def test_gitattributes_forces_crlf_for_batch_files() -> None:
    rules = (REPO_ROOT / ".gitattributes").read_text().splitlines()
    assert "*.bat text eol=crlf" in rules
    assert "*.cmd text eol=crlf" in rules


# ---------------------------------------------------------------------------
# resumeProgram.bat: input validation and failure handling.
# ---------------------------------------------------------------------------


def test_resume_program_bat_validates_node_names_like_the_shell_script() -> None:
    bat = (SLURM_SCRIPTS / "resumeProgram.bat").read_text()
    assert "[][a-zA-Z0-9 ,_-]+" in (SLURM_SCRIPTS / "resumeProgram.sh").read_text()

    match = re.search(r'findstr\s+/r\s+/x\s+/c:"([^"]+)"', bat)
    assert match, "resumeProgram.bat must validate node names with findstr"
    # findstr and Python regex share the escaping used for this character class.
    pattern = re.compile(f"^{match.group(1)}$")
    for ok in ("compute-[001-003]", "node1 node2", "a_b,c"):
        assert pattern.match(ok), ok
    for bad in ("node1&calc", 'node"1', "n|m", "a%b", "a^b", "a;b", "a(b)"):
        assert not pattern.match(bad), bad


def test_resume_program_bat_validates_before_running_any_command() -> None:
    lines = _batch_lines(SLURM_SCRIPTS / "resumeProgram.bat")

    def first(needle: str) -> int:
        return next(i for i, line in enumerate(lines) if needle in line)

    assert first("findstr") < first("scontrol show hostnames") < first("orb machines request")


def test_resume_program_bat_checks_command_failures() -> None:
    text = (SLURM_SCRIPTS / "resumeProgram.bat").read_text()
    assert 'if not "%SCONTROL_RC%"=="0"' in text
    assert 'if not "%ORB_RC%"=="0"' in text
    assert "exit /b %ORB_RC%" in text
    # A `for /f ('scontrol ...')` loop would hide scontrol failures.
    assert "('scontrol" not in text


# ---------------------------------------------------------------------------
# SLURM scripts: suspendProgram/resumeProgram call `orb ...` directly rather
# than going through invoke_provider, so extract the invocation from the
# "orb <...>" fragment on its own (stopping at shell redirection/operators).
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r'^"?(?:%[^%]+%|\$\{[^}]+\})"?$')


def _extract_orb_invocation_tokens(path: Path) -> list[str]:
    text = path.read_text()
    match = None
    for line in text.splitlines():
        if line.strip().lower().startswith(("rem ", "::", "#")):
            continue
        match = re.search(r"(?<![\w.-])orb\s+((?:machines|requests|templates)\b.*)", line)
        if match:
            break
    assert match, f"could not find an `orb ...` invocation in {path}"

    tokens: list[str] = []
    for raw in match.group(1).split():
        if raw.startswith((">", "2>", ")", ";")) or raw == "then":
            break
        tokens.append(raw.rstrip(";)"))
    return tokens


_PLACEHOLDER_VALUE = "7"  # numeric so it satisfies int-typed positionals (e.g. machine_count) too


def _substitute_placeholders(tokens: list[str]) -> list[str]:
    """Replace %VAR%/${VAR} shell placeholders with a stand-in value so the
    tokens can be fed into argparse, and drop surrounding quotes."""
    out = []
    for token in tokens:
        if _PLACEHOLDER_RE.match(token):
            out.append(_PLACEHOLDER_VALUE)
        else:
            out.append(token.strip('"').strip("'"))
    return out


def _flag_names(tokens: list[str]) -> set[str]:
    return {t for t in tokens if t.startswith("-")}


@pytest.mark.parametrize("stem", ["suspendProgram", "resumeProgram"])
def test_slurm_bat_and_sh_invoke_same_subcommand(stem: str) -> None:
    bat_tokens = _extract_orb_invocation_tokens(SLURM_SCRIPTS / f"{stem}.bat")
    sh_tokens = _extract_orb_invocation_tokens(SLURM_SCRIPTS / f"{stem}.sh")

    # First two tokens are always "<resource> <action>" (e.g. "machines terminate").
    assert bat_tokens[:2] == sh_tokens[:2]


def test_suspend_program_bat_and_sh_both_pass_nodes_and_force() -> None:
    bat_tokens = _extract_orb_invocation_tokens(SLURM_SCRIPTS / "suspendProgram.bat")
    sh_tokens = _extract_orb_invocation_tokens(SLURM_SCRIPTS / "suspendProgram.sh")

    for tokens, label in [(bat_tokens, "suspendProgram.bat"), (sh_tokens, "suspendProgram.sh")]:
        flags = _flag_names(tokens)
        assert "--nodes" in flags, f"{label} must pass --nodes"
        assert "--force" in flags, f"{label} must pass --force"


def test_resume_program_bat_and_sh_both_pass_template_and_count() -> None:
    for path in (SLURM_SCRIPTS / "resumeProgram.bat", SLURM_SCRIPTS / "resumeProgram.sh"):
        tokens = _extract_orb_invocation_tokens(path)
        # tokens[0:2] == ["machines", "request"]; the template_id and
        # machine_count positionals must both be present before --nodes.
        positional_tokens = []
        for token in tokens[2:]:
            if token.startswith("-"):
                break
            positional_tokens.append(token)
        assert len(positional_tokens) == 2, (
            f"{path.name} must pass both template_id and machine_count "
            f"positionally to `machines request`, got: {tokens}"
        )


@pytest.mark.parametrize(
    "stem,expect_force",
    [("suspendProgram", True), ("resumeProgram", False)],
)
def test_slurm_scripts_parse_against_real_cli_parser(stem: str, expect_force: bool) -> None:
    """Replay each script's `orb ...` invocation through the real machines
    action parser and check it is accepted with the fields the handler needs."""
    for path in (SLURM_SCRIPTS / f"{stem}.bat", SLURM_SCRIPTS / f"{stem}.sh"):
        tokens = _substitute_placeholders(_extract_orb_invocation_tokens(path))
        action, rest = tokens[1], tokens[2:]

        namespace = MACHINES_PARSER.parse_args([action, *rest])

        assert namespace.nodes == _PLACEHOLDER_VALUE
        if expect_force:
            assert namespace.force is True
        else:
            # machines request: template_id/machine_count must both resolve
            # (nargs="?" means argparse alone won't catch a missing value).
            assert namespace.template_id == _PLACEHOLDER_VALUE
            assert namespace.machine_count == int(_PLACEHOLDER_VALUE)
