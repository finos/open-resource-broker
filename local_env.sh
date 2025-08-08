#!/bin/bash

# Get the full path of the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Host Factory Provider Configuration
export HF_PROVIDER_NAME="awscpinst"
export HF_PROVIDER_CONFDIR="${SCRIPT_DIR}/${HF_PROVIDER_NAME}/config"
export HF_PROVIDER_LOGDIR="${SCRIPT_DIR}/${HF_PROVIDER_NAME}/logs"
export HF_PROVIDER_WORKDIR="${SCRIPT_DIR}/${HF_PROVIDER_NAME}/workdir"

# Package Development Configuration
# Set to 'true' or '1' to use local run.py instead of installed package
export USE_LOCAL_DEV="true"

# Package name override (default: open-hostfactory-plugin)
export OHFP_PACKAGE_NAME="open-hostfactory-plugin"

# Command name override (default: ohfp)
export OHFP_COMMAND="ohfp"

echo "Environment variables set:"
echo ""
echo "Host Factory Configuration:"
echo "  HF_PROVIDER_NAME=${HF_PROVIDER_NAME}"
echo "  HF_PROVIDER_CONFDIR=${HF_PROVIDER_CONFDIR}"
echo "  HF_PROVIDER_LOGDIR=${HF_PROVIDER_LOGDIR}"
echo "  HF_PROVIDER_WORKDIR=${HF_PROVIDER_WORKDIR}"
echo ""
echo "Package Development Configuration:"
echo "  USE_LOCAL_DEV=${USE_LOCAL_DEV}"
echo "  OHFP_PACKAGE_NAME=${OHFP_PACKAGE_NAME}"
echo "  OHFP_COMMAND=${OHFP_COMMAND}"
echo ""
echo "Usage Examples:"
echo "  # Use local development version (src/run.py):"
echo "  ./scripts/requestMachines.sh basic-template 2"
echo ""
echo "  # Use installed package version:"
echo "  USE_LOCAL_DEV=false ./scripts/requestMachines.sh basic-template 2"
echo ""
echo "  # Use different command name:"
echo "  OHFP_COMMAND=open-hostfactory-plugin ./scripts/requestMachines.sh basic-template 2"
echo ""
echo "  # Direct execution (development):"
echo "  python src/run.py templates list"
