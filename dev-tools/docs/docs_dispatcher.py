#!/usr/bin/env python3
"""Documentation dispatcher for the Open Resource Broker."""

import os
import subprocess
import sys


def parse_args(args):
    """Parse command line arguments."""
    return {
        "serve": "serve" in args,
        "deploy": "deploy" in args,
        "clean": "clean" in args,
        "list_versions": "list" in args,
        "version": next((arg.split("=")[1] for arg in args if arg.startswith("version=")), None),
        "delete": next((arg.split("=")[1] for arg in args if arg.startswith("delete=")), None),
    }


def handle_clean():
    """Handle clean command."""
    docs_build_dir = os.getenv("DOCS_BUILD_DIR", "docs/site")
    subprocess.run(["rm", "-rf", docs_build_dir], check=True)
    print(f"Cleaned {docs_build_dir}")
    return 0


def handle_list():
    """Handle list versions command."""
    cmd = ["./dev-tools/setup/run_tool.sh", "mike", "list"]
    subprocess.run(cmd, cwd="docs", check=True)
    return 0


def handle_delete(version_to_delete):
    """Handle delete command."""
    if not version_to_delete:
        print("ERROR: delete requires version (e.g., delete=1.0.0)")
        return 1
    cmd = ["./dev-tools/setup/run_tool.sh", "mike", "delete", version_to_delete]
    subprocess.run(cmd, cwd="docs", check=True)
    return 0


def handle_serve():
    """Handle serve command."""
    print("Starting documentation server at http://127.0.0.1:8000")
    print("Press Ctrl+C to stop the server")
    cmd = ["./dev-tools/setup/run_tool.sh", "mike", "serve"]
    subprocess.run(cmd, cwd="docs", check=True)
    return 0


def handle_deploy(version_num):
    """Handle deploy command."""
    if version_num:
        if os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
            subprocess.run(
                ["git", "config", "--global", "user.name", "github-actions[bot]"], check=True
            )
            subprocess.run(
                [
                    "git",
                    "config",
                    "--global",
                    "user.email",
                    "github-actions[bot]@users.noreply.github.com",
                ],
                check=True,
            )
        cmd = [
            "./dev-tools/setup/run_tool.sh",
            "mike",
            "deploy",
            "--push",
            "--update-aliases",
            version_num,
            "latest",
        ]
        subprocess.run(cmd, cwd="docs", check=True)
    elif os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
        subprocess.run(["make", "ci-docs-deploy"], check=True)
    else:
        print("WARNING: This will commit to your local gh-pages branch")
        cmd = ["./dev-tools/setup/run_tool.sh", "mike", "deploy", "--update-aliases", "latest"]
        subprocess.run(cmd, cwd="docs", check=True)
        print("Documentation deployed locally. Use 'git push origin gh-pages' to publish.")
    return 0


def handle_default():
    """Handle default build command."""
    if os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
        subprocess.run(["make", "ci-docs-build"], check=True)
    else:
        print("Building documentation locally with mike...")
        cmd = ["./dev-tools/setup/run_tool.sh", "mike", "deploy", "--update-aliases", "latest"]
        subprocess.run(cmd, cwd="docs", check=True)
        print("Documentation built with mike versioning")
    return 0


def main():
    parsed = parse_args(sys.argv[1:])

    if parsed["clean"]:
        return handle_clean()
    if parsed["list_versions"]:
        return handle_list()
    if parsed["delete"]:
        return handle_delete(parsed["delete"])
    if parsed["serve"]:
        return handle_serve()
    if parsed["deploy"]:
        return handle_deploy(parsed["version"])

    return handle_default()


if __name__ == "__main__":
    sys.exit(main())
