#!/usr/bin/env bash
# Valid fixture: passes `shellcheck --severity=warning` cleanly.
set -euo pipefail

name="world"
printf 'hello %s\n' "${name}"
