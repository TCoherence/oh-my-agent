#!/usr/bin/env bash
# weather.sh — Query weather via wttr.in
# Usage: weather.sh <city>

set -euo pipefail

CITY="${1:?Usage: weather.sh <city>}"

# wttr.in with compact format
curl -sf "wttr.in/${CITY}?format=3" 2>/dev/null || {
    echo "Error: Could not fetch weather for '${CITY}'"
    exit 1
}

echo ""
# Detailed 1-day forecast (compact)
curl -sf "wttr.in/${CITY}?1&Q&T" 2>/dev/null || true
