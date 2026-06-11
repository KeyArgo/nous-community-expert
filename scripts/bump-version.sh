#!/bin/bash
# Talio version auto-bump (patch iteration per commit)
# Reads VERSION file (e.g., "0.2.0"), increments patch, writes back.
# Usage: ./scripts/bump-version.sh [major|minor|patch]
#   default: patch (0.2.0 -> 0.2.1 -> 0.2.2 -> ...)
#   explicit: ./scripts/bump-version.sh minor  (0.2.0 -> 0.3.0, requires user permission)
#   explicit: ./scripts/bump-version.sh major  (0.2.0 -> 1.0.0, requires user permission)

set -e
VERSION_FILE="$(dirname "$0")/../VERSION"
CURRENT=$(cat "$VERSION_FILE" | tr -d '[:space:]')

# Parse MAJOR.MINOR.PATCH
MAJOR=$(echo "$CURRENT" | cut -d. -f1)
MINOR=$(echo "$CURRENT" | cut -d. -f2)
PATCH=$(echo "$CURRENT" | cut -d. -f3)

BUMP="${1:-patch}"

case "$BUMP" in
  patch) PATCH=$((PATCH + 1)) ;;
  minor)
    # Require explicit user confirmation
    if [ -z "$CONFIRM_MINOR" ]; then
      echo "ERROR: minor bumps require user permission." >&2
      echo "Run: CONFIRM_MINOR=yes $0 minor" >&2
      exit 1
    fi
    MINOR=$((MINOR + 1))
    PATCH=0
    ;;
  major)
    if [ -z "$CONFIRM_MAJOR" ]; then
      echo "ERROR: major bumps require user permission." >&2
      echo "Run: CONFIRM_MAJOR=yes $0 major" >&2
      exit 1
    fi
    MAJOR=$((MAJOR + 1))
    MINOR=0
    PATCH=0
    ;;
  *) echo "Usage: $0 [patch|minor|major]" >&2; exit 1 ;;
esac

NEW="$MAJOR.$MINOR.$PATCH"
echo "$NEW" > "$VERSION_FILE"
echo "VERSION: $CURRENT -> $NEW"
