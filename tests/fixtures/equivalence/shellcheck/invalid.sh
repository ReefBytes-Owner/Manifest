#!/usr/bin/env bash
# Invalid fixture: unused variable trips SC2034 at warning severity
# (the exact `--severity=warning` threshold both the registry's direct
# `shellcheck` invocation and the shellcheck-py pre-commit wrapper apply).
set -euo pipefail

unused_var="never read"
printf 'done\n'
