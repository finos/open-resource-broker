#!/bin/bash
HF_CALLER_SCRIPT="$(basename "$0")"
export HF_CALLER_SCRIPT
"$(dirname "$0")/invoke_provider.sh" templateWizard "$@"
