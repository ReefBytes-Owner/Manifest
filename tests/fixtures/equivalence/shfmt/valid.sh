#!/usr/bin/env bash
# Valid fixture: already formatted per `shfmt -i 4 -ci -sr -ln bash -d`
# (4-space indent, switch-case indented, redirect operators spaced).
set -euo pipefail

case "$1" in
    start)
        echo "starting"
        ;;
    *)
        echo "unknown" >&2
        exit 1
        ;;
esac
