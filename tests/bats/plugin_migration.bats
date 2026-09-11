#!/usr/bin/env bats

load '../test_helper/store_python.bash'

setup() {
  REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
}

@test "legacy inventory renders deterministically" {
  # Correction 12 (C7k step 5b): the script's own dependencies come from
  # the store's project-env interpreter, never `uv run` against this
  # candidate (which would materialize a real .venv here).
  local python_bin
  python_bin="$(store_project_env_python)" || skip "store project-env unavailable (run: manifest provision)"
  run "$python_bin" "$REPO_ROOT/tools/render_capability_inventory.py" --check
  [ "$status" -eq 0 ]
}

@test "migration inventory rejects forbidden shared runtime destinations" {
  run grep -nE 'manifest-core.*bootstrap.*shared-plugin' \
    "$REPO_ROOT/src/manifest_agent/migration.py"
  [ "$status" -eq 0 ]
}
