#!/usr/bin/env bash

# Check if pip is in the path
if ! command -v pip &> /dev/null
then
    echo "pip could not be found"
    python -m ensurepip --upgrade
    pip install --upgrade pip
fi
pip install uv
VENV="${VENV:-venv}"
uv venv "${VENV}" --allow-existing
source "${VENV}/bin/activate"
python -m ensurepip --upgrade
pip install --upgrade pip
pip install uv
uv pip install -r pyproject.toml -c constraints.txt
uv pip install -e .

echo "Source the venv with: source ${VENV}/bin/activate"
