#!/usr/bin/env bash

# This is generic script that will create proper venv for the Nix python
# project.
#
# Ideally it should live somewhere central. For now, expectations are for
# projects to copy the script verbatim.

# Heurisitic to cd to the root of the project.
#
# The $out is set to <src>/outputs/out whatever that means.
#

echo "Runnnig devhook: pwd = $(pwd)"

source="$out"
while ! test -d "$source"; do
  source="$(dirname "$source")"
done

toplevel=$(cd "$source" && git rev-parse --show-toplevel)

if [[ "$VENV" ]]
then
  venv="$VENV"
else
  venv="$toplevel"/venv
fi

echo "venv: $venv"

cd "$toplevel"

if test -w flake.nix; then

  # Create a virtual environment and install the project
  # in editable mode.
  python -mvenv "$venv"

  # shellcheck disable=SC1091
  source "$venv"/bin/activate

  # TODO: need better heuristic to find venv site-packages.
  cd "$venv"/lib64/python3.*/site-packages

  IFS=':' read -ra array <<< "$PYTHONPATH"

  for element in "${array[@]}"; do
    echo $element
    find $element -maxdepth 1 -mindepth 1 -name '*.dist-info' | \
    while read -r i; do
      fname="$(cut -d'-' -f1 <<< $(basename "$i"))".pth
      if ! diff -q "$fname" - <<< "$element" > /dev/null 2>&1; then
        echo "$element" > "$fname"
      fi
    done
  done
  unset PYTHONPATH
  cd "$toplevel"

  # There is only one pyproject.toml, but for case where this project is
  # subproject of a larger project, we need to local it dynamically.
  find "$toplevel" -name pyproject.toml | head -n 1 | while read -r f; do
    pyproject="$(dirname "$f")"
    echo "Installing $pyproject"
    "$venv"/bin/pip install -e "$pyproject"
  done

else
  # Assume existing virtual environment.
  # TODO: consider installing into different venv for each user.
  unset PYTHONPATH
  # shellcheck disable=SC1091
  source "$venv"/bin/activate
fi

# Debug output to confirm where local tools are coming from.
export PS1="+ $PS1"
cd "$toplevel"
