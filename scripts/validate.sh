#!/usr/bin/env bash
# Run the same validators that the GitHub Actions Validate workflow runs,
# locally via Docker. Lets you catch problems before pushing.
#
# Usage:
#   scripts/validate.sh                # run both validators
#   scripts/validate.sh hassfest       # run only hassfest (no token needed)
#   scripts/validate.sh hacs           # run only HACS (needs GITHUB_TOKEN)
#
# Both validators run inside the same Docker images CI uses, so what
# passes here will pass in CI (modulo upstream image changes between
# pulls — re-pull periodically with ``docker pull
# ghcr.io/home-assistant/hassfest:latest`` and ``docker pull
# ghcr.io/hacs/action:main``).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTEGRATION_PATH="custom_components/multisplit_zone_controller"

want_hassfest=1
want_hacs=1
case "${1:-}" in
  hassfest) want_hacs=0 ;;
  hacs)     want_hassfest=0 ;;
  "")       ;;
  *)
    echo "Usage: $0 [hassfest|hacs]" >&2
    exit 2
    ;;
esac

# ----- hassfest ---------------------------------------------------------------
# Validates HA Core integration shape: manifest schema + key order, services
# schema, translations, dependency graph, IoT class, etc. Required to pass.
#
# We mount *only* custom_components/ so hassfest's auto-discovery sees just
# the published integration. If we mounted the whole repo, hassfest would
# also try to validate ``tests/integration/recording_climate/`` (the test
# fake), holding the test helper to the same rules as a shipped integration
# for no good reason.
if [ "$want_hassfest" = "1" ]; then
  echo "==> Running hassfest (custom_components/ only)..."
  docker run --rm \
    -v "$REPO_ROOT/custom_components":/github/workspace/custom_components:ro \
    ghcr.io/home-assistant/hassfest:latest
fi

# ----- HACS -------------------------------------------------------------------
# Validates HACS-store-specific rules: hacs.json shape, repo structure,
# manifest version field, brand presence (warning only). Needs a GitHub
# token to call the GitHub API. Tries env GITHUB_TOKEN first, then falls
# back to ``gh auth token`` if the GitHub CLI is logged in.
if [ "$want_hacs" = "1" ]; then
  token="${GITHUB_TOKEN:-}"
  if [ -z "$token" ] && command -v gh >/dev/null 2>&1; then
    token="$(gh auth token 2>/dev/null || true)"
  fi
  if [ -z "$token" ]; then
    echo "==> Skipping HACS validation: no GITHUB_TOKEN set and 'gh auth token'"
    echo "    is unavailable. Either:"
    echo "      - export GITHUB_TOKEN=<a personal access token with public_repo scope>"
    echo "      - or run: gh auth login"
    echo "    Hassfest already passed; HACS will run cleanly in CI."
    exit 0
  fi

  echo "==> Running HACS validator..."
  docker run --rm \
    -v "$REPO_ROOT":/github/workspace \
    -e INPUT_CATEGORY=integration \
    -e GITHUB_REPOSITORY=matt-blackstone/ha-zone-climate \
    -e GITHUB_TOKEN="$token" \
    ghcr.io/hacs/action:main
fi

echo "==> All requested validators passed."
