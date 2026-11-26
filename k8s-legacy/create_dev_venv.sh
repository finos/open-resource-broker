#!/usr/bin/env bash

# Hash-locked tool requirements live at the repository root.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQ="${REPO_ROOT}/.github/requirements"
for f in build-tools.txt uv.txt; do
    if [ ! -f "${REQ}/${f}" ]; then
        echo "ERROR: ${REQ}/${f} not found; run this script from a full repository checkout" >&2
        exit 1
    fi
done

install_tools() {
    pip install --require-hashes --no-deps -r "${REQ}/build-tools.txt"
    pip install --require-hashes --no-deps -r "${REQ}/uv.txt"
}

# Check if pip is in the path
if ! command -v pip &> /dev/null
then
    echo "pip could not be found"
    python -m ensurepip --upgrade
fi
install_tools
VENV="${VENV:-venv}"
uv venv "${VENV}" --allow-existing
# shellcheck source=/dev/null
source "${VENV}/bin/activate"
python -m ensurepip --upgrade
install_tools
uv pip install -r pyproject.toml -c constraints.txt
uv pip install -e .

echo "Source the venv with: source ${VENV}/bin/activate"
