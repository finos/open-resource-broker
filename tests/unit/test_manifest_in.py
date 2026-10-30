"""Source distributions must carry every config file the container build ships."""

from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Documentation only; the sdist does not need it for runtime behaviour.
NOT_PACKAGED = {"config/README.md"}


def _dockerignore_config_includes() -> set[str]:
    files: set[str] = set()
    for line in (REPO_ROOT / ".dockerignore").read_text().splitlines():
        line = line.strip()
        if not line.startswith("!config/") or line.endswith("/"):
            continue
        for match in REPO_ROOT.glob(line[1:]):
            if match.is_file():
                files.add(match.relative_to(REPO_ROOT).as_posix())
    return files - NOT_PACKAGED


def _manifest_covers(relative: str, rules: list[list[str]]) -> bool:
    for rule in rules:
        command, args = rule[0], rule[1:]
        if command == "include" and any(
            fnmatch(relative, arg) and arg.count("/") == relative.count("/") for arg in args
        ):
            return True
        if command == "recursive-include":
            directory, patterns = args[0], args[1:]
            if relative.startswith(directory + "/") and any(
                fnmatch(Path(relative).name, pattern) for pattern in patterns
            ):
                return True
    return False


def test_sdist_manifest_covers_files_the_image_copies_from_config():
    rules = [
        line.split()
        for line in (REPO_ROOT / "MANIFEST.in").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    wanted = _dockerignore_config_includes()
    assert "config/sample_user_data_script.sh" in wanted
    assert any(path.endswith(".yaml") for path in wanted)
    missing = sorted(path for path in wanted if not _manifest_covers(path, rules))
    assert not missing, f"MANIFEST.in does not include: {missing}"
