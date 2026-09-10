#!/usr/bin/env bash
# Invalid fixture: two-space indent and an unindented case body trip
# `shfmt -i 4 -ci -sr -ln bash -d`.
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
