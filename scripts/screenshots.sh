#!/usr/bin/env bash
# Regenerate the README screenshots from the real app.
#
# Runs the same e2e stack the test suite uses, then drives a browser through the
# screens worth showing -- light and dark, because a screenshot that only documents
# one theme misrepresents half the interface.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="docs/assets"
mkdir -p "$OUT"

# Absolute so the output location does not depend on where the runner is invoked from.
export SCREENSHOT_DIR="$(pwd)/${OUT}"
export E2E_SCREENSHOTS=1
exec bash scripts/e2e.sh --grep @screenshots
