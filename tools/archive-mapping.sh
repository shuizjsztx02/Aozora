#!/usr/bin/env bash
# tools/archive-mapping.sh — T-M5-05b
#
# Archive the release mapping.txt so old crash stacktraces remain
# symbolizable after subsequent builds overwrite build/outputs/mapping/.
#
# Usage:
#   ./tools/archive-mapping.sh [<version-name>]
#
# Default version: reads from app/build.gradle.kts `versionName` if
# available, otherwise falls back to "unknown".
#
# Output:
#   build/mapping-archive/mapping-<version>-<unix-timestamp>.txt
#
# The script is a no-op when the source mapping file does not exist
# (non-release builds), exiting 0 so CI pipelines don't break.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SOURCE_DIR="$REPO_ROOT/app/build/outputs/mapping/release"
ARCHIVE_DIR="$REPO_ROOT/build/mapping-archive"

SOURCE_FILE="$SOURCE_DIR/mapping.txt"

if [[ ! -f "$SOURCE_FILE" ]]; then
    echo "[archive-mapping] no mapping.txt at $SOURCE_FILE — skipping (non-release build?)"
    exit 0
fi

# Resolve version name.
if [[ -n "${1:-}" ]]; then
    VERSION="$1"
elif [[ -f "$REPO_ROOT/app/build.gradle.kts" ]]; then
    VERSION="$(grep -E '^\s*versionName\s*=' "$REPO_ROOT/app/build.gradle.kts" \
        | head -n1 \
        | sed -E 's/.*versionName\s*=\s*"([^"]+)".*/\1/' || true)"
    VERSION="${VERSION:-unknown}"
else
    VERSION="unknown"
fi

TIMESTAMP="$(date +%s)"
TARGET_DIR="$ARCHIVE_DIR"
TARGET_FILE="$TARGET_DIR/mapping-${VERSION}-${TIMESTAMP}.txt"

mkdir -p "$TARGET_DIR"
cp "$SOURCE_FILE" "$TARGET_FILE"

# Also write a sidecar with the version + timestamp for quick grep.
cat > "${TARGET_FILE%.txt}.meta" <<EOF
version=$VERSION
timestamp=$TIMESTAMP
source_sha256=$(sha256sum "$SOURCE_FILE" | cut -d' ' -f1)
EOF

echo "[archive-mapping] archived: $TARGET_FILE"
echo "[archive-mapping]          (size: $(wc -c < "$TARGET_FILE") bytes, version: $VERSION)"
