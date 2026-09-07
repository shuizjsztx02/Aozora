#!/usr/bin/env bash
# T-M5-02c: Scan build artifacts for accidentally-embedded secrets.
#
# Three scanners:
#   1. APK / AAB — unzip and run `strings` looking for key patterns
#   2. Web dist — grep the frontend build output for key patterns
#   3. Server image — placeholder for Trivy image scan (needs docker build)
#
# Usage:
#   tools/scan-secrets.sh [--apk PATH] [--web PATH] [--server-image NAME]
#
# Each scanner exits non-zero if matches are found.
# Intended for CI / pre-release gate.

set -euo pipefail

# Patterns commonly found in leaked secrets (API keys, tokens).
PATTERNS='sk-[a-zA-Z0-9]{20,}|AIza[a-zA-Z0-9_-]{35}|ghp_[a-zA-Z0-9]{36}|AKIA[0-9A-Z]{16}|eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*'

EXIT_CODE=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

scan_strings() {
    local label="$1"
    local file="$2"

    if [[ ! -f "$file" ]]; then
        echo "⏭  $label: file not found ($file), skipping"
        return 0
    fi

    echo "🔍 Scanning $label: $file"
    local matches
    matches=$(strings "$file" | grep -iEo "$PATTERNS" || true)

    if [[ -n "$matches" ]]; then
        echo "❌ FOUND potential secrets in $label:"
        echo "$matches" | head -20
        EXIT_CODE=1
    else
        echo "✅ $label: no secrets detected"
    fi
}

scan_grep_dir() {
    local label="$1"
    local dir="$2"

    if [[ ! -d "$dir" ]]; then
        echo "⏭  $label: directory not found ($dir), skipping"
        return 0
    fi

    echo "🔍 Scanning $label: $dir"
    local matches
    matches=$(grep -rE "$PATTERNS" "$dir" --include='*.js' --include='*.css' --include='*.html' --include='*.json' -l || true)

    if [[ -n "$matches" ]]; then
        echo "❌ FOUND potential secrets in $label:"
        echo "$matches"
        EXIT_CODE=1
    else
        echo "✅ $label: no secrets detected"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

APK_PATH="${1:-app/build/outputs/bundle/release/app-release.aab}"
WEB_PATH="${2:-app/dist}"
SERVER_IMAGE="${3:-}"

# 1. APK / AAB scan
scan_strings "APK/AAB" "$APK_PATH"

# 2. Web dist scan
scan_grep_dir "Web dist" "$WEB_PATH"

# 3. Server Docker image scan (requires Trivy)
if [[ -n "$SERVER_IMAGE" ]]; then
    if command -v trivy &>/dev/null; then
        echo "🔍 Scanning server image: $SERVER_IMAGE"
        if trivy image --severity CRITICAL --exit-code 1 "$SERVER_IMAGE"; then
            echo "✅ Server image: no CRITICAL vulnerabilities"
        else
            echo "❌ Server image: CRITICAL vulnerabilities found"
            EXIT_CODE=1
        fi
    else
        echo "⏭  Server image scan: trivy not installed, skipping"
    fi
else
    echo "⏭  Server image scan: no image name provided (use --server-image)"
fi

exit $EXIT_CODE
