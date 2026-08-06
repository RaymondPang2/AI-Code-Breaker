#!/usr/bin/env bash
# Builds the AI Code Breaker runner image.
#
# Usage: ./runner/build.sh   (or run from inside runner/: ./build.sh)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
docker build -t ai-code-breaker-runner:latest -f Dockerfile .
echo "Built ai-code-breaker-runner:latest"
