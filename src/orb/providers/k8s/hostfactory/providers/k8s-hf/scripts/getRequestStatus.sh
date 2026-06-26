#!/usr/bin/env bash
set -euo pipefail

inJson="${2:-}"

orb --provider k8s requests status "$inJson" 2>> /tmp/orb-k8s.log
exit $?
