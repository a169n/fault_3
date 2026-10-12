#!/usr/bin/env bash
# Delete the local kind cluster created by setup_kind.sh
set -euo pipefail
kind delete cluster --name aitu
