#!/bin/bash
export HF_CALLER_SCRIPT="$(basename "$0")"
"$(dirname "$0")/invoke_provider.sh" templateWizard "$@"
